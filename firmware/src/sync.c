#include "sync.h"

#include <math.h>
#include <stdio.h>

#include "hardware/adc.h"

#include "motion.h"
#include "protocol.h"
#include "toolchange.h"

#define HISTORY_LEN 16
#define SYNC_TRAILING_SOFT_WALL_MS 1200.0f
#define SYNC_TRAILING_HARD_WALL_MS 350.0f
#define SYNC_TRAILING_HARD_PUSH_MM_S 0.25f
#define SYNC_TRAILING_COLLAPSE_DELAY_MS 250u
#define SYNC_TRAILING_COLLAPSE_RAMP_MULT 3
#define SYNC_TRAILING_COLLAPSE_CAP_MS 600u
#define SYNC_MID_TRAILING_TAPER_FRAC 0.5f
#define SYNC_MID_TRAILING_FLOOR_FRAC 0.45f
#define SYNC_RESERVE_CENTER_GUARD_FRAC 0.05f
#define SYNC_HIGH_FLOW_NEG_ASSIST_START_MM_MIN 1000.0f
#define SYNC_HIGH_FLOW_NEG_ASSIST_FULL_MM_MIN 1400.0f
#define SYNC_HIGH_FLOW_NEG_ASSIST_FRAC 0.75f
#define SYNC_RECENT_NEGATIVE_HOLD_MS 900u
#define SYNC_POSITIVE_RELAUNCH_DAMP_NUM 1
#define SYNC_POSITIVE_RELAUNCH_DAMP_DEN 4

bool sync_enabled = false;
bool sync_auto_started = false;
bool sync_tail_assist_active = false;
uint32_t sync_idle_since_ms = 0;
int sync_current_sps = 0;
int g_baseline_target_sps = CONF_BASELINE_SPS;
int g_baseline_sps = CONF_BASELINE_SPS;
float g_baseline_alpha = CONF_BASELINE_ALPHA;
uint32_t sync_fast_brake_until_ms = 0;
static bool sync_trailing_recovery_active = false;
static uint32_t sync_continuous_trailing_since_ms = 0;
static uint32_t sync_post_trailing_boost_until_ms = 0;
static uint32_t sync_recent_negative_until_ms = 0;
static bool sync_positive_relaunch_pending = false;
astatic float g_buf_physical_entry_pos_mm = 0.0f;

buf_tracker_t g_buf = { .state = BUF_MID };

uint32_t sync_last_tick_ms = 0;
uint32_t sync_last_evt_ms = 0;
float extruder_est_sps = 0.0f;
float extruder_est_prev_sps = 0.0f;
uint32_t extruder_est_last_update_ms = 0;
uint32_t last_slope_update_ms = 0;

float g_buf_pos = 0.0f;

bool g_boot_stabilizing = false;
uint32_t g_boot_stabilize_deadline_ms = 0;
lane_t *g_boot_stabilize_lane = NULL;
static bool g_buffer_stabilize_emit_events = false;

typedef enum {
    BUFFER_SERVICE_STABILIZE = 0,
    BUFFER_SERVICE_NEG_SYNC,
} buffer_service_mode_t;

static buffer_service_mode_t g_buffer_service_mode = BUFFER_SERVICE_STABILIZE;
static uint32_t g_idle_trailing_since_ms = 0;

typedef struct {
    buf_state_t zone;
    uint32_t dwell_ms;
} zone_event_t;

static zone_event_t g_history[HISTORY_LEN] = {0};
static int g_hist_idx = 0;
static uint32_t buf_pos_last_ms = 0;
static buf_state_t g_buf_stable_state = BUF_MID;
static buf_state_t g_buf_pending_state = BUF_MID;
static uint32_t g_buf_pending_since_ms = 0;

static void buf_update(buf_state_t new_state, uint32_t now_ms);

static int lane_motion_sps(lane_t *L) {
    if (!L) return 0;
    if (L->current_sps > 0) return L->current_sps;
    if (g_tc_ctx.state == TC_RELOAD_FOLLOW && g_tc_ctx.reload_current_sps > 0)
        return g_tc_ctx.reload_current_sps;
    return sync_current_sps;
}

static lane_t *pick_boot_stabilize_lane(void) {
    lane_t *stab_lane = lane_ptr(active_lane);
    if (stab_lane) return stab_lane;
    if (lane_out_present(&g_lane_l1) && !lane_out_present(&g_lane_l2)) return &g_lane_l1;
    if (lane_out_present(&g_lane_l2) && !lane_out_present(&g_lane_l1)) return &g_lane_l2;
    return &g_lane_l1;
}

static float buf_physical_half_travel_mm(void) {
    float physical_half = (BUF_SIZE_MM > 0) ? ((float)BUF_SIZE_MM * 0.5f) : BUF_HALF_TRAVEL_MM;
    if (physical_half < 1.0f) physical_half = 1.0f;
    if (physical_half < BUF_HALF_TRAVEL_MM) physical_half = BUF_HALF_TRAVEL_MM;
    return physical_half;
}

static float buf_threshold_mm(void) {
    float physical_half = buf_physical_half_travel_mm();
    float threshold = BUF_HALF_TRAVEL_MM;
    if (threshold < 1.0f) threshold = 1.0f;
    if (threshold > physical_half) threshold = physical_half;
    return threshold;
}

static float buf_target_reserve_mm(void) {
    float threshold = buf_threshold_mm();
    float physical_half = buf_physical_half_travel_mm();
    float pct = (float)SYNC_RESERVE_PCT / 100.0f;
    float target = -(threshold * pct);
    float center_guard_mm = threshold * SYNC_RESERVE_CENTER_GUARD_FRAC;

    if (pct > 0.0f) target -= center_guard_mm;

    float min_target = -physical_half + 0.5f;
    if (target < min_target) target = min_target;
    if (target > threshold) target = threshold;
    return target;
}

static float buf_virtual_deadband_mm(void) {
    float deadband = buf_threshold_mm() * 0.15f;
    if (deadband < 0.5f) deadband = 0.5f;
    if (deadband > 2.0f) deadband = 2.0f;
    return deadband;
}

static void buf_anchor_virtual_position(buf_state_t old_state, buf_state_t new_state) {
    if (BUF_SENSOR_TYPE != 0) return;

    float threshold = buf_threshold_mm();
    buf_state_t anchor_state = new_state;
    if (new_state == BUF_MID) anchor_state = old_state;

    if (anchor_state == BUF_ADVANCE) g_buf_pos = threshold;
    else if (anchor_state == BUF_TRAILING) g_buf_pos = -threshold;
    else g_buf_pos = 0.0f;
}

static void buf_virtual_position_tick(lane_t *A, uint32_t elapsed_ms) {
    if (BUF_SENSOR_TYPE != 0 || elapsed_ms == 0) return;

    float threshold = buf_threshold_mm();
    float physical_half = buf_physical_half_travel_mm();
    if (!A) {
        g_buf_pos = clamp_f(g_buf_pos, -physical_half, physical_half);
        return;
    }

    int idx = lane_to_idx(A->lane_id);
    if (idx < 0 || idx >= NUM_LANES) idx = 0;

    bool tracking_motion = sync_enabled || g_tc_ctx.state == TC_RELOAD_APPROACH ||
                           g_tc_ctx.state == TC_RELOAD_FOLLOW ||
                           (A->task == TASK_FEED && A->fault == FAULT_NONE);
    if (tracking_motion) {
        float dt_s = (float)elapsed_ms / 1000.0f;
        float mmu_mm_s = (float)lane_motion_sps(A) * MM_PER_STEP[idx];
        float extruder_mm_s = extruder_est_sps * MM_PER_STEP[idx];
        g_buf_pos += (extruder_mm_s - mmu_mm_s) * dt_s;
    }

    g_buf_pos = clamp_f(g_buf_pos, -physical_half, physical_half);
    if (g_buf.state == BUF_ADVANCE && g_buf_pos < threshold) g_buf_pos = threshold;
    else if (g_buf.state == BUF_TRAILING && g_buf_pos > -threshold) g_buf_pos = -threshold;
    else if (g_buf.state == BUF_MID) g_buf_pos = clamp_f(g_buf_pos, -threshold, threshold);
}

static float lane_motion_mm_s(lane_t *L) {
    if (!L) return 0.0f;
    int idx = lane_to_idx(L->lane_id);
    if (idx < 0 || idx >= NUM_LANES) idx = 0;
    return (float)lane_motion_sps(L) * MM_PER_STEP[idx];
}

static float extruder_motion_mm_s(lane_t *L) {
    if (!L) return 0.0f;
    int idx = lane_to_idx(L->lane_id);
    if (idx < 0 || idx >= NUM_LANES) idx = 0;
    return extruder_est_sps * MM_PER_STEP[idx];
}

float sync_trailing_wall_velocity_mm_s(lane_t *L) {
    if (!L || BUF_SENSOR_TYPE != 0) return 0.0f;
    float toward_trailing = lane_motion_mm_s(L) - extruder_motion_mm_s(L);
    return toward_trailing > 0.0f ? toward_trailing : 0.0f;
}

static float sync_trailing_wall_remaining_mm(void) {
    if (BUF_SENSOR_TYPE != 0) return 0.0f;
    float remaining = g_buf_pos + buf_physical_half_travel_mm();
    if (remaining < 0.0f) remaining = 0.0f;
    return remaining;
}

static int sync_trailing_floor_sps(void) {
    return (SYNC_MIN_SPS > TRAILING_SPS) ? SYNC_MIN_SPS : TRAILING_SPS;
}

float sync_trailing_wall_time_ms(lane_t *L) {
    float toward_trailing = sync_trailing_wall_velocity_mm_s(L);
    if (!L || BUF_SENSOR_TYPE != 0 || toward_trailing < 0.05f) return 1000000000.0f;
    return (sync_trailing_wall_remaining_mm() / toward_trailing) * 1000.0f;
}

static int sync_apply_scaling(int base_sps) {
    if (BUF_SENSOR_TYPE == 1) {
        float frac = clamp_f((g_buf_pos + 1.0f) * 0.5f, 0.0f, 1.0f);
        return (int)(TRAILING_SPS + (float)(base_sps - TRAILING_SPS) * frac);
    }

    int target = base_sps;

    if (g_buf_pos < (buf_target_reserve_mm() - buf_virtual_deadband_mm())) {
        float taper_start_mm = buf_target_reserve_mm() - buf_virtual_deadband_mm();
        float taper_end_mm = -buf_threshold_mm();
        float taper_span_mm = taper_start_mm - taper_end_mm;

        if (target > TRAILING_SPS && taper_span_mm > 0.001f) {
            float overfill_mm = taper_start_mm - g_buf_pos;
            float taper_frac = clamp_f(overfill_mm / taper_span_mm, 0.0f, 1.0f);
            int taper_floor_sps = TRAILING_SPS;
            if (g_buf.state == BUF_MID) {
                taper_frac *= SYNC_MID_TRAILING_TAPER_FRAC;
                int dynamic_mid_floor = (int)(extruder_est_sps * SYNC_MID_TRAILING_FLOOR_FRAC);
                if (dynamic_mid_floor > taper_floor_sps) taper_floor_sps = dynamic_mid_floor;
            }
            float tapered = (float)target - ((float)(target - TRAILING_SPS) * taper_frac);
            if (tapered < (float)taper_floor_sps) tapered = (float)taper_floor_sps;
            target = (int)tapered;
        }
    }

    return target;
}

const char *buf_state_name(buf_state_t s) {
    switch (s) {
        case BUF_MID: return "MID";
        case BUF_ADVANCE: return "ADVANCE";
        case BUF_TRAILING: return "TRAILING";
        case BUF_FAULT: return "FAULT";
        default: return "?";
    }
}

static void history_push(buf_state_t zone, uint32_t dwell_ms) {
    g_history[g_hist_idx].zone = zone;
    g_history[g_hist_idx].dwell_ms = dwell_ms;
    g_hist_idx = (g_hist_idx + 1) % HISTORY_LEN;
}

static bool predict_advance_coming(void) {
    int mid_count = 0;
    int short_count = 0;

    for (int i = 0; i < HISTORY_LEN; i++) {
        if (g_history[i].zone == BUF_MID && g_history[i].dwell_ms > 0) {
            mid_count++;
            if (g_history[i].dwell_ms < (uint32_t)BUF_PREDICT_THR_MS) {
                short_count++;
            }
        }
    }
    return mid_count > 0 && (short_count * 2 >= mid_count);
}

static void buf_analog_update(void) {
    adc_select_input(PIN_BUF_ANALOG - 26);
    uint32_t sum = 0;
    for (int i = 0; i < 4; i++) sum += adc_read();
    float fraction = (float)(sum >> 2) / 4095.0f;

    float delta = fraction - BUF_NEUTRAL;
    float scale = (BUF_RANGE > 0.001f) ? BUF_RANGE : 0.001f;
    float norm = clamp_f(delta / scale, -1.0f, 1.0f);
    if (BUF_INVERT) norm = -norm;

    g_buf_pos = BUF_ANALOG_ALPHA * norm + (1.0f - BUF_ANALOG_ALPHA) * g_buf_pos;
}

buf_state_t buf_state_raw(void) {
    if (BUF_SENSOR_TYPE == 1) {
        if (g_buf_pos > BUF_THR) return BUF_ADVANCE;
        if (g_buf_pos < -BUF_THR) return BUF_TRAILING;
        return BUF_MID;
    }

    bool adv_raw = on_al(&g_buf_adv_din);
    bool trl_raw = on_al(&g_buf_trl_din);

    bool adv = BUF_INVERT ? trl_raw : adv_raw;
    bool trl = BUF_INVERT ? adv_raw : trl_raw;

    if (adv && trl) return BUF_FAULT;
    if (adv) return BUF_ADVANCE;
    if (trl) return BUF_TRAILING;
    return BUF_MID;
}

static buf_state_t buf_read_stable(uint32_t now_ms) {
    buf_state_t raw = buf_state_raw();
    if (raw == g_buf_stable_state) {
        g_buf_pending_since_ms = 0;
        return g_buf_stable_state;
    }

    if (raw != g_buf_pending_state) {
        g_buf_pending_state = raw;
        g_buf_pending_since_ms = now_ms;
        return g_buf_stable_state;
    }

    if ((now_ms - g_buf_pending_since_ms) >= (uint32_t)BUF_HYST_MS) {
        g_buf_stable_state = g_buf_pending_state;
        g_buf_pending_since_ms = 0;
    }
    return g_buf_stable_state;
}

static void buf_force_stable_state(buf_state_t state, uint32_t now_ms) {
    g_buf_stable_state = state;
    g_buf_pending_state = state;
    g_buf_pending_since_ms = 0;

    if (g_buf.state != state) {
        buf_update(state, now_ms);
    }

    g_buf.entered_ms = now_ms;
    if (state == BUF_MID) {
        g_buf_pos = 0.0f;
        g_buf_physical_entry_pos_mm = 0.0f;
        g_buf.arm_vel_mm_s = 0.0f;
        if (!sync_enabled) {
            extruder_est_sps = 0.0f;
            extruder_est_prev_sps = 0.0f;
            extruder_est_last_update_ms = now_ms;
        }
    }
}

static void boot_stabilize_stop(void) {
    if (g_boot_stabilize_lane) {
        motor_stop(&g_boot_stabilize_lane->m);
    }
    g_boot_stabilizing = false;
    g_boot_stabilize_deadline_ms = 0;
    g_boot_stabilize_lane = NULL;
    g_buffer_stabilize_emit_events = false;
    g_buffer_service_mode = BUFFER_SERVICE_STABILIZE;
}

static void boot_stabilize_disarm(void) {
    g_boot_stabilizing = false;
    g_boot_stabilize_deadline_ms = 0;
    g_boot_stabilize_lane = NULL;
    g_buffer_stabilize_emit_events = false;
    g_buffer_service_mode = BUFFER_SERVICE_STABILIZE;
}

static bool buffer_stabilize_controller_idle(void) {
    if (g_tc_ctx.state != TC_IDLE || g_cut.state != CUT_IDLE || sync_enabled) return false;
    if (g_lane_l1.task != TASK_IDLE || g_lane_l2.task != TASK_IDLE) return false;
    return true;
}

static bool buffer_negative_sync_eligible(void) {
    lane_t *active = lane_ptr(active_lane);
    return active && lane_out_present(active);
}

static bool buffer_stabilize_start_internal(uint32_t now_ms, bool emit_events, buffer_service_mode_t mode) {
    if (g_boot_stabilizing) return true;
    if (!buffer_stabilize_controller_idle()) return false;
    if (BUF_SENSOR_TYPE != 0) return false;

    buf_state_t buf_state = buf_state_raw();
    lane_t *stab_lane = NULL;
    bool forward = false;

    if (mode == BUFFER_SERVICE_NEG_SYNC) {
        if (buf_state != BUF_TRAILING || !buffer_negative_sync_eligible()) return true;
        stab_lane = lane_ptr(active_lane);
        forward = false;
    } else {
        if (buf_state != BUF_TRAILING && buf_state != BUF_ADVANCE) return true;
        stab_lane = pick_boot_stabilize_lane();
        forward = (buf_state == BUF_ADVANCE);
    }

    if (!stab_lane || BUF_STAB_SPS <= 0) return false;

    g_boot_stabilizing = true;
    g_boot_stabilize_deadline_ms = now_ms + 10000u;
    g_boot_stabilize_lane = stab_lane;
    g_buffer_stabilize_emit_events = emit_events;
    g_buffer_service_mode = mode;
    g_idle_trailing_since_ms = 0;

    motor_enable(&stab_lane->m, true);
    motor_set_dir(&stab_lane->m, forward);
    motor_set_rate_sps(&stab_lane->m, BUF_STAB_SPS);

    if (g_buffer_stabilize_emit_events) cmd_event("BUF_STAB", "START");
    return true;
}

bool buffer_stabilize_request(uint32_t now_ms) {
    g_idle_trailing_since_ms = 0;
    return buffer_stabilize_start_internal(now_ms, true, BUFFER_SERVICE_STABILIZE);
}

void boot_stabilize_start(uint32_t now_ms) {
    (void)buffer_stabilize_start_internal(now_ms, false, BUFFER_SERVICE_STABILIZE);
}

void buffer_stabilize_tick(uint32_t now_ms) {
    if (!g_boot_stabilizing) {
        if (!buffer_stabilize_controller_idle()) {
            g_idle_trailing_since_ms = 0;
        } else if (buf_state_raw() == BUF_TRAILING && buffer_negative_sync_eligible()) {
            if (g_idle_trailing_since_ms == 0) g_idle_trailing_since_ms = now_ms;
            if (POST_PRINT_STAB_DELAY_MS <= 0 ||
                (now_ms - g_idle_trailing_since_ms) >= (uint32_t)POST_PRINT_STAB_DELAY_MS) {
                (void)buffer_stabilize_start_internal(now_ms, true, BUFFER_SERVICE_NEG_SYNC);
            }
        } else {
            g_idle_trailing_since_ms = 0;
        }
    }

    if (!g_boot_stabilizing) return;

    if (!g_boot_stabilize_lane) {
        boot_stabilize_disarm();
        return;
    }

    if (g_boot_stabilize_lane->task != TASK_IDLE) {
        boot_stabilize_disarm();
        return;
    }

    if (!buffer_stabilize_controller_idle()) {
        boot_stabilize_stop();
        return;
    }

    buf_state_t raw_state = buf_state_raw();
    if (g_buffer_service_mode == BUFFER_SERVICE_NEG_SYNC) {
        if (raw_state == BUF_MID) {
            buf_force_stable_state(BUF_MID, now_ms);
            if (g_buffer_stabilize_emit_events) cmd_event("BUF_STAB", "DONE");
            boot_stabilize_stop();
            return;
        }

        if (raw_state == BUF_ADVANCE) {
            g_buffer_service_mode = BUFFER_SERVICE_STABILIZE;
            g_boot_stabilize_deadline_ms = now_ms + 10000u;
            motor_enable(&g_boot_stabilize_lane->m, true);
            motor_set_dir(&g_boot_stabilize_lane->m, true);
            motor_set_rate_sps(&g_boot_stabilize_lane->m, BUF_STAB_SPS);
            return;
        }
    } else if (raw_state == BUF_MID) {
        buf_force_stable_state(BUF_MID, now_ms);
        if (g_buffer_stabilize_emit_events) cmd_event("BUF_STAB", "DONE");
        boot_stabilize_stop();
        return;
    }

    if ((int32_t)(now_ms - g_boot_stabilize_deadline_ms) >= 0) {
        if (g_buffer_stabilize_emit_events) cmd_event("BUF_STAB", "TIMEOUT");
        boot_stabilize_stop();
    }
}

static void buf_update(buf_state_t new_state, uint32_t now_ms) {
    if (new_state == g_buf.state) return;

    uint32_t prev_dwell = now_ms - g_buf.entered_ms;
    g_buf.dwell_ms = prev_dwell;
    lane_t *A = lane_ptr(active_lane);
    int mmu_now_sps = lane_motion_sps(A);

    float mmu_avg_sps = 0.0f;
    if (g_buf.mmu_sps_dwell_samples > 0) {
        mmu_avg_sps = (float)g_buf.mmu_sps_dwell_sum / (float)g_buf.mmu_sps_dwell_samples;
    } else {
        mmu_avg_sps = (float)(g_buf.mmu_sps_at_entry + mmu_now_sps) / 2.0f;
    }

    float travel_mm = 0.0f;
    buf_state_t old = g_buf.state;
    float threshold = buf_threshold_mm();
    float max_transition_mm = threshold * 2.0f;
    float mid_pos_mm = clamp_f(g_buf_pos, -threshold, threshold);

    g_buf.arm_vel_mm_s = 0.0f;

    if (old == BUF_MID) {
        if (new_state == BUF_ADVANCE) travel_mm = threshold - g_buf_physical_entry_pos_mm;
        else if (new_state == BUF_TRAILING) travel_mm = -threshold - g_buf_physical_entry_pos_mm;
        if (new_state == BUF_ADVANCE) travel_mm = threshold - mid_pos_mm;
        else if (new_state == BUF_TRAILING) travel_mm = -threshold - mid_pos_mm;
    } else if (old == BUF_ADVANCE) {
        if (new_state == BUF_MID) travel_mm = 0.0f;
        else if (new_state == BUF_TRAILING) travel_mm = -max_transition_mm;
    } else if (old == BUF_TRAILING) {
        if (new_state == BUF_MID) travel_mm = 0.0f;
        else if (new_state == BUF_ADVANCE) travel_mm = max_transition_mm;
    }

    travel_mm = clamp_f(travel_mm, -max_transition_mm, max_transition_mm);

    if (new_state == BUF_ADVANCE) {
        g_buf_physical_entry_pos_mm = threshold;
    } else if (new_state == BUF_TRAILING) {
        g_buf_physical_entry_pos_mm = -threshold;
    } else if (new_state == BUF_MID) {
        if (old == BUF_ADVANCE) g_buf_physical_entry_pos_mm = threshold;
        else if (old == BUF_TRAILING) g_buf_physical_entry_pos_mm = -threshold;
    }

    if (fabsf(travel_mm) > 0.001f && prev_dwell > (uint32_t)BUF_HYST_MS) {
        uint32_t effective_dwell = prev_dwell - (uint32_t)(BUF_HYST_MS / 2);
        if (effective_dwell < 5) effective_dwell = 5;
        g_buf.arm_vel_mm_s = travel_mm / ((float)effective_dwell / 1000.0f);

        int idx = g_buf.lane_idx_at_entry;
        if (idx < 0 || idx >= NUM_LANES) idx = 0;
        float mmu_mm_s = mmu_avg_sps * MM_PER_STEP[idx];
        float extruder_mm_s = mmu_mm_s + g_buf.arm_vel_mm_s;
        float est_sps = extruder_mm_s / MM_PER_STEP[idx];
        float max_est_sps = (float)GLOBAL_MAX_SPS;
        if (est_sps < 0.0f) est_sps = 0.0f;
        if (est_sps > max_est_sps) est_sps = max_est_sps;

        const float estimator_norm_mm_s = 30.0f;
        float alpha = clamp_f(fabsf(g_buf.arm_vel_mm_s) / estimator_norm_mm_s, EST_ALPHA_MIN, EST_ALPHA_MAX);
        if (old == BUF_ADVANCE && new_state == BUF_TRAILING) {
            extruder_est_sps = est_sps;
        } else {
            extruder_est_sps = alpha * est_sps + (1.0f - alpha) * extruder_est_sps;
        }
        extruder_est_last_update_ms = now_ms;
    }

    history_push(g_buf.state, prev_dwell);
    g_buf.state = new_state;
    g_buf.entered_ms = now_ms;
    buf_anchor_virtual_position(old, new_state);

    g_buf.lane_idx_at_entry = (active_lane == 2) ? 1 : 0;
    g_buf.mmu_sps_at_entry = mmu_now_sps;
    g_buf.mmu_sps_dwell_sum = 0;
    g_buf.mmu_sps_dwell_samples = 0;
}

static void baseline_update_on_settle(uint32_t mid_dwell_ms) {
    if (mid_dwell_ms > 500) {
        g_baseline_sps = (int)(g_baseline_alpha * (float)sync_current_sps + (1.0f - g_baseline_alpha) * (float)g_baseline_sps);
    }
}

static int baseline_control_floor_sps(void) {
    return (g_baseline_sps > g_baseline_target_sps) ? g_baseline_sps : g_baseline_target_sps;
}

int sync_clamp_max_sps(int requested_sps) {
    return motion_clamp_rate_sps(requested_sps);
}

void sync_disable(bool reset_estimator) {
    sync_enabled = false;
    sync_auto_started = false;
    sync_tail_assist_active = false;
    sync_current_sps = 0;
    sync_idle_since_ms = 0;
    sync_fast_brake_until_ms = 0;
    sync_trailing_recovery_active = false;
    sync_continuous_trailing_since_ms = 0;
    sync_post_trailing_boost_until_ms = 0;
    sync_recent_negative_until_ms = 0;
    sync_positive_relaunch_pending = false;

    if (reset_estimator) {
        extruder_est_sps = 0.0f;
        extruder_est_prev_sps = 0.0f;
        extruder_est_last_update_ms = g_now_ms;
    }
}

static int sync_bootstrap_sps(void) {
    int startup_sps = baseline_control_floor_sps();
    if (startup_sps < BUF_STAB_SPS) startup_sps = BUF_STAB_SPS;
    int max_sps = sync_clamp_max_sps(SYNC_MAX_SPS);
    int res = clamp_i(startup_sps, TRAILING_SPS, max_sps);
    g_baseline_sps = res;
    extruder_est_sps = (float)res;
    extruder_est_prev_sps = (float)res;
    extruder_est_last_update_ms = g_now_ms;
    sync_fast_brake_until_ms = 0;
    return res;
}

static int sync_effective_kp_sps(buf_state_t s) {
    int baseline_ref_sps = baseline_control_floor_sps();
    int baseline_limited_kp = (s == BUF_ADVANCE) ? (baseline_ref_sps * 2) : (baseline_ref_sps / 3);
    if (baseline_limited_kp < TRAILING_SPS) baseline_limited_kp = TRAILING_SPS;
    return (SYNC_KP_SPS < baseline_limited_kp) ? SYNC_KP_SPS : baseline_limited_kp;
}

static void sync_apply_to_active(void) {
    lane_t *A = lane_ptr(active_lane);
    if (!A) {
        sync_current_sps = 0;
        return;
    }
    if (A->task == TASK_MOVE) return;

    bool is_protected_task = (A->task == TASK_UNLOAD || A->task == TASK_AUTOLOAD);

    if (sync_current_sps > 0) {
        if (is_protected_task) {
            A->current_sps = sync_current_sps;
            A->target_sps = sync_current_sps;
            motor_set_rate_sps(&A->m, sync_current_sps);
            motor_enable(&A->m, true);
        } else if (A->task != TASK_FEED && A->fault == FAULT_NONE) {
            lane_start(A, TASK_FEED, sync_current_sps, true, g_now_ms, 0);
        } else {
            A->current_sps = sync_current_sps;
            A->target_sps = sync_current_sps;
            motor_set_rate_sps(&A->m, sync_current_sps);
            motor_enable(&A->m, true);
            motor_set_dir(&A->m, true);
        }
    } else if (A->task == TASK_FEED) {
        lane_stop(A);
    }
}

static void sync_on_transition(buf_state_t prev, buf_state_t now_state, uint32_t now_ms) {
    if (prev == BUF_ADVANCE && now_state == BUF_TRAILING) {
        sync_fast_brake_until_ms = now_ms + 250u;
    }

    if (!sync_tail_assist_active) {
        if (now_state == BUF_TRAILING) {
            sync_trailing_recovery_active = true;
            sync_continuous_trailing_since_ms = 0;
            sync_post_trailing_boost_until_ms = 0;
        } else if (prev == BUF_TRAILING && now_state == BUF_MID) {
            if (sync_trailing_recovery_active) sync_post_trailing_boost_until_ms = now_ms + 300u;
            sync_trailing_recovery_active = false;
            sync_continuous_trailing_since_ms = 0;
        } else if (now_state == BUF_ADVANCE) {
            sync_trailing_recovery_active = false;
            sync_continuous_trailing_since_ms = 0;
            sync_post_trailing_boost_until_ms = 0;
        }
    }

    if (prev != BUF_MID && now_state == BUF_MID && sync_enabled) {
        baseline_update_on_settle(g_buf.dwell_ms);
    }
}

void buf_sensor_tick(uint32_t now_ms) {
    uint32_t elapsed_ms = now_ms - buf_pos_last_ms;
    bool do_pos = elapsed_ms >= (uint32_t)SYNC_TICK_MS;
    if (do_pos) buf_pos_last_ms = now_ms;

    if (BUF_SENSOR_TYPE == 1 && do_pos) buf_analog_update();

    buf_state_t prev = g_buf.state;
    buf_state_t s = buf_read_stable(now_ms);
    if (s != prev) {
        buf_update(s, now_ms);
        sync_on_transition(prev, s, now_ms);
    }

    if (BUF_SENSOR_TYPE == 0 && do_pos) {
        buf_virtual_position_tick(lane_ptr(active_lane), elapsed_ms);
    }
}

void sync_tick(uint32_t now_ms) {
    lane_t *A = lane_ptr(active_lane);
    if (!A || tc_state() != TC_IDLE || g_boot_stabilizing) return;

    buf_state_t s = g_buf.state;
    bool auto_start_allowed = (A->task == TASK_IDLE || A->task == TASK_FEED);

    if (AUTO_MODE && !sync_enabled && auto_start_allowed && s == BUF_ADVANCE) {
        bool tail_assist = !lane_in_present(A) && lane_out_present(A);
        int startup_sps = sync_bootstrap_sps();
        sync_current_sps = startup_sps;
        sync_enabled = true;
        sync_auto_started = true;
        sync_tail_assist_active = tail_assist;
        sync_idle_since_ms = 0;
        cmd_event("SYNC", "AUTO_START");
    }

    if (!sync_enabled) return;

    if (sync_auto_started) {
        if (sync_tail_assist_active) {
            if (s != BUF_TRAILING) {
                sync_idle_since_ms = 0;
            } else {
                if (sync_idle_since_ms == 0) sync_idle_since_ms = now_ms;
                if (SYNC_AUTO_STOP_MS > 0 && (now_ms - sync_idle_since_ms) > (uint32_t)SYNC_AUTO_STOP_MS) {
                    sync_disable(true);
                    extruder_est_last_update_ms = now_ms;
                    sync_apply_to_active();
                    cmd_event("SYNC", "AUTO_STOP");
                    return;
                }
            }
        } else {
            sync_idle_since_ms = 0;
        }
    }

    if ((now_ms - sync_last_tick_ms) < (uint32_t)SYNC_TICK_MS) return;

    sync_last_tick_ms = now_ms;

    if (s == BUF_FAULT) {
        sync_current_sps = 0;
        sync_apply_to_active();
        cmd_event("BS", "FAULT,0");
        return;
    }

    if (A && A->task == TASK_FEED && A->fault == FAULT_NONE && sync_current_sps > 0) {
        g_buf.mmu_sps_dwell_sum += (uint32_t)lane_motion_sps(A);
        g_buf.mmu_sps_dwell_samples++;
    }

    bool buf_near_target = fabsf(g_buf_pos - buf_target_reserve_mm()) < (buf_virtual_deadband_mm() * 2.0f);
    if (s == BUF_MID && (now_ms - g_buf.entered_ms) > 2000u &&
        A->task == TASK_FEED && A->fault == FAULT_NONE &&
        buf_near_target && sync_current_sps > 0) {
        extruder_est_sps += 0.05f * ((float)lane_motion_sps(A) - extruder_est_sps);
    }

    float reserve_target_mm = buf_target_reserve_mm();
    float reserve_deadband_mm = buf_virtual_deadband_mm();
    float reserve_error_mm = g_buf_pos - reserve_target_mm;
    if (reserve_error_mm < -reserve_deadband_mm) {
        sync_recent_negative_until_ms = now_ms + SYNC_RECENT_NEGATIVE_HOLD_MS;
        sync_positive_relaunch_pending = true;
    } else if (sync_positive_relaunch_pending &&
               reserve_error_mm <= reserve_deadband_mm &&
               (int32_t)(now_ms - sync_recent_negative_until_ms) >= 0) {
        sync_recent_negative_until_ms = 0;
        sync_positive_relaunch_pending = false;
    }
    bool damp_positive_relaunch = sync_is_positive_relaunch_damped();
    int kp_window = sync_effective_kp_sps(s);
    int reserve_correction = 0;
    float trailing_wall_ms = 1000000000.0f;
    float trailing_push_mm_s = 0.0f;
    if (BUF_SENSOR_TYPE == 0) {
        float threshold = buf_threshold_mm();
        reserve_correction = (int)((reserve_error_mm / threshold) * (float)kp_window);
        reserve_correction = clamp_i(reserve_correction, -kp_window, kp_window);
        if (damp_positive_relaunch && reserve_correction > 0) {
            reserve_correction = (reserve_correction * SYNC_POSITIVE_RELAUNCH_DAMP_NUM) /
                                 SYNC_POSITIVE_RELAUNCH_DAMP_DEN;
        }
        trailing_push_mm_s = sync_trailing_wall_velocity_mm_s(A);
        trailing_wall_ms = sync_trailing_wall_time_ms(A);
    }

    int zone_bias = 0;
    uint32_t dwell_s = (now_ms - g_buf.entered_ms) / 1000u;
    if (reserve_error_mm > reserve_deadband_mm) {
        zone_bias = ZONE_BIAS_BASE_SPS + (int)(dwell_s * ZONE_BIAS_RAMP_SPS_S);
        if (damp_positive_relaunch) {
            zone_bias = (zone_bias * SYNC_POSITIVE_RELAUNCH_DAMP_NUM) /
                        SYNC_POSITIVE_RELAUNCH_DAMP_DEN;
        }
    } else if (reserve_error_mm < -reserve_deadband_mm) {
        zone_bias = -ZONE_BIAS_BASE_SPS - (int)(dwell_s * ZONE_BIAS_RAMP_SPS_S);

        if (s == BUF_MID) {
            int assist_start_sps = mm_per_min_to_sps(SYNC_HIGH_FLOW_NEG_ASSIST_START_MM_MIN);
            int assist_full_sps = mm_per_min_to_sps(SYNC_HIGH_FLOW_NEG_ASSIST_FULL_MM_MIN);
            float flow_frac = 1.0f;

            if (assist_full_sps > assist_start_sps) {
                flow_frac = ((float)extruder_est_sps - (float)assist_start_sps) /
                            (float)(assist_full_sps - assist_start_sps);
                flow_frac = clamp_f(flow_frac, 0.0f, 1.0f);
            }

            if (flow_frac > 0.0f) {
                int assist_sps = (int)(flow_frac * (float)ZONE_BIAS_BASE_SPS * SYNC_HIGH_FLOW_NEG_ASSIST_FRAC);
                zone_bias += assist_sps;
            }
        }
    }
    zone_bias = clamp_i(zone_bias, -ZONE_BIAS_MAX_SPS, ZONE_BIAS_MAX_SPS);

    if ((now_ms - last_slope_update_ms) >= 500u) {
        extruder_est_prev_sps = extruder_est_sps;
        last_slope_update_ms = now_ms;
    }
    float slope_gain = 0.5f;
    int slope_bias = (int)(slope_gain * (extruder_est_sps - extruder_est_prev_sps));

    bool advance_predicted = !damp_positive_relaunch && predict_advance_coming();
    int overshoot_trim = 0;
    if (s == BUF_TRAILING && SYNC_OVERSHOOT_PCT > 0) {
        int negative_correction = -reserve_correction;
        if (zone_bias < 0) negative_correction += -zone_bias;
        if (negative_correction < 0) negative_correction = 0;
        if (negative_correction > kp_window) negative_correction = kp_window;
        overshoot_trim = (negative_correction * SYNC_OVERSHOOT_PCT) / 100;
    }

    int wall_trim = 0;
    if (BUF_SENSOR_TYPE == 0 && s == BUF_TRAILING && trailing_wall_ms < SYNC_TRAILING_SOFT_WALL_MS) {
        float urgency = (SYNC_TRAILING_SOFT_WALL_MS - trailing_wall_ms) / SYNC_TRAILING_SOFT_WALL_MS;
        urgency = clamp_f(urgency, 0.0f, 1.0f);
        wall_trim = (int)(urgency * (float)kp_window);
    }

    int target_sps = (int)extruder_est_sps + reserve_correction + zone_bias + slope_bias - overshoot_trim - wall_trim;
    if (advance_predicted) target_sps += PRE_RAMP_SPS;

    target_sps = sync_apply_scaling(target_sps);

    if (sync_post_trailing_boost_until_ms != 0) {
        if ((int32_t)(sync_post_trailing_boost_until_ms - now_ms) > 0) target_sps += PRE_RAMP_SPS;
        else sync_post_trailing_boost_until_ms = 0;
    }

    if (sync_trailing_recovery_active) {
        uint32_t trailing_recovery_ms = now_ms - g_buf.entered_ms;
        int trailing_floor_sps = sync_trailing_floor_sps();
        int recovery_cap = (int)extruder_est_sps - kp_window;
        bool trailing_collapse_urgent = trailing_wall_ms < SYNC_TRAILING_SOFT_WALL_MS;
        if (trailing_collapse_urgent && trailing_recovery_ms > SYNC_TRAILING_COLLAPSE_DELAY_MS) {
            uint32_t collapse_ms = trailing_recovery_ms - SYNC_TRAILING_COLLAPSE_DELAY_MS;
            if (collapse_ms > SYNC_TRAILING_COLLAPSE_CAP_MS) collapse_ms = SYNC_TRAILING_COLLAPSE_CAP_MS;
            int extra_trim = (int)(((uint64_t)collapse_ms * (uint64_t)(kp_window + PRE_RAMP_SPS)) /
                                   (uint64_t)SYNC_TRAILING_COLLAPSE_CAP_MS);
            recovery_cap -= extra_trim;
        }
        if (recovery_cap < trailing_floor_sps) recovery_cap = trailing_floor_sps;
        if (target_sps > recovery_cap) target_sps = recovery_cap;
    }

    bool trailing_wall_critical = BUF_SENSOR_TYPE == 0 && s == BUF_TRAILING &&
                                 trailing_push_mm_s > SYNC_TRAILING_HARD_PUSH_MM_S &&
                                 trailing_wall_ms < SYNC_TRAILING_HARD_WALL_MS;
    if (trailing_wall_critical) {
        sync_disable(true);
        extruder_est_last_update_ms = now_ms;
        sync_apply_to_active();
        cmd_event("SYNC", "AUTO_STOP");
        return;
    }

    bool fast_brake_active = sync_fast_brake_until_ms != 0 && (int32_t)(sync_fast_brake_until_ms - now_ms) > 0;
    if (!fast_brake_active && sync_fast_brake_until_ms != 0 && (int32_t)(now_ms - sync_fast_brake_until_ms) >= 0)
        sync_fast_brake_until_ms = 0;

    int max_sps = sync_clamp_max_sps(SYNC_MAX_SPS);
    if (fast_brake_active) target_sps = 0;
    else target_sps = clamp_i(target_sps, SYNC_MIN_SPS, max_sps);

    int ramp_dn_sps = SYNC_RAMP_DN_SPS;
    if (!fast_brake_active && sync_trailing_recovery_active && s == BUF_TRAILING) {
        uint32_t trailing_recovery_ms = now_ms - g_buf.entered_ms;
        if (trailing_wall_ms < SYNC_TRAILING_SOFT_WALL_MS &&
            trailing_recovery_ms > SYNC_TRAILING_COLLAPSE_DELAY_MS) {
            ramp_dn_sps *= SYNC_TRAILING_COLLAPSE_RAMP_MULT;
        }
    }

    if (fast_brake_active) sync_current_sps = 0;
    else if (sync_current_sps > target_sps) sync_current_sps -= ramp_dn_sps;
    else if (sync_current_sps < target_sps) sync_current_sps += SYNC_RAMP_UP_SPS;

    if (!fast_brake_active && s == BUF_TRAILING) {
        int trailing_floor_sps = sync_trailing_floor_sps();
        if (sync_current_sps < trailing_floor_sps) sync_current_sps = trailing_floor_sps;
    }

    sync_current_sps = clamp_i(sync_current_sps, 0, max_sps);

    if (sync_auto_started && !sync_tail_assist_active && s == BUF_TRAILING) {
        if (sync_continuous_trailing_since_ms == 0) sync_continuous_trailing_since_ms = now_ms;
        uint32_t trailing_dwell_ms = now_ms - sync_continuous_trailing_since_ms;
    if (sync_auto_started && !sync_tail_assist_active) {
        if (s == BUF_TRAILING) {
            // Start or maintain the continuous physical dwell timer
            if (sync_continuous_trailing_since_ms == 0) {
                sync_continuous_trailing_since_ms = now_ms;
            }
            
            uint32_t trailing_dwell_ms = now_ms - sync_continuous_trailing_since_ms;

        int effective_floor_sps = sync_trailing_floor_sps() + PRE_RAMP_SPS;
        uint32_t floor_timeout_ms = (uint32_t)SYNC_AUTO_STOP_MS;
            // Define a widened floor threshold to ignore PID hunting/noise
            int effective_floor_sps = sync_trailing_floor_sps() + PRE_RAMP_SPS; 

        if (floor_timeout_ms > 0 && trailing_dwell_ms > floor_timeout_ms) {
            if (sync_current_sps <= effective_floor_sps) {
            // Max time it should take to ramp down from 1600 mm/min to floor, plus safety margin
            uint32_t max_recovery_time_ms = 3000u; 

            // If we've given it enough time to ramp down from MAX speed,
            // AND the speed has collapsed near the floor, the extruder is dead or ultra-slow.
            if (trailing_dwell_ms > max_recovery_time_ms && sync_current_sps <= effective_floor_sps) {
                sync_disable(true);
                extruder_est_last_update_ms = now_ms;
                sync_apply_to_active();
                cmd_event("SYNC", "AUTO_STOP");
                return;
            }
        } else {
            // ONLY reset the timer when the arm physically leaves the trailing switch
            sync_continuous_trailing_since_ms = 0;
        }
    } else {
        sync_continuous_trailing_since_ms = 0;
    }

    sync_apply_to_active();

    if ((now_ms - sync_last_evt_ms) >= 500u) {
        sync_last_evt_ms = now_ms;
        char ev[48];
        snprintf(ev, sizeof(ev), "%s,%.1f,%.2f",
                 buf_state_name(s),
                 (double)sps_to_mm_per_min(sync_current_sps),
                 (double)g_buf_pos);
        cmd_event("BS", ev);
    }
}
float sync_reserve_error_mm(void) {
    return g_buf_pos - buf_target_reserve_mm();
}

bool sync_is_positive_relaunch_damped(void) {
    if (sync_tail_assist_active) return false;
    return sync_positive_relaunch_pending;
}

bool sync_is_advance_predicted(void) {
    return !sync_is_positive_relaunch_damped() && predict_advance_coming();
}
