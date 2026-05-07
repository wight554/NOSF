#include "sync.h"

#include <math.h>
#include <stdio.h>

#include "hardware/adc.h"

#include "motion.h"
#include "protocol.h"
#include "toolchange.h"

#define HISTORY_LEN 16

bool sync_enabled = false;
bool sync_auto_started = false;
bool sync_tail_assist_active = false;
uint32_t sync_idle_since_ms = 0;
int sync_current_sps = 0;
int g_baseline_sps = CONF_BASELINE_SPS;
float g_baseline_alpha = CONF_BASELINE_ALPHA;
uint32_t sync_fast_brake_until_ms = 0;

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

typedef struct {
    buf_state_t zone;
    uint32_t dwell_ms;
} zone_event_t;

static zone_event_t g_history[HISTORY_LEN] = {0};
static int g_hist_idx = 0;
static uint32_t buf_pos_last_ms = 0;

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

static int sync_apply_scaling(int base_sps) {
    if (BUF_SENSOR_TYPE == 1) {
        float frac = clamp_f((g_buf_pos + 1.0f) * 0.5f, 0.0f, 1.0f);
        return (int)(TRAILING_SPS + (float)(base_sps - TRAILING_SPS) * frac);
    }

    int target = base_sps;

    if (g_buf.state == BUF_ADVANCE) {
        if (target < base_sps) target = base_sps;
    } else if (g_buf.state == BUF_TRAILING) {
        if (target > TRAILING_SPS) target = TRAILING_SPS;
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

static buf_state_t buf_read(void) {
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
    static buf_state_t cur = BUF_MID;
    static buf_state_t pending = BUF_MID;
    static uint32_t pend_since = 0;

    buf_state_t raw = buf_read();
    if (raw == cur) {
        pend_since = 0;
        return cur;
    }

    if (raw != pending) {
        pending = raw;
        pend_since = now_ms;
        return cur;
    }

    if ((now_ms - pend_since) >= (uint32_t)BUF_HYST_MS) {
        cur = pending;
        pend_since = 0;
    }
    return cur;
}

static void boot_stabilize_stop(void) {
    if (g_boot_stabilize_lane) {
        motor_stop(&g_boot_stabilize_lane->m);
    }
    g_boot_stabilizing = false;
    g_boot_stabilize_deadline_ms = 0;
    g_boot_stabilize_lane = NULL;
}

static void boot_stabilize_disarm(void) {
    g_boot_stabilizing = false;
    g_boot_stabilize_deadline_ms = 0;
    g_boot_stabilize_lane = NULL;
}

void boot_stabilize_start(uint32_t now_ms) {
    if (BUF_SENSOR_TYPE != 0) return;

    buf_state_t buf_state = buf_read();
    if (buf_state != BUF_TRAILING && buf_state != BUF_ADVANCE) return;

    lane_t *stab_lane = pick_boot_stabilize_lane();
    if (!stab_lane || BUF_STAB_SPS <= 0) return;

    g_boot_stabilizing = true;
    g_boot_stabilize_deadline_ms = now_ms + 10000u;
    g_boot_stabilize_lane = stab_lane;

    motor_enable(&stab_lane->m, true);
    motor_set_dir(&stab_lane->m, buf_state == BUF_ADVANCE);
    motor_set_rate_sps(&stab_lane->m, BUF_STAB_SPS);
}

void boot_stabilize_tick(uint32_t now_ms) {
    if (!g_boot_stabilizing) return;

    if (!g_boot_stabilize_lane) {
        boot_stabilize_disarm();
        return;
    }

    if (g_boot_stabilize_lane->task != TASK_IDLE) {
        boot_stabilize_disarm();
        return;
    }

    if (g_tc_ctx.state != TC_IDLE || g_cut.state != CUT_IDLE || sync_enabled) {
        boot_stabilize_stop();
        return;
    }

    if (buf_read() == BUF_MID || (int32_t)(now_ms - g_boot_stabilize_deadline_ms) >= 0) {
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
    if (old == BUF_MID) {
        if (new_state == BUF_ADVANCE) travel_mm = BUF_HALF_TRAVEL_MM;
        else if (new_state == BUF_TRAILING) travel_mm = -BUF_HALF_TRAVEL_MM;
    } else if (old == BUF_ADVANCE) {
        if (new_state == BUF_MID) travel_mm = -BUF_HALF_TRAVEL_MM;
        else if (new_state == BUF_TRAILING) travel_mm = -(BUF_HALF_TRAVEL_MM * 2.0f);
    } else if (old == BUF_TRAILING) {
        if (new_state == BUF_MID) travel_mm = BUF_HALF_TRAVEL_MM;
        else if (new_state == BUF_ADVANCE) travel_mm = (BUF_HALF_TRAVEL_MM * 2.0f);
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

    if (reset_estimator) {
        extruder_est_sps = 0.0f;
        extruder_est_prev_sps = 0.0f;
        extruder_est_last_update_ms = g_now_ms;
    }
}

static int sync_bootstrap_sps(void) {
    int startup_sps = (g_baseline_sps < BUF_STAB_SPS) ? g_baseline_sps : BUF_STAB_SPS;
    int max_sps = sync_clamp_max_sps(SYNC_MAX_SPS);
    int res = clamp_i(startup_sps, TRAILING_SPS, max_sps);
    extruder_est_sps = (float)res;
    extruder_est_prev_sps = (float)res;
    extruder_est_last_update_ms = g_now_ms;
    sync_fast_brake_until_ms = 0;
    return res;
}

static int sync_effective_kp_sps(buf_state_t s) {
    int baseline_limited_kp = (s == BUF_ADVANCE) ? (g_baseline_sps * 2) : (g_baseline_sps / 3);
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
    if (prev != BUF_MID && now_state == BUF_MID && sync_enabled) {
        baseline_update_on_settle(g_buf.dwell_ms);
    }
}

void buf_sensor_tick(uint32_t now_ms) {
    bool do_pos = (now_ms - buf_pos_last_ms) >= (uint32_t)SYNC_TICK_MS;
    if (do_pos) buf_pos_last_ms = now_ms;

    if (BUF_SENSOR_TYPE == 1 && do_pos) buf_analog_update();

    buf_state_t prev = g_buf.state;
    buf_state_t s = buf_read_stable(now_ms);
    if (s != prev) {
        buf_update(s, now_ms);
        sync_on_transition(prev, s, now_ms);
    }

    if (BUF_SENSOR_TYPE == 0 && do_pos) {
        float target = (g_buf.state == BUF_ADVANCE) ? 1.0f :
                       (g_buf.state == BUF_TRAILING) ? -1.0f : 0.0f;
        g_buf_pos = BUF_ANALOG_ALPHA * target + (1.0f - BUF_ANALOG_ALPHA) * g_buf_pos;
        if (g_buf.state == BUF_MID && g_buf_pos < 0.0f) g_buf_pos = 0.0f;
    }
}

void sync_tick(uint32_t now_ms) {
    lane_t *A = lane_ptr(active_lane);
    if (!A || tc_state() != TC_IDLE) return;

    buf_state_t s = g_buf.state;

    if (AUTO_MODE && !sync_enabled && s == BUF_ADVANCE) {
        bool tail_assist = !lane_in_present(A) && lane_out_present(A);
        int startup_sps = sync_bootstrap_sps();
        g_baseline_sps = startup_sps;
        sync_current_sps = startup_sps;
        sync_enabled = true;
        sync_auto_started = true;
        sync_tail_assist_active = tail_assist;
        sync_idle_since_ms = 0;
        cmd_event("SYNC", "AUTO_START");
    }

    if (!sync_enabled) return;

    if (sync_auto_started) {
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

    if (s == BUF_MID && (now_ms - g_buf.entered_ms) > 2000u && A->task == TASK_FEED && A->fault == FAULT_NONE) {
        extruder_est_sps += 0.05f * ((float)lane_motion_sps(A) - extruder_est_sps);
    }

    int zone_bias = 0;
    uint32_t dwell_s = (now_ms - g_buf.entered_ms) / 1000u;
    if (s == BUF_ADVANCE) {
        zone_bias = ZONE_BIAS_BASE_SPS + (int)(dwell_s * ZONE_BIAS_RAMP_SPS_S);
    } else if (s == BUF_TRAILING) {
        zone_bias = -ZONE_BIAS_BASE_SPS - (int)(dwell_s * ZONE_BIAS_RAMP_SPS_S);
    }
    zone_bias = clamp_i(zone_bias, -ZONE_BIAS_MAX_SPS, ZONE_BIAS_MAX_SPS);

    if ((now_ms - last_slope_update_ms) >= 500u) {
        extruder_est_prev_sps = extruder_est_sps;
        last_slope_update_ms = now_ms;
    }
    float slope_gain = 0.5f;
    int slope_bias = (int)(slope_gain * (extruder_est_sps - extruder_est_prev_sps));

    bool advance_predicted = predict_advance_coming();
    int advance_push = 0;
    if (s == BUF_ADVANCE && SYNC_OVERSHOOT_PCT > 0) {
        int kp_window = sync_effective_kp_sps(s);
        int positive_correction = zone_bias + slope_bias;
        if (advance_predicted) positive_correction += PRE_RAMP_SPS;
        if (positive_correction < 0) positive_correction = 0;
        if (positive_correction > kp_window) positive_correction = kp_window;
        advance_push = (positive_correction * SYNC_OVERSHOOT_PCT) / 100;
    }

    int target_sps = (int)extruder_est_sps + zone_bias + slope_bias + advance_push;
    if (advance_predicted) target_sps += PRE_RAMP_SPS;

    target_sps = sync_apply_scaling(target_sps);

    bool fast_brake_active = sync_fast_brake_until_ms != 0 && (int32_t)(sync_fast_brake_until_ms - now_ms) > 0;
    if (!fast_brake_active && sync_fast_brake_until_ms != 0 && (int32_t)(now_ms - sync_fast_brake_until_ms) >= 0)
        sync_fast_brake_until_ms = 0;

    int max_sps = sync_clamp_max_sps(SYNC_MAX_SPS);
    if (fast_brake_active) target_sps = 0;
    else target_sps = clamp_i(target_sps, SYNC_MIN_SPS, max_sps);

    if (fast_brake_active) sync_current_sps = 0;
    else if (sync_current_sps > target_sps) sync_current_sps -= SYNC_RAMP_DN_SPS;
    else if (sync_current_sps < target_sps) sync_current_sps += SYNC_RAMP_UP_SPS;

    if (!fast_brake_active && s == BUF_TRAILING && sync_current_sps < TRAILING_SPS)
        sync_current_sps = TRAILING_SPS;

    sync_current_sps = clamp_i(sync_current_sps, 0, max_sps);

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