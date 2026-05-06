#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pico/bootrom.h"
#include "pico/flash.h"
#include "pico/stdlib.h"
#include "config.h"

#include "hardware/clocks.h"
#include "hardware/flash.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/adc.h"
#include "hardware/pwm.h"
#include "hardware/sync.h"

#include "neopixel.h"
#include "tmc2209.h"



// ===================== Tunables =====================
static int FEED_SPS = CONF_FEED_SPS;
static int REV_SPS = CONF_REV_SPS;
static int AUTO_SPS = CONF_AUTO_SPS;

static int MOTION_STARTUP_MS = CONF_MOTION_STARTUP_MS;

static int RUNOUT_COOLDOWN_MS = CONF_RUNOUT_COOLDOWN_MS;
static int RELOAD_MODE = CONF_RELOAD_MODE;
static int RELOAD_Y_TIMEOUT_MS = CONF_RELOAD_Y_TIMEOUT_MS;
static int JOIN_SPS = CONF_JOIN_SPS;
static int PRESS_SPS = CONF_PRESS_SPS;
static int TRAILING_SPS = CONF_TRAILING_SPS;
static int SG_DERIV[NUM_LANES] = {CONF_L1_SG_DERIV, CONF_L2_SG_DERIV};
static float SG_TARGET[NUM_LANES] = {CONF_L1_SG_TARGET, CONF_L2_SG_TARGET};
static int FOLLOW_TIMEOUT_MS[NUM_LANES] = {CONF_L1_FOLLOW_TIMEOUT_MS, CONF_L2_FOLLOW_TIMEOUT_MS};

static int RAMP_STEP_SPS = CONF_RAMP_STEP_SPS;
static int RAMP_TICK_MS = CONF_RAMP_TICK_MS;

static int TMC_RUN_CURRENT_MA[NUM_LANES] = {CONF_L1_RUN_CURRENT_MA, CONF_L2_RUN_CURRENT_MA};
static int TMC_HOLD_CURRENT_MA[NUM_LANES] = {CONF_L1_HOLD_CURRENT_MA, CONF_L2_HOLD_CURRENT_MA};
static int TMC_MICROSTEPS[NUM_LANES] = {CONF_L1_MICROSTEPS, CONF_L2_MICROSTEPS};
static bool TMC_SPREADCYCLE[NUM_LANES] = {CONF_L1_SPREADCYCLE, CONF_L2_SPREADCYCLE};
static int TMC_SGTHRS[NUM_LANES] = {CONF_L1_SGTHRS, CONF_L2_SGTHRS};
static int TMC_TCOOLTHRS[NUM_LANES] = {CONF_L1_TCOOLTHRS, CONF_L2_TCOOLTHRS};
static float TMC_ROTATION_DISTANCE[NUM_LANES] = {CONF_L1_ROTATION_DISTANCE, CONF_L2_ROTATION_DISTANCE};
static float TMC_GEAR_RATIO[NUM_LANES] = {CONF_L1_GEAR_RATIO, CONF_L2_GEAR_RATIO};
static int TMC_FULL_STEPS[NUM_LANES] = {CONF_L1_FULL_STEPS, CONF_L2_FULL_STEPS};
static int TMC_TBL[NUM_LANES] = {CONF_L1_TBL, CONF_L2_TBL};
static int TMC_TOFF[NUM_LANES] = {CONF_L1_TOFF, CONF_L2_TOFF};
static int TMC_HSTRT[NUM_LANES] = {CONF_L1_HSTRT, CONF_L2_HSTRT};
static int TMC_HEND[NUM_LANES] = {CONF_L1_HEND, CONF_L2_HEND};
static bool TMC_INTERPOLATE[NUM_LANES] = {CONF_L1_INTPOL, CONF_L2_INTPOL};
static int SG_CURRENT_MA[NUM_LANES] = {CONF_L1_SG_CURRENT_MA, CONF_L2_SG_CURRENT_MA};

static float g_sg_load = 0.0f;   // MA-filtered SG_RESULT; updated only during RELOAD states
static int STALL_RECOVERY_MS = CONF_STALL_RECOVERY_MS;

static int BUF_SENSOR_TYPE = CONF_BUF_SENSOR_TYPE;
static float BUF_NEUTRAL = CONF_BUF_NEUTRAL;
static float BUF_RANGE = CONF_BUF_RANGE;
static float BUF_THR = CONF_BUF_THR;
static float BUF_ANALOG_ALPHA = CONF_BUF_ANALOG_ALPHA;
static int SYNC_KP_SPS = CONF_SYNC_KP_SPS;
static int TS_BUF_FALLBACK_MS = CONF_TS_BUF_FALLBACK_MS;
static bool SYNC_SG_INTERP = CONF_SYNC_SG_INTERP;
static bool RELOAD_SG_INTERP = CONF_RELOAD_SG_INTERP;

static int SERVO_OPEN_US = CONF_SERVO_OPEN_US;
static int SERVO_CLOSE_US = CONF_SERVO_CLOSE_US;
static int SERVO_BLOCK_US = CONF_SERVO_BLOCK_US;
static int SERVO_SETTLE_MS = CONF_SERVO_SETTLE_MS;
static int CUT_FEED_MM = CONF_CUT_FEED_MM;
static int CUT_LENGTH_MM = CONF_CUT_LENGTH_MM;
static int CUT_AMOUNT = CONF_CUT_AMOUNT;
static int CUT_TIMEOUT_SETTLE_MS = CONF_CUT_SETTLE_MS;
static int CUT_TIMEOUT_FEED_MS = CONF_CUT_FEED_MS;

static int TC_TIMEOUT_CUT_MS = CONF_TC_TIMEOUT_CUT_MS;
static int LOAD_MAX_MM = CONF_LOAD_MAX_MM;
static int UNLOAD_MAX_MM = CONF_UNLOAD_MAX_MM;
static int TC_TIMEOUT_TH_MS = CONF_TC_TIMEOUT_TH_MS;
static int TC_TIMEOUT_Y_MS = CONF_TC_TIMEOUT_Y_MS;

static int SYNC_MAX_SPS = CONF_SYNC_MAX_SPS;
static int SYNC_MIN_SPS = CONF_SYNC_MIN_SPS;
static int SYNC_RAMP_UP_SPS = CONF_SYNC_RAMP_UP_SPS;
static int SYNC_RAMP_DN_SPS = CONF_SYNC_RAMP_DN_SPS;
static int SYNC_TICK_MS = CONF_SYNC_TICK_MS;
static int PRE_RAMP_SPS = CONF_PRE_RAMP_SPS;
static int BUF_HYST_MS = CONF_BUF_HYST_MS;
static int BUF_PREDICT_THR_MS = CONF_BUF_PREDICT_THR_MS;
static float BUF_HALF_TRAVEL_MM = CONF_BUF_HALF_TRAVEL_MM;
static int SYNC_AUTO_STOP_MS = CONF_SYNC_AUTO_STOP_MS;
static int AUTOLOAD_MAX_MM = CONF_AUTOLOAD_MAX_MM;
static bool BUF_INVERT = false;
static int AUTO_MODE = 1; // 1=Automated flow, 0=Host-controlled flow
static bool AUTO_PRELOAD = true;
static int AUTOLOAD_RETRACT_MM = 10;
static bool ENABLE_CUTTER = false;

static int DIST_IN_OUT = CONF_DIST_IN_OUT;
static int DIST_OUT_Y  = CONF_DIST_OUT_Y;
static int DIST_Y_BUF  = CONF_DIST_Y_BUF;
static int BUF_BODY_LEN = CONF_BUF_BODY_LEN;
static int BUF_SIZE_MM = CONF_BUF_SIZE_MM;

// Derived Physical Path Constants
#define Y_TO_BUF_NEUTRAL      ((float)DIST_Y_BUF + (float)BUF_SIZE_MM / 2.0f)

static float MM_PER_STEP[NUM_LANES] = {CONF_L1_MM_PER_STEP, CONF_L2_MM_PER_STEP}; 

static inline int mm_per_min_to_sps_idx(float mm_per_min, int idx) {
    return (int)(mm_per_min / 60.0f / MM_PER_STEP[idx] + 0.5f);
}
static inline int mm_per_min_to_sps(float mm_per_min) {
    return mm_per_min_to_sps_idx(mm_per_min, 0);
}
static inline float sps_to_mm_per_min_idx(int sps, int idx) {
    return (float)sps * MM_PER_STEP[idx] * 60.0f + 0.05f; // Small offset for display rounding
}
static inline float sps_to_mm_per_min(int sps) {
    return sps_to_mm_per_min_idx(sps, 0);
}

static uint32_t g_shadow_ihold_irun[NUM_LANES] = {0, 0};
static bool g_shadow_ihold_irun_valid[NUM_LANES] = {false, false};
static bool g_shadow_vsense[NUM_LANES] = {true, true};

// ===================== Helpers =====================
static inline int clamp_i(int v, int lo, int hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static inline float clamp_f(float v, float lo, float hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static int lane_to_idx(int ln) {
    return (ln == 1) ? 0 : 1;
}

static int cs_to_ma(uint8_t cs, bool vsense) {
    const float reff = CONF_RSENSE_OHM + 0.020f;
    const float vref = vsense ? 0.18f : 0.32f;
    const float sqrt2 = 1.41421356f;
    float irms = ((float)cs + 1.0f) * vref / (32.0f * reff * sqrt2);
    int ma = (int)(irms * 1000.0f + 0.5f);
    return clamp_i(ma, 0, 2000);
}

static uint8_t ma_to_cs(int ma, bool vsense) {
    if (ma <= 0) return 0;
    const float reff = CONF_RSENSE_OHM + 0.020f;
    const float vref = vsense ? 0.18f : 0.32f;
    const float sqrt2 = 1.41421356f;
    float irms = (float)ma / 1000.0f;
    int cs = (int)(32.0f * irms * reff * sqrt2 / vref - 1.0f + 0.5f);
    return (uint8_t)clamp_i(cs, 0, 31);
}

static uint32_t build_ihold_irun_reg(int run_ma, int hold_ma, bool vsense) {
    uint8_t irun = ma_to_cs(run_ma, vsense);
    uint8_t ihold = ma_to_cs(hold_ma, vsense);
    return ((uint32_t)ihold) | ((uint32_t)irun << 8) | (8u << 16);
}

static void sync_currents_from_ihold_irun(int ln, uint32_t reg) {
    int idx = lane_to_idx(ln);
    uint8_t ihold = (uint8_t)(reg & 0x1Fu);
    uint8_t irun = (uint8_t)((reg >> 8) & 0x1Fu);
    bool vsense = g_shadow_vsense[idx];
    TMC_RUN_CURRENT_MA[idx] = cs_to_ma(irun, vsense);
    TMC_HOLD_CURRENT_MA[idx] = cs_to_ma(ihold, vsense);
}

// ===================== Debounced digital input =====================
typedef struct {
    uint pin;
    bool stable;
    bool last_raw;
    absolute_time_t last_edge;
} din_t;

static inline void din_init(din_t *d, uint pin) {
    d->pin = pin;
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_IN);
    gpio_pull_up(pin);

    bool raw = gpio_get(pin);
    d->stable = raw;
    d->last_raw = raw;
    d->last_edge = get_absolute_time();
}

static inline void din_update(din_t *d) {
    absolute_time_t now = get_absolute_time();
    bool raw = gpio_get(d->pin);

    if (raw != d->last_raw) {
        d->last_raw = raw;
        d->last_edge = now;
    }

    if (raw != d->stable) {
        if (absolute_time_diff_us(d->last_edge, now) >= 10000) {
            d->stable = raw;
        }
    }
}

static inline bool on_al(const din_t *d) {
    return d->stable != 0;
}

// ===================== Stepper PWM =====================
typedef struct {
    uint en, dir, step;
    bool dir_invert;
    uint slice;
    uint chan;
} motor_t;

static void motor_init(motor_t *m, uint en, uint dir, uint step, bool dir_invert) {
    m->en = en;
    m->dir = dir;
    m->step = step;
    m->dir_invert = dir_invert;

    gpio_init(m->en);
    gpio_set_dir(m->en, GPIO_OUT);

    gpio_init(m->dir);
    gpio_set_dir(m->dir, GPIO_OUT);

    if (EN_ACTIVE_LOW) gpio_put(m->en, 1);
    else gpio_put(m->en, 0);

    gpio_put(m->dir, 0);

    gpio_set_function(m->step, GPIO_FUNC_PWM);
    m->slice = pwm_gpio_to_slice_num(m->step);
    m->chan = pwm_gpio_to_channel(m->step);

    pwm_config cfg = pwm_get_default_config();
    pwm_init(m->slice, &cfg, false);
    pwm_set_enabled(m->slice, false);
}

static inline void motor_enable(motor_t *m, bool on) {
    if (EN_ACTIVE_LOW) gpio_put(m->en, on ? 0 : 1);
    else gpio_put(m->en, on ? 1 : 0);
}

static inline void motor_set_dir(motor_t *m, bool forward) {
    bool d = forward ^ m->dir_invert;
    gpio_put(m->dir, d ? 1 : 0);
}

static void motor_set_rate_sps(motor_t *m, int sps) {
    if (sps <= 0) {
        pwm_set_enabled(m->slice, false);
        return;
    }

    uint32_t sys = clock_get_hz(clk_sys);
    float target = (float)sps;

    float div = (float)sys / (target * 65535.0f);
    if (div < 1.0f) div = 1.0f;
    if (div > 255.0f) div = 255.0f;

    uint32_t wrap = (uint32_t)((float)sys / (div * target) - 1.0f);
    if (wrap < 10) wrap = 10;
    if (wrap > 65535) wrap = 65535;

    pwm_set_clkdiv(m->slice, div);
    pwm_set_wrap(m->slice, wrap);
    pwm_set_chan_level(m->slice, m->chan, (uint16_t)(wrap / 2));
    pwm_set_enabled(m->slice, true);
}

static inline void motor_stop(motor_t *m) {
    pwm_set_enabled(m->slice, false);
    motor_enable(m, false);
}

// ===================== Core types =====================
typedef enum {
    TASK_IDLE = 0,
    TASK_AUTOLOAD,
    TASK_FEED,
    TASK_UNLOAD,      // extruder unload: reverse until OUT clears
    TASK_UNLOAD_MMU,  // MMU unload: reverse until IN clears
    TASK_LOAD_FULL,   // full load: forward until toolhead sensor (TS:1), then stop
    TASK_MOVE         // timed exact-distance move; stops at autoload_deadline_ms
} task_t;

typedef enum {
    FAULT_NONE = 0,
    FAULT_STALL,
    FAULT_TIMEOUT,
    FAULT_SENSOR,
    FAULT_BUF,
    FAULT_CUT
} fault_t;

typedef struct lane_s {
    din_t in_sw;
    din_t out_sw;
    motor_t m;
    task_t task;
    uint32_t motion_started_ms;
    uint32_t task_started_ms;
    float task_limit_mm;
    uint32_t retract_deadline_ms;

    int target_sps;
    int current_sps;
    uint32_t ramp_last_tick_ms;

    tmc_t *tmc;
    uint diag_pin;
    bool stall_armed;
    bool stall_recovery;              // true = first stall fired during sync; next stall = hard stop
    uint32_t stall_recovery_deadline_ms;
    bool unload_sensor_latch;
    fault_t fault;
    int lane_id;
    uint32_t runout_block_until_ms;
    uint32_t buf_advance_since_ms;  // for TS:1 buffer fallback
    uint32_t reload_tail_ms;           // RELOAD: timestamp when IN cleared; 0 = not tracking
    float task_dist_mm;                // distance traveled in current task
    float dist_at_out_mm;              // displacement when OUT sensor was first hit
    uint32_t last_dist_tick_ms;        // for distance integration
} lane_t;

typedef enum {
    CUT_IDLE,
    CUT_OPENING,
    CUT_OPEN_WAIT,
    CUT_FEEDING,
    CUT_FEED_WAIT,
    CUT_CLOSING,
    CUT_CLOSE_WAIT,
    CUT_REOPENING,
    CUT_REOPEN_WAIT,
    CUT_REPEAT_CHECK,
    CUT_DONE
} cutter_state_t;

typedef struct {
    cutter_state_t state;
    lane_t *lane;
    uint32_t phase_start_ms;
    uint32_t feed_initial_ms;
    uint32_t feed_repeat_ms;
    uint32_t feed_active_ms;
    int repeats_done;
} cutter_ctx_t;

typedef enum {
    TC_IDLE,
    TC_UNLOAD_CUT,
    TC_UNLOAD_WAIT_CUT,
    TC_UNLOAD_REVERSE,
    TC_UNLOAD_WAIT_OUT,
    TC_UNLOAD_WAIT_Y,
    TC_UNLOAD_WAIT_TH,
    TC_UNLOAD_DONE,
    TC_SWAP,
    TC_LOAD_START,
    TC_LOAD_WAIT_OUT,
    TC_LOAD_WAIT_TH,
    TC_LOAD_DONE,
    TC_RELOAD_WAIT_Y,    // RELOAD: old tail exited OUT, waiting for Y-splitter to clear
    TC_RELOAD_APPROACH,  // RELOAD State 1: fast approach — detect contact via SG derivative
    TC_RELOAD_FOLLOW,    // RELOAD State 2: follow sync — SG-interpolated speed, exit on ADVANCE
    TC_ERROR
} tc_state_t;

typedef struct {
    tc_state_t state;
    int target_lane;
    int from_lane;
    uint32_t phase_start_ms;
    uint32_t reload_tick_ms;                      // SYNC_TICK_MS rate limiter for RELOAD ticks
    // SG moving-average ring buffer (States 1 & 2)
    float    sg_ma_buf[CONF_SG_MA_LEN];    // raw SG readings
    uint8_t  sg_ma_idx;                         // next write position
    uint8_t  sg_ma_fill;                        // samples loaded so far (cap at MA_LEN)
    float    sg_ma_prev;                        // MA from previous tick (for derivative)
    // State 2 follow sync
    int      reload_current_sps;                   // current ramped speed in TC_RELOAD_FOLLOW
    int      reload_stall_count;                   // consecutive stalls in State 2
    uint32_t last_reload_stall_ms;                 // for stall frequency tracking
    uint32_t last_trailing_ms;                  // for trailing timeout in follow
    uint32_t runout_ms;                         // for dry spin protection
} tc_ctx_t;

typedef enum {
    BUF_MID,
    BUF_ADVANCE,
    BUF_TRAILING,
    BUF_FAULT
} buf_state_t;

typedef struct {
    buf_state_t state;
    uint32_t entered_ms;
    uint32_t dwell_ms;
    float arm_vel_mm_s;
} buf_tracker_t;

#define HISTORY_LEN 16
typedef struct {
    buf_state_t zone;
    uint32_t dwell_ms;
} zone_event_t;

static const char *buf_state_name(buf_state_t s);

static void tc_enter_error(const char *reason);
static void lane_stop(lane_t *L);

// ===================== Globals =====================
static lane_t g_lane_l1;
static lane_t g_lane_l2;
static din_t g_y_split;

static din_t g_buf_adv_din;
static din_t g_buf_trl_din;

static tmc_t g_tmc_l1;
static tmc_t g_tmc_l2;

static cutter_ctx_t g_cut = {0};
static tc_ctx_t g_tc_ctx = { .state = TC_IDLE };

static volatile uint32_t g_now_ms = 0;
static int active_lane = 0;
static bool toolhead_has_filament = false;

static bool sync_enabled = false;
static bool sync_auto_started = false;
static uint32_t sync_idle_since_ms = 0;
static int sync_current_sps = 0;
static int g_baseline_sps = CONF_BASELINE_SPS;
static float g_baseline_alpha = CONF_BASELINE_ALPHA;

static buf_tracker_t g_buf = { .state = BUF_MID };
static zone_event_t g_history[HISTORY_LEN] = {0};
static int g_hist_idx = 0;

static uint32_t sync_last_tick_ms = 0;
static uint32_t sync_last_sg_ms = 0;
static uint32_t sync_last_evt_ms = 0;

static volatile bool stall_pending_l1 = false;
static volatile bool stall_pending_l2 = false;

static float g_buf_pos = 0.0f;  // normalised buffer position [-1, +1]; analog = ADC EMA, endstop = zone EMA

static bool prev_lane1_in_present = false;
static bool prev_lane2_in_present = false;

// ===================== Forward declarations =====================
static void cmd_event(const char *type, const char *data);
static inline tc_state_t tc_state(void);
static void reload_trigger(int runout_lane, uint32_t now_ms);

// Auto-enable/disable sync whenever toolhead filament state changes.
static void set_toolhead_filament(bool present) {
    toolhead_has_filament = present;
    sync_enabled = present;
    if (!present) sync_current_sps = 0;
}

// ===================== Lane helpers =====================
static inline bool lane_in_present(lane_t *L) { return on_al(&L->in_sw); }
static inline bool lane_out_present(lane_t *L) { return on_al(&L->out_sw); }

static int detect_active_lane_from_out(void) {
    bool l1 = lane_out_present(&g_lane_l1);
    bool l2 = lane_out_present(&g_lane_l2);
    if (l1 && !l2) return 1;
    if (l2 && !l1) return 2;
    return 0;
}

static void set_active_lane(int lane) {
    active_lane = lane;
    if (lane == 1 || lane == 2) {
        char lane_s[2] = { (char)('0' + lane), 0 };
        cmd_event("ACTIVE", lane_s);
    } else {
        cmd_event("ACTIVE", "NONE");
    }
}

static inline lane_t *lane_ptr(int lane) {
    if (lane == 1) return &g_lane_l1;
    if (lane == 2) return &g_lane_l2;
    return NULL;
}

static inline int other_lane(int lane) {
    return (lane == 1) ? 2 : 1;
}

static void lane_setup(lane_t *L, uint pin_in, uint pin_out, motor_t m, int lane_id, uint diag_pin, tmc_t *tmc) {
    din_init(&L->in_sw, pin_in);
    din_init(&L->out_sw, pin_out);
    L->m = m;
    L->task = TASK_IDLE;
    L->task_limit_mm = 0.0f;
    L->target_sps = 0;
    L->current_sps = 0;
    L->ramp_last_tick_ms = 0;
    L->fault = FAULT_NONE;
    L->tmc = tmc;
    L->diag_pin = diag_pin;
    L->motion_started_ms = 0;
    L->task_started_ms = 0;
    L->stall_armed = false;
    L->fault = FAULT_NONE;
    L->lane_id = lane_id;
    L->runout_block_until_ms = 0;
    L->retract_deadline_ms = 0;
    L->unload_sensor_latch = false;
    L->buf_advance_since_ms = 0;
}

static void lane_stop(lane_t *L) {
    L->task = TASK_IDLE;
    L->task_started_ms = 0;
    L->stall_armed = false;
    L->stall_recovery = false;
    L->stall_recovery_deadline_ms = 0;
    L->unload_sensor_latch = false;
    L->retract_deadline_ms = 0;
    L->buf_advance_since_ms = 0;
    L->reload_tail_ms = 0;
    L->current_sps = 0;
    L->target_sps = 0;
    motor_stop(&L->m);
    // Revert to global default mode when idle.
    tmc_set_spreadcycle(L->tmc, TMC_SPREADCYCLE[L->lane_id - 1]);
}

static void lane_start(lane_t *L, task_t t, int sps, bool forward, uint32_t now_ms, float limit_mm) {
    L->task = t;
    L->fault = FAULT_NONE;
    L->last_dist_tick_ms = now_ms;
    L->task_dist_mm = 0.0f;
    L->dist_at_out_mm = 0.0f;
    L->stall_armed = false;
    L->unload_sensor_latch = false;
    L->retract_deadline_ms = 0;

    L->task_limit_mm = limit_mm;

    L->target_sps = sps;
    L->current_sps = RAMP_STEP_SPS;
    L->ramp_last_tick_ms = now_ms;
    L->motion_started_ms = now_ms;
    if (L->task_started_ms == 0) L->task_started_ms = now_ms;

    motor_enable(&L->m, true);
    motor_set_dir(&L->m, forward);
    motor_set_rate_sps(&L->m, L->current_sps);

    // Hybrid mode logic:
    // 1. RELOAD_APPROACH always needs SG (for contact detection).
    // 2. RELOAD_FOLLOW needs SG only if RELOAD_SG_INTERP is enabled.
    // 3. Normal sync (TC_IDLE) needs SG only if SYNC_SG_INTERP is enabled.
    bool is_sync = (t == TASK_FEED);
    bool is_reload_approach = (is_sync && g_tc_ctx.state == TC_RELOAD_APPROACH);
    bool is_reload_follow = (is_sync && g_tc_ctx.state == TC_RELOAD_FOLLOW);
    bool is_normal_sync = (is_sync && g_tc_ctx.state == TC_IDLE);

    bool use_sg_interpolation = (is_reload_follow && RELOAD_SG_INTERP) || (is_normal_sync && SYNC_SG_INTERP);
    bool use_stealth = is_reload_approach || use_sg_interpolation;

    int idx = L->lane_id - 1;
    bool run_spreadcycle = TMC_SPREADCYCLE[idx] && !use_stealth;
    int current_ma = use_stealth ? SG_CURRENT_MA[idx] : TMC_RUN_CURRENT_MA[idx];

    tmc_set_spreadcycle(L->tmc, run_spreadcycle);
    tmc_set_run_current_ma(L->tmc, current_ma, TMC_HOLD_CURRENT_MA[L->lane_id-1]);
}

static void sg_ma_update(lane_t *A) {
    if (!A || A->task != TASK_FEED) return;
    uint16_t sg_raw;
    if (tmc_read_sg_result(A->tmc, &sg_raw)) {
        g_tc_ctx.sg_ma_buf[g_tc_ctx.sg_ma_idx] = (float)sg_raw;
        g_tc_ctx.sg_ma_idx = (g_tc_ctx.sg_ma_idx + 1) % CONF_SG_MA_LEN;
        if (g_tc_ctx.sg_ma_fill < CONF_SG_MA_LEN) g_tc_ctx.sg_ma_fill++;
        float sum = 0.0f;
        for (int i = 0; i < (int)g_tc_ctx.sg_ma_fill; i++) sum += g_tc_ctx.sg_ma_buf[i];
        g_sg_load = sum / (float)g_tc_ctx.sg_ma_fill;
    }
}

static int sync_apply_scaling(lane_t *L, int base_sps, bool use_sg) {
    if (BUF_SENSOR_TYPE == 1) {
        // Analog: scale between TRAILING_SPS and base_sps based on g_buf_pos [-1, +1]
        float frac = clamp_f((g_buf_pos + 1.0f) * 0.5f, 0.0f, 1.0f);
        return (int)(TRAILING_SPS + (float)(base_sps - TRAILING_SPS) * frac);
    }

    int idx = lane_to_idx(L->lane_id);
    int target = base_sps;

    // 1. Calculate SG-based target if enabled
    if (use_sg && SG_TARGET[idx] > 0.1f) {
        // Linear scaling based on target load. 
        // 0.5 headroom (1.5 max) allows the MMU to accelerate to catch up 
        // even if the buffer is still in MID.
        float sg_frac = clamp_f(g_sg_load / SG_TARGET[idx], 0.0f, 1.5f);
        target = (int)((float)base_sps * sg_frac);
    }

    // 2. Sensor Fusion: Buffer Override (Priority)
    // The buffer provides the "Ground Truth" for absolute limits.
    if (g_buf.state == BUF_ADVANCE) {
        // Extruder is pulling. We MUST at least match base_sps (or baseline+KP).
        if (target < base_sps) target = base_sps;
    } else if (g_buf.state == BUF_TRAILING) {
        // Buffer is empty. We MUST NOT exceed TRAILING_SPS.
        if (target > TRAILING_SPS) target = TRAILING_SPS;
    }

    return target;
}

static void lane_tick(lane_t *L, uint32_t now_ms) {
    // Acceleration ramp: step current_sps toward target_sps every RAMP_TICK_MS.
    if (L->task != TASK_IDLE && L->current_sps < L->target_sps) {
        if ((int32_t)(now_ms - L->ramp_last_tick_ms) >= RAMP_TICK_MS) {
            L->ramp_last_tick_ms = now_ms;
            L->current_sps += RAMP_STEP_SPS;
            if (L->current_sps > L->target_sps) L->current_sps = L->target_sps;
            motor_set_rate_sps(&L->m, L->current_sps);
        }
    }

    // Distance integration
    uint32_t dt_ms = now_ms - L->last_dist_tick_ms;
    if (dt_ms > 0) {
        int idx = lane_to_idx(L->lane_id);
        L->task_dist_mm += (float)L->current_sps * ((float)dt_ms / 1000.0f) * MM_PER_STEP[idx];
        L->last_dist_tick_ms = now_ms;
    }

    if (!L->stall_armed && L->task != TASK_IDLE) {
        if ((int32_t)(now_ms - L->motion_started_ms) >= MOTION_STARTUP_MS) {
            L->stall_armed = true;
        }
    }

    // Expire the stall recovery window so a subsequent stall will hard-stop.
    if (L->stall_recovery && (int32_t)(now_ms - L->stall_recovery_deadline_ms) >= 0) {
        L->stall_recovery = false;
    }

    if (L->task == TASK_AUTOLOAD) {
        if (lane_out_present(L)) {
            // Filament reached OUT: back off so tip parks between IN and OUT.
            // Active lane is set at IN trigger (in autopreload_tick), not here.
            if (AUTOLOAD_RETRACT_MM > 0) {
                float secs = (float)AUTOLOAD_RETRACT_MM / ((float)REV_SPS * MM_PER_STEP[L->lane_id-1]);
                if (secs < 0.05f) secs = 0.05f;
                L->retract_deadline_ms = now_ms + (uint32_t)(secs * 1000.0f);
                L->task = TASK_UNLOAD;
                L->stall_armed = false;
                motor_set_dir(&L->m, false);
                // Ramp from zero in reverse direction.
                L->target_sps = REV_SPS;
                L->current_sps = RAMP_STEP_SPS;
                L->ramp_last_tick_ms = now_ms;
                motor_set_rate_sps(&L->m, L->current_sps);
            } else {
                lane_stop(L);
            }
        } else if (L->task_dist_mm > (float)DIST_IN_OUT * 1.5f) {
            // Jam detection: traveled 50% more than physical distance without hitting OUT.
            lane_stop(L);
            tc_enter_error("PRELOAD_JAM");
        } else if (L->task_limit_mm > 0 && L->task_dist_mm >= L->task_limit_mm) {
            lane_stop(L);
        }
    }

    if (L->task == TASK_UNLOAD && L->retract_deadline_ms != 0) {
        // Autopreload retract: timed back-off after reaching OUT.
        if ((int32_t)(now_ms - L->retract_deadline_ms) >= 0) {
            lane_stop(L);
        }
    }

    if (L->task == TASK_UNLOAD && L->retract_deadline_ms == 0) {
        // Extruder unload: reverse until OUT sensor clears.
        if (lane_out_present(L)) {
            L->unload_sensor_latch = true;
        }
        if (L->unload_sensor_latch && !lane_out_present(L)) {
            lane_stop(L);
            char lane_s[2] = { (char)('0' + L->lane_id), 0 };
            cmd_event("UNLOADED", lane_s);
        } else if (L->task_limit_mm > 0 && L->task_dist_mm >= L->task_limit_mm) {
            lane_stop(L);
            cmd_event("UNLOAD_TIMEOUT", NULL);
        }
    }

    if (L->task == TASK_UNLOAD_MMU) {
        // MMU unload: reverse until IN sensor clears.
        if (lane_in_present(L)) {
            L->unload_sensor_latch = true;
        }
        if (L->unload_sensor_latch && !lane_in_present(L)) {
            lane_stop(L);
            char lane_s[2] = { (char)('0' + L->lane_id), 0 };
            cmd_event("UNLOADED", lane_s);
        } else if (L->task_dist_mm > (float)DIST_IN_OUT * 1.5f) {
            // Jam detection: retracted 50% more than physical distance without clearing IN.
            lane_stop(L);
            tc_enter_error("UNLOAD_JAM");
        } else if (L->task_limit_mm > 0 && L->task_dist_mm >= L->task_limit_mm) {
            lane_stop(L);
            cmd_event("UNLOAD_TIMEOUT", NULL);
        }
    }

    if (L->task == TASK_LOAD_FULL) {
        // Track whether filament has passed OUT (reuse unload_sensor_latch as out_seen).
        if (lane_out_present(L) && !L->unload_sensor_latch) {
            L->unload_sensor_latch = true;
            L->dist_at_out_mm = L->task_dist_mm;
        }

        // TS:1 buffer fallback: after filament passes OUT, if buffer stays TRAILING
        // for TS_BUF_FALLBACK_MS, the tip is pressing against the toolhead entry (filament
        // blocked = MMU still pushing, buffer fills up). Treat as loaded.
        if (TS_BUF_FALLBACK_MS > 0 && L->unload_sensor_latch) {
            if (g_buf.state == BUF_TRAILING) {
                if (L->buf_advance_since_ms == 0) L->buf_advance_since_ms = now_ms;
                else if ((int32_t)(now_ms - L->buf_advance_since_ms) >= TS_BUF_FALLBACK_MS)
                    set_toolhead_filament(true);
            } else {
                L->buf_advance_since_ms = 0;
            }
        }

        char lane_s[2] = { (char)('0' + L->lane_id), 0 };
        // Success Triggers: 
        // 1. Host reported TS:1
        // 2. Buffer Advance (Extruder is pulling)
        // 3. Buffer Trailing (Filament reached gears/blockage) AFTER passing OUT sensor
        // Sanity: Ignore buffer advance until tip has reached at least the buffer's neutral point.
        bool buf_advance_sane = (g_buf.state == BUF_ADVANCE);
        if (L->unload_sensor_latch) {
            float dist_since_out = L->task_dist_mm - L->dist_at_out_mm;
            float threshold = (float)DIST_OUT_Y + Y_TO_BUF_NEUTRAL;
            if (dist_since_out < threshold * 0.8f) {
                buf_advance_sane = false; // Path Sanity: Tip hasn't reached buffer neutral yet.
            }
        } else {
            buf_advance_sane = false; // Tip hasn't even reached OUT.
        }

        bool loaded = toolhead_has_filament || buf_advance_sane || 
                     (L->unload_sensor_latch && g_buf.state == BUF_TRAILING);

        if (loaded) {
            lane_stop(L);
            cmd_event("LOADED", lane_s);
            // Automatically enable sync if we are in AUTO_MODE
            if (AUTO_MODE) {
                sync_enabled = true;
                sync_auto_started = true;
                sync_idle_since_ms = 0;
            }
        } else if (!lane_in_present(L) && (int32_t)(now_ms - L->task_started_ms) >= 1000) {
            if (lane_out_present(L)) {
                // Tail between IN and OUT: keep pushing until OUT clears or 10s timeout.
                L->reload_tail_ms = now_ms;
            } else {
                lane_stop(L);
                cmd_event("RUNOUT", lane_s);
                if (RELOAD_MODE && tc_state() == TC_IDLE) reload_trigger(L->lane_id, now_ms);
            }
        } else if (!L->unload_sensor_latch &&
                   (int32_t)(now_ms - L->motion_started_ms) >= 10000) {
            // OUT not seen after 10 s — motor likely free-spinning (tail stuck at IN
            // behind drive gear, not engaged). User must clear manually.
            lane_stop(L);
            cmd_event("RUNOUT", lane_s);
        } else if (L->task_limit_mm > 0 && L->task_dist_mm >= L->task_limit_mm) {
            lane_stop(L);
            cmd_event("LOAD_TIMEOUT", lane_s);
        }
    }

    if (L->task == TASK_MOVE) {
        if (L->task_limit_mm > 0 && L->task_dist_mm >= L->task_limit_mm) {
            lane_stop(L);
            char lane_s[2] = { (char)('0' + L->lane_id), 0 };
            cmd_event("MOVE_DONE", lane_s);
        }
    }

    if ((L->task == TASK_FEED || L->task == TASK_AUTOLOAD) && !lane_in_present(L)) {
        if ((int32_t)(now_ms - L->task_started_ms) >= 1000 &&
            (int32_t)(now_ms - L->runout_block_until_ms) >= 0) {
            if (lane_out_present(L)) {
                L->reload_tail_ms = now_ms;
                L->runout_block_until_ms = now_ms + 30000u;
            } else {
                char lane_s[2] = { (char)('0' + L->lane_id), 0 };
                cmd_event("RUNOUT", lane_s);
                L->runout_block_until_ms = now_ms + (uint32_t)RUNOUT_COOLDOWN_MS;
                if (L->task == TASK_FEED) set_toolhead_filament(false);
                lane_stop(L);
                if (RELOAD_MODE && L->task == TASK_FEED && tc_state() == TC_IDLE)
                    reload_trigger(L->lane_id, now_ms);
            }
        }
    }

    if (L->reload_tail_ms != 0 && (L->task == TASK_FEED || L->task == TASK_LOAD_FULL || L->task == TASK_AUTOLOAD)) {
        uint32_t tail_age = now_ms - L->reload_tail_ms;
        if (!lane_out_present(L) || (RELOAD_MODE && g_buf.state == BUF_ADVANCE) || tail_age >= 10000u) {
            char lane_s[2] = { (char)('0' + L->lane_id), 0 };
            L->reload_tail_ms = 0;
            L->runout_block_until_ms = now_ms + (uint32_t)RUNOUT_COOLDOWN_MS;
            cmd_event("RUNOUT", lane_s);
            if (L->task == TASK_FEED) set_toolhead_filament(false);
            bool was_reload = (RELOAD_MODE && L->task == TASK_FEED && tc_state() == TC_IDLE);
            lane_stop(L);
            if (was_reload) reload_trigger(L->lane_id, now_ms);
        }
    }
}

static void stop_all(void) {
    lane_stop(&g_lane_l1);
    lane_stop(&g_lane_l2);
}

// ===================== Servo =====================
static uint g_servo_slice = 0;
static uint g_servo_chan = 0;

static void servo_init(uint pin) {
    gpio_set_function(pin, GPIO_FUNC_PWM);
    g_servo_slice = pwm_gpio_to_slice_num(pin);
    g_servo_chan = pwm_gpio_to_channel(pin);

    pwm_config c = pwm_get_default_config();
    pwm_config_set_clkdiv(&c, 125.0f);
    pwm_config_set_wrap(&c, 20000 - 1);
    pwm_init(g_servo_slice, &c, false);
}

static void servo_set_us(uint pin, uint pulse_us) {
    (void)pin;
    if (pulse_us < 500) pulse_us = 500;
    if (pulse_us > 2500) pulse_us = 2500;
    pwm_set_chan_level(g_servo_slice, g_servo_chan, (uint16_t)pulse_us);
    pwm_set_enabled(g_servo_slice, true);
}

static void servo_idle(uint pin) {
    (void)pin;
    pwm_set_enabled(g_servo_slice, false);
}

// ===================== Cutter =====================
static inline bool cutter_busy(void) {
    return g_cut.state != CUT_IDLE;
}

static uint32_t cut_feed_ms_for_mm(int mm, int idx) {
    float secs = (float)mm / ((float)REV_SPS * MM_PER_STEP[idx]);
    if (secs < 0.0f) secs = 0.0f;
    return (uint32_t)(secs * 1000.0f);
}

static void cut_begin_feed(uint32_t now_ms, uint32_t window_ms) {
    g_cut.feed_active_ms = window_ms;
    motor_enable(&g_cut.lane->m, true);
    motor_set_dir(&g_cut.lane->m, true);
    motor_set_rate_sps(&g_cut.lane->m, REV_SPS);
    g_cut.lane->stall_armed = false;
    g_cut.phase_start_ms = now_ms;
    g_cut.state = CUT_FEED_WAIT;
}

static void cutter_start(lane_t *L, uint32_t now_ms) {
    if (g_cut.state != CUT_IDLE) return;

    int idx = L->lane_id - 1;
    g_cut.lane = L;
    g_cut.repeats_done = 0;
    g_cut.feed_initial_ms = cut_feed_ms_for_mm(CUT_FEED_MM, idx);
    g_cut.feed_repeat_ms = cut_feed_ms_for_mm(CUT_LENGTH_MM, idx);
    g_cut.phase_start_ms = now_ms;
    g_cut.state = CUT_OPENING;

    char lane_s[2] = { (char)('0' + L->lane_id), 0 };
    cmd_event("TC:CUTTING", lane_s);
}

static void cutter_abort(void) {
    if (g_cut.state == CUT_IDLE) return;  // nothing to abort
    if (g_cut.lane) {
        motor_stop(&g_cut.lane->m);
    }
    servo_set_us(PIN_SERVO, SERVO_OPEN_US);
    g_cut.phase_start_ms = to_ms_since_boot(get_absolute_time());
    g_cut.state = CUT_OPEN_WAIT;
    g_cut.repeats_done = 0;
}

static void cutter_tick(uint32_t now_ms) {
    uint32_t age = now_ms - g_cut.phase_start_ms;

    switch (g_cut.state) {
        case CUT_IDLE:
            return;

        case CUT_OPENING:
            servo_set_us(PIN_SERVO, SERVO_OPEN_US);
            g_cut.phase_start_ms = now_ms;
            g_cut.state = CUT_OPEN_WAIT;
            break;

        case CUT_OPEN_WAIT:
            if (age > (uint32_t)CUT_TIMEOUT_SETTLE_MS) {
                cutter_abort();
            } else if (age >= (uint32_t)SERVO_SETTLE_MS) {
                g_cut.phase_start_ms = now_ms;
                g_cut.state = CUT_FEEDING;
            }
            break;

        case CUT_FEEDING:
            cmd_event("CUT:FEEDING", NULL);
            cut_begin_feed(now_ms, g_cut.repeats_done == 0 ? g_cut.feed_initial_ms : g_cut.feed_repeat_ms);
            break;

        case CUT_FEED_WAIT:
            if (age >= g_cut.feed_active_ms) {
                motor_stop(&g_cut.lane->m);
                g_cut.phase_start_ms = now_ms;
                g_cut.state = CUT_CLOSING;
            } else if (age > (uint32_t)CUT_TIMEOUT_FEED_MS) {
                cutter_abort();
            }
            break;

        case CUT_CLOSING:
            servo_set_us(PIN_SERVO, SERVO_CLOSE_US);
            g_cut.phase_start_ms = now_ms;
            g_cut.state = CUT_CLOSE_WAIT;
            break;

        case CUT_CLOSE_WAIT:
            if (age > (uint32_t)CUT_TIMEOUT_SETTLE_MS) {
                cutter_abort();
            } else if (age >= (uint32_t)SERVO_SETTLE_MS) {
                g_cut.phase_start_ms = now_ms;
                g_cut.state = CUT_REOPENING;
            }
            break;

        case CUT_REOPENING:
            servo_set_us(PIN_SERVO, SERVO_OPEN_US);
            g_cut.phase_start_ms = now_ms;
            g_cut.state = CUT_REOPEN_WAIT;
            break;

        case CUT_REOPEN_WAIT:
            if (age > (uint32_t)CUT_TIMEOUT_SETTLE_MS) {
                cutter_abort();
            } else if (age >= (uint32_t)SERVO_SETTLE_MS) {
                g_cut.state = CUT_REPEAT_CHECK;
            }
            break;

        case CUT_REPEAT_CHECK:
            if (g_cut.repeats_done < CUT_AMOUNT - 1) {
                g_cut.repeats_done++;
                g_cut.phase_start_ms = now_ms;
                g_cut.state = CUT_FEEDING;
            } else {
                g_cut.phase_start_ms = now_ms;
                g_cut.state = CUT_DONE;
            }
            break;

        case CUT_DONE:
            servo_set_us(PIN_SERVO, SERVO_BLOCK_US);
            if (age >= (uint32_t)SERVO_SETTLE_MS) {
                servo_idle(PIN_SERVO);
                g_cut.state = CUT_IDLE;
            }
            break;
    }
}

// ===================== Toolchange =====================
static inline tc_state_t tc_state(void) {
    return g_tc_ctx.state;
}

static void tc_enter_error(const char *reason) {
    cmd_event("TC:ERROR", reason);
    stop_all();
    cutter_abort();
    g_tc_ctx.state = TC_ERROR;
}

static void tc_start(int target_lane, uint32_t now_ms) {
    if (g_tc_ctx.state != TC_IDLE) return;
    memset(&g_tc_ctx, 0, sizeof(g_tc_ctx));
    if (target_lane != 1 && target_lane != 2) return;
    if (active_lane != 1 && active_lane != 2) return;

    g_tc_ctx.target_lane = target_lane;
    g_tc_ctx.from_lane = active_lane;
    g_tc_ctx.phase_start_ms = now_ms;
    set_toolhead_filament(false);
    if (target_lane == active_lane) {
        g_tc_ctx.state = TC_LOAD_START;
    } else if (ENABLE_CUTTER) {
        g_tc_ctx.state = TC_UNLOAD_CUT;
    } else {
        // Cutter disabled: skip cut, go straight to reverse unload.
        g_tc_ctx.state = TC_UNLOAD_REVERSE;
    }
}

static void tc_abort(void) {
    if (g_tc_ctx.state == TC_IDLE) return;
    stop_all();
    cutter_abort();
    set_toolhead_filament(false);
    g_tc_ctx.state = TC_IDLE;
    cmd_event("TC:ERROR", "ABORTED");
}

// RELOAD auto-switch: called when the old lane's filament tail exits OUT.
// Waits for the Y-splitter to clear, then starts the standby lane in sync mode
// until TRAILING confirms the new tip has met the old tail.
static void reload_trigger(int runout_lane, uint32_t now_ms) {
    memset(&g_tc_ctx, 0, sizeof(g_tc_ctx));
    int other = (runout_lane == 1) ? 2 : 1;
    lane_t *OL = lane_ptr(other);
    if (!OL || !lane_in_present(OL)) {
        cmd_event("RELOAD:FAULT", "NO_FILAMENT");
        return;
    }
    char ev[8];
    snprintf(ev, sizeof(ev), "%d->%d", runout_lane, other);
    cmd_event("RELOAD:SWITCHING", ev);
    g_tc_ctx.target_lane    = other;
    g_tc_ctx.from_lane      = runout_lane;
    g_tc_ctx.phase_start_ms = now_ms;
    g_tc_ctx.reload_stall_count = 0;
    g_tc_ctx.state          = TC_RELOAD_WAIT_Y;
}

static const char *tc_state_name(tc_state_t s) {
    switch (s) {
        case TC_IDLE: return "IDLE";
        case TC_UNLOAD_CUT: return "UNLOAD_CUT";
        case TC_UNLOAD_WAIT_CUT: return "UNLOAD_WAIT_CUT";
        case TC_UNLOAD_REVERSE: return "UNLOAD_REVERSE";
        case TC_UNLOAD_WAIT_OUT: return "UNLOAD_WAIT_OUT";
        case TC_UNLOAD_WAIT_Y:  return "UNLOAD_WAIT_Y";
        case TC_UNLOAD_WAIT_TH: return "UNLOAD_WAIT_TH";
        case TC_UNLOAD_DONE: return "UNLOAD_DONE";
        case TC_SWAP: return "SWAP";
        case TC_LOAD_START: return "LOAD_START";
        case TC_LOAD_WAIT_OUT: return "LOAD_WAIT_OUT";
        case TC_LOAD_WAIT_TH: return "LOAD_WAIT_TH";
        case TC_LOAD_DONE: return "LOAD_DONE";
        case TC_RELOAD_WAIT_Y:   return "RELOAD_WAIT_Y";
        case TC_RELOAD_APPROACH: return "RELOAD_APPROACH";
        case TC_RELOAD_FOLLOW:   return "RELOAD_FOLLOW";
        case TC_ERROR: return "ERROR";
        default: return "?";
    }
}

static const char *task_name(task_t t) {
    switch (t) {
        case TASK_IDLE: return "IDLE";
        case TASK_AUTOLOAD: return "AUTOLOAD";
        case TASK_FEED: return "FEED";
        case TASK_UNLOAD: return "UNLOAD";
        case TASK_UNLOAD_MMU: return "UNLOAD_MMU";
        case TASK_LOAD_FULL: return "LOAD_FULL";
        case TASK_MOVE: return "MOVE";
        default: return "?";
    }
}

static void tc_tick(uint32_t now_ms) {
    uint32_t age = now_ms - g_tc_ctx.phase_start_ms;
    lane_t *A = lane_ptr(active_lane);

    switch (g_tc_ctx.state) {
        case TC_IDLE:
        case TC_ERROR:
            return;

        case TC_UNLOAD_CUT:
            cutter_start(A, now_ms);
            g_tc_ctx.phase_start_ms = now_ms;
            g_tc_ctx.state = TC_UNLOAD_WAIT_CUT;
            break;

        case TC_UNLOAD_WAIT_CUT:
            if (!cutter_busy()) {
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = TC_UNLOAD_REVERSE;
            } else if (age > (uint32_t)TC_TIMEOUT_CUT_MS) {
                tc_enter_error("CUT_TIMEOUT");
            }
            break;

        case TC_UNLOAD_REVERSE: {
            char lane_s[2] = { (char)('0' + active_lane), 0 };
            cmd_event("TC:UNLOADING", lane_s);
            if (!lane_out_present(A)) {
                // Already before OUT (pre-loaded); skip reverse entirely.
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = (TC_TIMEOUT_Y_MS > 0) ? TC_UNLOAD_WAIT_Y :
                                 (TC_TIMEOUT_TH_MS > 0) ? TC_UNLOAD_WAIT_TH : TC_UNLOAD_DONE;
            } else {
                lane_start(A, TASK_UNLOAD, REV_SPS, false, now_ms, (float)UNLOAD_MAX_MM);
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = TC_UNLOAD_WAIT_OUT;
            }
            break;
        }

        case TC_UNLOAD_WAIT_OUT:
            if (!lane_out_present(A)) {
                lane_stop(A);
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = (TC_TIMEOUT_Y_MS > 0) ? TC_UNLOAD_WAIT_Y :
                                 (TC_TIMEOUT_TH_MS > 0) ? TC_UNLOAD_WAIT_TH : TC_UNLOAD_DONE;
            } else if (A->task == TASK_IDLE) {
                tc_enter_error("UNLOAD_TIMEOUT");
            }
            break;
        case TC_UNLOAD_WAIT_Y:
            if (!on_al(&g_y_split)) {
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = (TC_TIMEOUT_TH_MS > 0) ? TC_UNLOAD_WAIT_TH : TC_UNLOAD_DONE;
            } else if (age > (uint32_t)TC_TIMEOUT_Y_MS) {
                tc_enter_error("Y_TIMEOUT");
            }
            break;

        case TC_UNLOAD_WAIT_TH:
            if (!toolhead_has_filament || age > (uint32_t)TC_TIMEOUT_TH_MS) {
                g_tc_ctx.state = TC_UNLOAD_DONE;
            }
            break;

        case TC_UNLOAD_DONE:
            g_tc_ctx.state = TC_SWAP;
            break;

        case TC_SWAP: {
            char swap_s[8];
            snprintf(swap_s, sizeof(swap_s), "%d->%d", active_lane, g_tc_ctx.target_lane);
            cmd_event("TC:SWAPPING", swap_s);
            active_lane = g_tc_ctx.target_lane;
            g_tc_ctx.phase_start_ms = now_ms;
            g_tc_ctx.state = TC_LOAD_START;
            break;
        }

        case TC_LOAD_START: {
            if (TC_TIMEOUT_Y_MS > 0 && on_al(&g_y_split)) {
                tc_enter_error("HUB_NOT_CLEAR");
                break;
            }
            char lane_s[2] = { (char)('0' + active_lane), 0 };
            cmd_event("TC:LOADING", lane_s);
            set_toolhead_filament(false);
            lane_start(A, TASK_LOAD_FULL, FEED_SPS, true, now_ms, (float)LOAD_MAX_MM);
            g_tc_ctx.phase_start_ms = now_ms;
            g_tc_ctx.state = TC_LOAD_WAIT_OUT;
            break;
        }

        case TC_LOAD_WAIT_OUT:
            // Non-stopping checkpoint: TASK_LOAD_FULL continues past OUT toward toolhead.
            if (lane_out_present(A)) {
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = TC_LOAD_WAIT_TH;
            } else if (A->task == TASK_IDLE) {
                tc_enter_error("LOAD_TIMEOUT");
            }
            break;

        case TC_LOAD_WAIT_TH:
            // Wait for TASK_LOAD_FULL to complete (stopped by TS:1 or distance limit).
            if (A->task == TASK_IDLE) {
                if (toolhead_has_filament) {
                    g_tc_ctx.state = TC_LOAD_DONE;
                } else {
                    tc_enter_error("LOAD_TIMEOUT");
                }
            }
            break;

        case TC_RELOAD_WAIT_Y:
            // Old filament tail exited OUT; wait for it to clear the Y-splitter
            // before starting the standby lane.
            if (!on_al(&g_y_split) || RELOAD_Y_TIMEOUT_MS == 0) {
                char lane_s[2] = { (char)('0' + g_tc_ctx.target_lane), 0 };
                set_active_lane(g_tc_ctx.target_lane);
                lane_t *NL = lane_ptr(active_lane);
                cmd_event("RELOAD:JOINING", lane_s);
                // State 1: fast approach — contact detected by SG derivative.
                lane_start(NL, TASK_FEED, JOIN_SPS, true, now_ms, 2000.0f); // Default 2m approach
                // Arm stall immediately — STARTUP_MS warmup is for sync mode.
                NL->stall_armed = true;
                // Zero RELOAD ctx fields for fresh approach.
                g_tc_ctx.reload_tick_ms = now_ms;
                g_tc_ctx.sg_ma_idx   = 0;
                g_tc_ctx.sg_ma_fill  = 0;
                g_tc_ctx.sg_ma_prev  = 0.0f;
                for (int i = 0; i < CONF_SG_MA_LEN; i++) g_tc_ctx.sg_ma_buf[i] = 0.0f;
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = TC_RELOAD_APPROACH;
            } else if (age > (uint32_t)RELOAD_Y_TIMEOUT_MS) {
                tc_enter_error("RELOAD_Y_TIMEOUT");
            }
            break;

        case TC_RELOAD_APPROACH: {
            // State 1: Fast Approach & Soft Crash Detection.
            //
            // Motor runs at JOIN_SPS.  SG is sampled into a moving-average
            // ring buffer every SYNC_TICK_MS.  The per-tick derivative (MA delta)
            // is watched: a sharp negative drop means the new tip just hit the old
            // tail at speed.  We transition immediately — before the buffer arm even
            // moves — so we avoid grinding the filament with a hard SG==0 stall.
            //
            // Fallback: if the buffer reports TRAILING (physical contact at lower
            // speed) or a hard DIAG stall fires, both also trigger transition.

            if (A && A->task == TASK_IDLE && A->fault != FAULT_STALL) {
                tc_enter_error("RELOAD_APPROACH_FAULT");
                break;
            }

            // SG derivative contact detection — 2-endstop mode only.
            // For analog the buffer position already changes continuously on contact;
            // the BUF_TRAILING fallback below is sufficient.
            bool contacted = false;
            if (BUF_SENSOR_TYPE == 0 &&
                (now_ms - g_tc_ctx.reload_tick_ms) >= (uint32_t)SYNC_TICK_MS) {
                g_tc_ctx.reload_tick_ms = now_ms;

                if (A && A->task == TASK_FEED) {
                    uint16_t sg_raw;
                    if (tmc_read_sg_result(A->tmc, &sg_raw)) {
                        // Push into MA ring buffer.
                        g_tc_ctx.sg_ma_buf[g_tc_ctx.sg_ma_idx] = (float)sg_raw;
                        g_tc_ctx.sg_ma_idx = (g_tc_ctx.sg_ma_idx + 1) % CONF_SG_MA_LEN;
                        if (g_tc_ctx.sg_ma_fill < CONF_SG_MA_LEN) g_tc_ctx.sg_ma_fill++;

                        // Compute MA and derivative.
                        if (g_tc_ctx.sg_ma_fill == CONF_SG_MA_LEN) {
                            float sum = 0.0f;
                            for (int i = 0; i < CONF_SG_MA_LEN; i++)
                                sum += g_tc_ctx.sg_ma_buf[i];
                            float sg_ma = sum / (float)CONF_SG_MA_LEN;
                            g_sg_load = sg_ma;

                            float deriv = sg_ma - g_tc_ctx.sg_ma_prev;
                            g_tc_ctx.sg_ma_prev = sg_ma;
                            // RELOAD hit detection refined:
                            // Require a sharp drop (SG_DERIV) AND the value must be below 50% of SG_TARGET.
                            // This ignores minor friction/drag and only triggers on a significant load increase.
                            int idx = lane_to_idx(A->lane_id);
                            if (SG_DERIV[idx] > 0 && 
                                deriv < -(float)SG_DERIV[idx] &&
                                sg_ma < (SG_TARGET[idx] * 0.5f))
                                contacted = true;
                        } else {
                            g_tc_ctx.sg_ma_prev = (float)sg_raw;
                        }
                    }
                }
            }

            if (A && !lane_in_present(A)) {
                if (g_tc_ctx.runout_ms == 0) g_tc_ctx.runout_ms = now_ms;
                if ((now_ms - g_tc_ctx.runout_ms) > 15000) {
                    tc_enter_error("RELOAD_APPROACH_RUNOUT");
                    lane_stop(A);
                    break;
                }
            } else {
                g_tc_ctx.runout_ms = 0;
            }

            // Buffer/stall fallbacks — always active regardless of sensor type.
            bool stalled = (A && A->task == TASK_IDLE && A->fault == FAULT_STALL);
            if (g_buf.state == BUF_TRAILING || stalled)
                contacted = true;

            if (contacted) {
                if (A) {
                    if (stalled) A->fault = FAULT_NONE;
                    lane_stop(A);
                }
                // Initialise State 2 at PRESS_SPS so follow sync starts
                // immediately without a ramp-up delay.
                g_tc_ctx.reload_current_sps = PRESS_SPS;
                g_tc_ctx.reload_tick_ms     = now_ms;
                g_tc_ctx.phase_start_ms  = now_ms;
                g_tc_ctx.state = TC_RELOAD_FOLLOW;
            } else if (A->task == TASK_IDLE) {
                tc_enter_error("RELOAD_APPROACH_TIMEOUT");
            }
            break;
        }

        case TC_RELOAD_FOLLOW: {
            // State 2: Follow Sync (Bang-Bang + SG Interpolation).
            //
            // Tips are touching.  We follow the old filament's speed through the
            // entire bowden journey (~1 m) to the extruder.
            //
            // Speed is driven by two signals:
            //   SG interpolation — target SG_TARGET (above crash=0, below
            //     free-air=~14).  Speed scales linearly: SG at target → PRESS_SPS,
            //     SG at 0 → 0.  If SG not configured (SG_TARGET==0), speed comes
            //     from buffer state alone.
            //   Buffer cap — when TRAILING (buffer full), speed is clamped to
            //     TRAILING_SPS regardless of SG.
            //
            // Exit (State 3 — Toolhead Handover):
            //   BUF_ADVANCE: extruder grabbed the new tip and is pulling faster than
            //     we are pushing → buffer empties → ADVANCE fires.  Definitive event.
            //   toolhead_has_filament (TS:1): host confirmed pickup.
            //   Safety timeout: TC_TIMEOUT_LOAD_MS.

            // ADVANCE = handover confirmed (State 3 exit).
            if (g_buf.state == BUF_ADVANCE || toolhead_has_filament) {
                if (A) lane_stop(A);
                set_toolhead_filament(true);
                char lane_s[2] = { (char)('0' + active_lane), 0 };
                cmd_event("RELOAD:LOADED", lane_s);
                g_tc_ctx.state = TC_IDLE;
                break;
            }

            if (A->task_dist_mm >= (float)LOAD_MAX_MM) {
                if (A) lane_stop(A);
                set_toolhead_filament(true);
                char lane_s[2] = { (char)('0' + active_lane), 0 };
                cmd_event("RELOAD:LOADED", lane_s);
                g_tc_ctx.state = TC_IDLE;
                break;
            }

            // Tick-rate-limited speed update.
            if ((now_ms - g_tc_ctx.reload_tick_ms) < (uint32_t)SYNC_TICK_MS) break;
            g_tc_ctx.reload_tick_ms = now_ms;

            // Speed target — common calculation.
            // RELOAD_SG_INTERP=1 enables pseudo-analog interpolation.
            // RELOAD_SG_INTERP=0 falls back to digital bang-bang (TRAILING_SPS / PRESS_SPS).
            sg_ma_update(A);
            int target_sps = sync_apply_scaling(A, PRESS_SPS, RELOAD_SG_INTERP);

            // Ramp toward target.
            if (g_tc_ctx.reload_current_sps > target_sps)
                g_tc_ctx.reload_current_sps -= SYNC_RAMP_DN_SPS;
            else if (g_tc_ctx.reload_current_sps < target_sps)
                g_tc_ctx.reload_current_sps += SYNC_RAMP_UP_SPS;
            g_tc_ctx.reload_current_sps = clamp_i(g_tc_ctx.reload_current_sps, 0, PRESS_SPS);

            // Drive motor.
            if (A) {
                if (g_tc_ctx.reload_current_sps > 0) {
                    if (A->task != TASK_FEED)
                        lane_start(A, TASK_FEED, g_tc_ctx.reload_current_sps, true, now_ms, 0);
                    else
                        motor_set_rate_sps(&A->m, g_tc_ctx.reload_current_sps);
                } else if (A->task == TASK_FEED) {
                    lane_stop(A);
                }
                // Stall during follow: contact spike or hard obstruction.
                if (A->fault == FAULT_STALL) {
                    A->fault = FAULT_NONE;
                    // Reset count if stalls are far apart (> 2s).
                    if ((int32_t)(now_ms - g_tc_ctx.last_reload_stall_ms) > 2000)
                        g_tc_ctx.reload_stall_count = 0;
                    
                    g_tc_ctx.reload_stall_count++;
                    g_tc_ctx.last_reload_stall_ms = now_ms;
                    g_tc_ctx.reload_current_sps = TRAILING_SPS;

                    if (g_tc_ctx.reload_stall_count >= 3) {
                        tc_enter_error("FOLLOW_JAM");
                        lane_stop(A);
                        break;
                    }
                }
            }

            // Dry spin protection: if IN clears and we don't finish in 15s, abort.
            if (A && !lane_in_present(A)) {
                if (g_tc_ctx.runout_ms == 0) g_tc_ctx.runout_ms = now_ms;
                if ((now_ms - g_tc_ctx.runout_ms) > 15000) {
                    tc_enter_error("RELOAD_DRY_RUNOUT");
                    lane_stop(A);
                    break;
                }
            } else {
                g_tc_ctx.runout_ms = 0;
            }

            // Safety timeout: if buffer stays in TRAILING for > 10s, abort.
            if (g_buf.state == BUF_TRAILING) {
                if (g_tc_ctx.last_trailing_ms == 0) g_tc_ctx.last_trailing_ms = now_ms;
                if ((now_ms - g_tc_ctx.last_trailing_ms) > (uint32_t)FOLLOW_TIMEOUT_MS[lane_to_idx(A->lane_id)]) {
                    tc_enter_error("FOLLOW_TIMEOUT");
                    lane_stop(A);
                    break;
                }
            } else {
                g_tc_ctx.last_trailing_ms = 0;
            }

            // Reporting for interpolation debugging
            static uint32_t last_reload_report_ms = 0;
            if ((now_ms - last_reload_report_ms) >= 500u) {
                last_reload_report_ms = now_ms;
                char ev[64];
                int idx = lane_to_idx(A->lane_id);
                float sg_report = (SG_TARGET[idx] > 0.1f) ? (g_sg_load / SG_TARGET[idx]) : 0.0f;
                snprintf(ev, sizeof(ev), "%s,%.1f,%.2f",
                         buf_state_name(g_buf.state),
                         (double)sps_to_mm_per_min(g_tc_ctx.reload_current_sps),
                         (double)sg_report);
                cmd_event("BS", ev);
            }
            break;
        }

        case TC_LOAD_DONE: {
            char lane_s[2] = { (char)('0' + active_lane), 0 };
            cmd_event("TC:DONE", lane_s);
            // toolhead_has_filament was set to true when load completed; sync is
            // already enabled via set_toolhead_filament — nothing extra needed here.
            g_tc_ctx.state = TC_IDLE;
            break;
        }
    }
}

static void autopreload_tick(uint32_t now_ms) {
    if (!AUTO_MODE && !AUTO_PRELOAD) {
        prev_lane1_in_present = lane_in_present(&g_lane_l1);
        prev_lane2_in_present = lane_in_present(&g_lane_l2);
        return;
    }

    bool in1 = lane_in_present(&g_lane_l1);
    bool in2 = lane_in_present(&g_lane_l2);

    // MMU is completely empty if neither OUT sensor is present.
    bool mmu_empty = !lane_out_present(&g_lane_l1) && !lane_out_present(&g_lane_l2);

    if (in1 && !prev_lane1_in_present) {
        if (g_lane_l1.task == TASK_IDLE && (tc_state() == TC_IDLE || tc_state() == TC_RELOAD_FOLLOW) && !cutter_busy() && !lane_out_present(&g_lane_l1)) {
            if (AUTO_MODE && mmu_empty) {
                // Completely empty MMU: auto-load all the way to toolhead.
                lane_start(&g_lane_l1, TASK_LOAD_FULL, FEED_SPS, true, now_ms, (float)LOAD_MAX_MM);
                cmd_event("AUTO_LOAD", "1");
            } else if (AUTO_PRELOAD) {
                // Other lane loaded (or AUTO_MODE off): just preload to Y-splitter.
                lane_start(&g_lane_l1, TASK_AUTOLOAD, AUTO_SPS, true, now_ms, (float)AUTOLOAD_MAX_MM);
                cmd_event("PRELOAD", "1");
            }
            if (!lane_out_present(&g_lane_l2)) set_active_lane(1);
        }
    }

    if (in2 && !prev_lane2_in_present) {
        if (g_lane_l2.task == TASK_IDLE && (tc_state() == TC_IDLE || tc_state() == TC_RELOAD_FOLLOW) && !cutter_busy() && !lane_out_present(&g_lane_l2)) {
            if (AUTO_MODE && mmu_empty) {
                lane_start(&g_lane_l2, TASK_LOAD_FULL, FEED_SPS, true, now_ms, (float)LOAD_MAX_MM);
                cmd_event("AUTO_LOAD", "2");
            } else if (AUTO_PRELOAD) {
                lane_start(&g_lane_l2, TASK_AUTOLOAD, AUTO_SPS, true, now_ms, (float)AUTOLOAD_MAX_MM);
                cmd_event("PRELOAD", "2");
            }
            if (!lane_out_present(&g_lane_l1)) set_active_lane(2);
        }
    }

    prev_lane1_in_present = in1;
    prev_lane2_in_present = in2;
}

// ===================== Buffer history + sync =====================
static const char *buf_state_name(buf_state_t s) {
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

// Read and EMA-filter the analog buffer position into g_buf_pos.
// Called once per sync tick when BUF_SENSOR_TYPE == 1.
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
        if (g_buf_pos >  BUF_THR) return BUF_ADVANCE;
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

static void buf_update(buf_state_t new_state, uint32_t now_ms) {
    if (new_state == g_buf.state) return;

    uint32_t prev_dwell = now_ms - g_buf.entered_ms;
    g_buf.dwell_ms = prev_dwell;

    if (g_buf.state == BUF_MID && (new_state == BUF_ADVANCE || new_state == BUF_TRAILING) && prev_dwell > 0) {
        g_buf.arm_vel_mm_s = BUF_HALF_TRAVEL_MM / ((float)prev_dwell / 1000.0f);
    }

    history_push(g_buf.state, prev_dwell);
    g_buf.state = new_state;
    g_buf.entered_ms = now_ms;
}

static void baseline_update_on_settle(uint32_t mid_dwell_ms) {
    if (mid_dwell_ms > 500) {
        g_baseline_sps = (int)(g_baseline_alpha * (float)sync_current_sps + (1.0f - g_baseline_alpha) * (float)g_baseline_sps);
    }
}

static void sync_apply_to_active(void) {
    lane_t *A = lane_ptr(active_lane);
    if (!A) {
        sync_current_sps = 0;
        return;
    }
    if (A->task == TASK_MOVE) return;  // don't clobber an in-progress timed move

    if (sync_current_sps > 0) {
        if (A->task != TASK_FEED) {
            lane_start(A, TASK_FEED, sync_current_sps, true, g_now_ms, 0);
        } else {
            A->current_sps = sync_current_sps;
            A->target_sps = sync_current_sps;
            motor_set_rate_sps(&A->m, sync_current_sps);
            motor_enable(&A->m, true);
            motor_set_dir(&A->m, true);
        }
    } else {
        if (A->task == TASK_FEED) {
            motor_stop(&A->m);
            A->current_sps = 0;
            A->target_sps = 0;
            // Revert to global default mode when idle.
            tmc_set_spreadcycle(A->tmc, TMC_SPREADCYCLE[A->lane_id - 1]);
        }
    }
}

static void sync_on_transition(buf_state_t prev, buf_state_t now_state) {
    if (prev == BUF_ADVANCE && now_state == BUF_MID && sync_enabled) {
        baseline_update_on_settle(g_buf.dwell_ms);
    }
}

// Update buffer sensor state and g_buf_pos unconditionally — called every main-loop
// iteration so both are current regardless of sync_enabled (needed for TS fallback).
// g_buf_pos semantics: both modes produce a normalised position in [-1, +1].
//   Analog:  ADC EMA (BUF_ANALOG_ALPHA) — reflects continuous arm deflection.
//   Endstop: zone EMA (BUF_ANALOG_ALPHA) toward {-1, 0, +1} — soft ramp on zone
//            transitions so proportional control ramps instead of stepping sharply.
static uint32_t buf_pos_last_ms = 0;

static void buf_sensor_tick(uint32_t now_ms) {
    bool do_pos = (now_ms - buf_pos_last_ms) >= (uint32_t)SYNC_TICK_MS;
    if (do_pos) buf_pos_last_ms = now_ms;

    // Analog: update g_buf_pos BEFORE buf_read_stable() so buf_read() uses the
    // current ADC value to classify the zone this tick.
    if (BUF_SENSOR_TYPE == 1 && do_pos) buf_analog_update();

    buf_state_t prev = g_buf.state;
    buf_state_t s = buf_read_stable(now_ms);
    if (s != prev) {
        buf_update(s, now_ms);
        sync_on_transition(prev, s);
    }

    // Endstop: update g_buf_pos AFTER state is committed so the EMA target is
    // the freshly-settled zone, not the previous one.
    if (BUF_SENSOR_TYPE == 0 && do_pos) {
        float target = (g_buf.state == BUF_ADVANCE) ?  1.0f :
                       (g_buf.state == BUF_TRAILING) ? -1.0f : 0.0f;
        g_buf_pos = BUF_ANALOG_ALPHA * target + (1.0f - BUF_ANALOG_ALPHA) * g_buf_pos;
        // After TRAILING resolves to MID, negative EMA lag would set a
        // sub-baseline target and delay recovery.  Clamp to zero so SYNC_UP
        // alone controls how fast the motor returns to speed.
        // Positive lag (ADVANCE→MID) is kept: it produces a smooth deceleration.
        if (g_buf.state == BUF_MID && g_buf_pos < 0.0f) g_buf_pos = 0.0f;
    }
}

static void sync_tick(uint32_t now_ms) {
    lane_t *A = lane_ptr(active_lane);
    if (!A || tc_state() != TC_IDLE) return;

    buf_state_t s = g_buf.state;

    // 1. Automated Start (RELOAD_MODE=1)
    // Auto-enable sync if buffer is pulled and we are in AUTO_MODE
    if (AUTO_MODE && !sync_enabled && s == BUF_ADVANCE) {
        sync_enabled = true;
        sync_auto_started = true;
        sync_idle_since_ms = 0;
        cmd_event("SYNC", "AUTO_START");
    }

    if (!sync_enabled) return;

    // 2. Automated Stop (if auto-started)
    if (sync_auto_started) {
        if (s == BUF_ADVANCE || s == BUF_MID) {
            sync_idle_since_ms = 0;
        } else {
            if (sync_idle_since_ms == 0) sync_idle_since_ms = now_ms;
            if (SYNC_AUTO_STOP_MS > 0 && (now_ms - sync_idle_since_ms) > (uint32_t)SYNC_AUTO_STOP_MS) {
                sync_enabled = false;
                sync_auto_started = false;
                sync_current_sps = 0;
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

    // Proportional speed control.
    // g_buf_pos: +1 = ADVANCE (extruder pulling ahead, speed up MMU),
    //            -1 = TRAILING (buffer filling, slow down MMU).
    // Both sensor modes produce the same [-1, +1] range via EMA in buf_sensor_tick.
    float buf_pos = g_buf_pos;

    if (s == BUF_TRAILING) {
        // Pause syncing when pushing against the wall, wait for neutral.
        // MMU sync differs from RELOAD follow here: we prefer a full stop to maintain position.
        sync_current_sps = 0;
    } else {
        float correction = (float)SYNC_KP_SPS * buf_pos;
        if (predict_advance_coming()) correction += (float)PRE_RAMP_SPS;

        int base_target = clamp_i(g_baseline_sps + (int)correction, SYNC_MIN_SPS, SYNC_MAX_SPS);

        // Common scaling (Analog or SG)
        // StallGuard sync doesn't need 50Hz updates. 10Hz (100ms) is plenty.
        if ((now_ms - sync_last_sg_ms) >= 100u) {
            sync_last_sg_ms = now_ms;
            sg_ma_update(A);
        }
        int target = sync_apply_scaling(A, base_target, SYNC_SG_INTERP);

        if (sync_current_sps > target) sync_current_sps -= SYNC_RAMP_DN_SPS;
        else if (sync_current_sps < target) sync_current_sps += SYNC_RAMP_UP_SPS;
        sync_current_sps = clamp_i(sync_current_sps, SYNC_MIN_SPS, SYNC_MAX_SPS);
    }

    sync_apply_to_active();

    if ((now_ms - sync_last_evt_ms) >= 500u) {
        sync_last_evt_ms = now_ms;
        char ev[48];
        snprintf(ev, sizeof(ev), "%s,%.1f,%.2f",
                 buf_state_name(s),
                 (double)sps_to_mm_per_min(sync_current_sps),
                 (double)buf_pos);
        cmd_event("BS", ev);
    }
}

// ===================== Stall IRQ + pump =====================
static void lane_fault(lane_t *L, fault_t f) {
    motor_stop(&L->m);
    L->task = TASK_IDLE;
    L->fault = f;
    L->stall_armed = false;
}

static void __not_in_flash_func(stall_irq)(uint gpio, uint32_t events) {
    if (!(events & GPIO_IRQ_EDGE_RISE)) return;

    if (gpio == PIN_L1_DIAG && g_lane_l1.stall_armed) {
        motor_stop(&g_lane_l1.m);
        stall_pending_l1 = true;
    }
    if (gpio == PIN_L2_DIAG && g_lane_l2.stall_armed) {
        motor_stop(&g_lane_l2.m);
        stall_pending_l2 = true;
    }
}

static void stall_init(void) {
    // Pins already configured by tmc_init (PIO RX). Just enable IRQ.
    gpio_set_irq_enabled_with_callback(PIN_L1_DIAG, GPIO_IRQ_EDGE_RISE, true, &stall_irq);
    gpio_set_irq_enabled(PIN_L2_DIAG, GPIO_IRQ_EDGE_RISE, true);
}

static void stall_handle(lane_t *L, const char *lane_s) {
    if (sync_enabled && !L->stall_recovery && STALL_RECOVERY_MS > 0) {
        // During sync a stall most likely means tension spike, not a jam.
        // Let the motor stop briefly (already done in IRQ), then let sync_tick
        // ramp back up.  If stall fires again within STALL_RECOVERY_MS it's a
        // real jam and the else-branch below will hard-stop.
        L->stall_recovery = true;
        L->stall_recovery_deadline_ms = g_now_ms + (uint32_t)STALL_RECOVERY_MS;
        sync_current_sps = 0;  // ramp from zero; sync_tick restarts the motor
        cmd_event("STALL", lane_s);
    } else {
        lane_fault(L, FAULT_STALL);
        cmd_event("STALL", lane_s);
    }
}

static void stall_pump(void) {
    if (stall_pending_l1) {
        stall_pending_l1 = false;
        stall_handle(&g_lane_l1, "1");
    }
    if (stall_pending_l2) {
        stall_pending_l2 = false;
        stall_handle(&g_lane_l2, "2");
    }
}

// ===================== Settings persistence =====================
#define SETTINGS_FLASH_OFFSET (PICO_FLASH_SIZE_BYTES - FLASH_SECTOR_SIZE)
#define SETTINGS_MAGIC 0x4E314F57u // 'N1OW' - NOSF settings sentinel.
#define SETTINGS_VERSION 34u

typedef struct {
    uint32_t magic;
    uint32_t version;

    int feed_sps, rev_sps, auto_sps;
    int sync_max_sps, sync_min_sps;
    int sync_ramp_up, sync_ramp_dn;
    int sync_tick_ms, pre_ramp_sps;
    int sync_auto_stop_ms;
    int load_max_mm;
    int unload_max_mm;
    int reload_y_timeout_ms;
    int autoload_max_mm;
    int auto_mode;
    int cutter_settle_ms;
    int dist_in_out, dist_out_y, dist_y_buf, buf_body_len, buf_size_mm;
    float buf_half_travel_mm;
    int buf_hyst_ms, buf_predict_thr_ms;
    float baseline_alpha;
    int autoload_retract_mm;

    int motion_startup_ms;
    int sgthrs[NUM_LANES];
    int tcoolthrs[NUM_LANES];
    int sg_current_ma[NUM_LANES];

    int servo_open_us, servo_close_us, servo_block_us;
    int servo_settle_ms;
    int cut_feed_mm, cut_length_mm, cut_amount;

    int tc_timeout_cut_ms;
    int tc_timeout_th_ms;
    int tc_timeout_y_ms;

    int runout_cooldown_ms;

    int ramp_step_sps, ramp_tick_ms;
    int stall_recovery_ms;

    int buf_sensor_type;
    float buf_neutral, buf_range, buf_thr, buf_analog_alpha;
    int sync_kp_sps;
    int ts_buf_fallback_ms;

    int join_sps;
    int press_sps;
    int trailing_sps;
    int sg_deriv[NUM_LANES];
    float sg_target[NUM_LANES];
    int follow_timeout_ms[NUM_LANES];

    // Grouped booleans — packed together to avoid per-field padding.
    bool buf_invert;
    bool auto_preload;
    bool enable_cutter;
    bool reload_mode;
    bool sync_sg_interp;
    bool reload_sg_interp;

    float tmc_rotation_distance[NUM_LANES];
    float tmc_gear_ratio[NUM_LANES];
    int tmc_full_steps[NUM_LANES];
    int tmc_microsteps[NUM_LANES];
    int tmc_tbl[NUM_LANES], tmc_toff[NUM_LANES], tmc_hstrt[NUM_LANES], tmc_hend[NUM_LANES];
    bool tmc_interpolate[NUM_LANES];
    bool tmc_spreadcycle[NUM_LANES];
    int tmc_run_current_ma[NUM_LANES], tmc_hold_current_ma[NUM_LANES];

    uint32_t crc32;
} settings_t;
_Static_assert(sizeof(settings_t) <= 512,
               "settings_t exceeds two flash pages - expand buffer in settings_save()");

static uint32_t crc32_buf(const uint8_t *data, size_t len) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++) {
            uint32_t mask = -(crc & 1u);
            crc = (crc >> 1) ^ (0xEDB88320u & mask);
        }
    }
    return ~crc;
}

static void settings_defaults(void) {
    FEED_SPS = CONF_FEED_SPS;
    REV_SPS = CONF_REV_SPS;
    AUTO_SPS = CONF_AUTO_SPS;

    SYNC_MAX_SPS = CONF_SYNC_MAX_SPS;
    SYNC_MIN_SPS = CONF_SYNC_MIN_SPS;
    SYNC_RAMP_UP_SPS = CONF_SYNC_RAMP_UP_SPS;
    SYNC_RAMP_DN_SPS = CONF_SYNC_RAMP_DN_SPS;
    SYNC_TICK_MS = CONF_SYNC_TICK_MS;
    PRE_RAMP_SPS = CONF_PRE_RAMP_SPS;
    SYNC_AUTO_STOP_MS = 2000;
    AUTOLOAD_MAX_MM = CONF_AUTOLOAD_MAX_MM;
    LOAD_MAX_MM = CONF_LOAD_MAX_MM;
    UNLOAD_MAX_MM = CONF_UNLOAD_MAX_MM;
    RELOAD_Y_TIMEOUT_MS = CONF_RELOAD_Y_TIMEOUT_MS;
    AUTO_MODE = 1;
    AUTO_PRELOAD = true;
    DIST_IN_OUT = CONF_DIST_IN_OUT;
    DIST_OUT_Y  = CONF_DIST_OUT_Y;
    DIST_Y_BUF  = CONF_DIST_Y_BUF;
    BUF_BODY_LEN = CONF_BUF_BODY_LEN;
    BUF_SIZE_MM = CONF_BUF_SIZE_MM;
    BUF_HALF_TRAVEL_MM = (float)BUF_SIZE_MM / 2.0f;
    BUF_HYST_MS = CONF_BUF_HYST_MS;
    BUF_PREDICT_THR_MS = CONF_BUF_PREDICT_THR_MS;
    g_baseline_sps   = CONF_BASELINE_SPS;
    g_baseline_alpha = CONF_BASELINE_ALPHA;
    BUF_INVERT = false;
    AUTO_PRELOAD = true;
    AUTOLOAD_RETRACT_MM = 10;
    ENABLE_CUTTER = false;
    STALL_RECOVERY_MS = CONF_STALL_RECOVERY_MS;

    MOTION_STARTUP_MS = CONF_MOTION_STARTUP_MS;
    for (int i = 0; i < NUM_LANES; i++) {
        SG_DERIV[i] = (i == 0) ? CONF_L1_SG_DERIV : CONF_L2_SG_DERIV;
        SG_TARGET[i] = (i == 0) ? CONF_L1_SG_TARGET : CONF_L2_SG_TARGET;
        FOLLOW_TIMEOUT_MS[i] = (i == 0) ? CONF_L1_FOLLOW_TIMEOUT_MS : CONF_L2_FOLLOW_TIMEOUT_MS;
        TMC_SGTHRS[i] = (i == 0) ? CONF_L1_SGTHRS : CONF_L2_SGTHRS;
        TMC_TCOOLTHRS[i] = (i == 0) ? CONF_L1_TCOOLTHRS : CONF_L2_TCOOLTHRS;
        SG_CURRENT_MA[i] = (i == 0) ? CONF_L1_SG_CURRENT_MA : CONF_L2_SG_CURRENT_MA;
        TMC_RUN_CURRENT_MA[i] = (i == 0) ? CONF_L1_RUN_CURRENT_MA : CONF_L2_RUN_CURRENT_MA;
        TMC_HOLD_CURRENT_MA[i] = (i == 0) ? CONF_L1_HOLD_CURRENT_MA : CONF_L2_HOLD_CURRENT_MA;
        TMC_MICROSTEPS[i] = (i == 0) ? CONF_L1_MICROSTEPS : CONF_L2_MICROSTEPS;
        TMC_SPREADCYCLE[i] = (i == 0) ? CONF_L1_SPREADCYCLE : CONF_L2_SPREADCYCLE;
    }

    SERVO_OPEN_US = CONF_SERVO_OPEN_US;
    SERVO_CLOSE_US = CONF_SERVO_CLOSE_US;
    SERVO_BLOCK_US = CONF_SERVO_BLOCK_US;
    SERVO_SETTLE_MS = CONF_SERVO_SETTLE_MS;
    CUT_FEED_MM = CONF_CUT_FEED_MM;
    CUT_LENGTH_MM = CONF_CUT_LENGTH_MM;
    CUT_AMOUNT = CONF_CUT_AMOUNT;

    TC_TIMEOUT_CUT_MS = CONF_TC_TIMEOUT_CUT_MS;
    TC_TIMEOUT_TH_MS = CONF_TC_TIMEOUT_TH_MS;
    TC_TIMEOUT_Y_MS = CONF_TC_TIMEOUT_Y_MS;

    RUNOUT_COOLDOWN_MS = CONF_RUNOUT_COOLDOWN_MS;

    RAMP_STEP_SPS = CONF_RAMP_STEP_SPS;
    RAMP_TICK_MS = CONF_RAMP_TICK_MS;


    BUF_SENSOR_TYPE = CONF_BUF_SENSOR_TYPE;
    BUF_NEUTRAL = CONF_BUF_NEUTRAL;
    BUF_RANGE = CONF_BUF_RANGE;
    BUF_THR = CONF_BUF_THR;
    BUF_ANALOG_ALPHA = CONF_BUF_ANALOG_ALPHA;
    SYNC_KP_SPS = CONF_SYNC_KP_SPS;
    TS_BUF_FALLBACK_MS = CONF_TS_BUF_FALLBACK_MS;

    MM_PER_STEP[0] = CONF_L1_MM_PER_STEP;
    MM_PER_STEP[1] = CONF_L2_MM_PER_STEP;

    for (int i = 0; i < NUM_LANES; i++) {
        SG_DERIV[i] = (i == 0) ? CONF_L1_SG_DERIV : CONF_L2_SG_DERIV;
        SG_TARGET[i] = (i == 0) ? CONF_L1_SG_TARGET : CONF_L2_SG_TARGET;
        FOLLOW_TIMEOUT_MS[i] = (i == 0) ? CONF_L1_FOLLOW_TIMEOUT_MS : CONF_L2_FOLLOW_TIMEOUT_MS;
        TMC_SGTHRS[i] = (i == 0) ? CONF_L1_SGTHRS : CONF_L2_SGTHRS;
        TMC_TCOOLTHRS[i] = (i == 0) ? CONF_L1_TCOOLTHRS : CONF_L2_TCOOLTHRS;
        SG_CURRENT_MA[i] = (i == 0) ? CONF_L1_SG_CURRENT_MA : CONF_L2_SG_CURRENT_MA;
    }
    SYNC_SG_INTERP = CONF_SYNC_SG_INTERP;
    RELOAD_SG_INTERP = CONF_RELOAD_SG_INTERP;

    TMC_ROTATION_DISTANCE[0] = CONF_L1_ROTATION_DISTANCE;
    TMC_ROTATION_DISTANCE[1] = CONF_L2_ROTATION_DISTANCE;
    TMC_GEAR_RATIO[0] = CONF_L1_GEAR_RATIO;
    TMC_GEAR_RATIO[1] = CONF_L2_GEAR_RATIO;
    TMC_FULL_STEPS[0] = CONF_L1_FULL_STEPS;
    TMC_FULL_STEPS[1] = CONF_L2_FULL_STEPS;
    TMC_MICROSTEPS[0] = CONF_L1_MICROSTEPS;
    TMC_MICROSTEPS[1] = CONF_L2_MICROSTEPS;
    TMC_TBL[0] = CONF_L1_TBL;
    TMC_TBL[1] = CONF_L2_TBL;
    TMC_TOFF[0] = CONF_L1_TOFF;
    TMC_TOFF[1] = CONF_L2_TOFF;
    TMC_HSTRT[0] = CONF_L1_HSTRT;
    TMC_HSTRT[1] = CONF_L2_HSTRT;
    TMC_HEND[0] = CONF_L1_HEND;
    TMC_HEND[1] = CONF_L2_HEND;
    TMC_INTERPOLATE[0] = CONF_L1_INTPOL;
    TMC_INTERPOLATE[1] = CONF_L2_INTPOL;
    TMC_SPREADCYCLE[0] = CONF_L1_SPREADCYCLE;
    TMC_SPREADCYCLE[1] = CONF_L2_SPREADCYCLE;
    TMC_RUN_CURRENT_MA[0] = CONF_L1_RUN_CURRENT_MA;
    TMC_RUN_CURRENT_MA[1] = CONF_L2_RUN_CURRENT_MA;
    TMC_HOLD_CURRENT_MA[0] = CONF_L1_HOLD_CURRENT_MA;
    TMC_HOLD_CURRENT_MA[1] = CONF_L2_HOLD_CURRENT_MA;

    MM_PER_STEP[0] = CONF_L1_MM_PER_STEP;
    MM_PER_STEP[1] = CONF_L2_MM_PER_STEP;
}

static void settings_save(void) {
    settings_t s = {0};
    s.magic = SETTINGS_MAGIC;
    s.version = SETTINGS_VERSION;

    s.feed_sps = FEED_SPS;
    s.rev_sps = REV_SPS;
    s.auto_sps = AUTO_SPS;

    s.sync_max_sps = SYNC_MAX_SPS;
    s.sync_min_sps = SYNC_MIN_SPS;
    s.sync_ramp_up = SYNC_RAMP_UP_SPS;
    s.sync_ramp_dn = SYNC_RAMP_DN_SPS;
    s.sync_tick_ms = SYNC_TICK_MS;
    s.pre_ramp_sps = PRE_RAMP_SPS;
    s.sync_auto_stop_ms = SYNC_AUTO_STOP_MS;
    s.autoload_max_mm = AUTOLOAD_MAX_MM;
    s.load_max_mm = LOAD_MAX_MM;
    s.unload_max_mm = UNLOAD_MAX_MM;
    s.reload_y_timeout_ms = RELOAD_Y_TIMEOUT_MS;
    s.auto_mode = AUTO_MODE;
    s.auto_preload = AUTO_PRELOAD ? 1 : 0;
    s.buf_half_travel_mm = BUF_HALF_TRAVEL_MM;
    s.dist_in_out = DIST_IN_OUT;
    s.dist_out_y = DIST_OUT_Y;
    s.dist_y_buf = DIST_Y_BUF;
    s.buf_body_len = BUF_BODY_LEN;
    s.buf_size_mm = BUF_SIZE_MM;
    s.buf_hyst_ms = BUF_HYST_MS;
    s.buf_predict_thr_ms = BUF_PREDICT_THR_MS;
    s.baseline_alpha = g_baseline_alpha;
    s.buf_invert = BUF_INVERT;
    s.auto_preload = AUTO_PRELOAD;
    s.autoload_retract_mm = AUTOLOAD_RETRACT_MM;
    s.enable_cutter = ENABLE_CUTTER;

    for (int i = 0; i < NUM_LANES; i++) {
        s.sgthrs[i] = TMC_SGTHRS[i];
        s.tcoolthrs[i] = TMC_TCOOLTHRS[i];
        s.sg_current_ma[i] = SG_CURRENT_MA[i];
    }

    s.servo_open_us = SERVO_OPEN_US;
    s.servo_close_us = SERVO_CLOSE_US;
    s.servo_block_us = SERVO_BLOCK_US;
    s.servo_settle_ms = SERVO_SETTLE_MS;
    s.cut_feed_mm = CUT_FEED_MM;
    s.cut_length_mm = CUT_LENGTH_MM;
    s.cut_amount = CUT_AMOUNT;

    s.tc_timeout_cut_ms = TC_TIMEOUT_CUT_MS;
    s.tc_timeout_th_ms = TC_TIMEOUT_TH_MS;
    s.tc_timeout_y_ms = TC_TIMEOUT_Y_MS;

    s.runout_cooldown_ms = RUNOUT_COOLDOWN_MS;

    s.ramp_step_sps = RAMP_STEP_SPS;
    s.ramp_tick_ms = RAMP_TICK_MS;
    s.stall_recovery_ms = STALL_RECOVERY_MS;


    s.buf_sensor_type = BUF_SENSOR_TYPE;
    s.buf_neutral = BUF_NEUTRAL;
    s.buf_range = BUF_RANGE;
    s.buf_thr = BUF_THR;
    s.buf_analog_alpha = BUF_ANALOG_ALPHA;
    s.sync_kp_sps = SYNC_KP_SPS;
    s.ts_buf_fallback_ms = TS_BUF_FALLBACK_MS;

    s.reload_mode = (bool)RELOAD_MODE;
    s.cutter_settle_ms = CUT_TIMEOUT_SETTLE_MS;
    for (int i = 0; i < NUM_LANES; i++) {
        s.sg_deriv[i] = SG_DERIV[i];
        s.sg_target[i] = SG_TARGET[i];
        s.follow_timeout_ms[i] = FOLLOW_TIMEOUT_MS[i];
        s.sgthrs[i] = TMC_SGTHRS[i];
        s.tcoolthrs[i] = TMC_TCOOLTHRS[i];
        s.sg_current_ma[i] = SG_CURRENT_MA[i];
    }

    s.sync_sg_interp = SYNC_SG_INTERP;
    s.reload_sg_interp = RELOAD_SG_INTERP;

    for (int i = 0; i < NUM_LANES; i++) {
        s.tmc_rotation_distance[i] = TMC_ROTATION_DISTANCE[i];
        s.tmc_gear_ratio[i] = TMC_GEAR_RATIO[i];
        s.tmc_full_steps[i] = TMC_FULL_STEPS[i];
        s.tmc_microsteps[i] = TMC_MICROSTEPS[i];
        s.tmc_tbl[i] = TMC_TBL[i];
        s.tmc_toff[i] = TMC_TOFF[i];
        s.tmc_hstrt[i] = TMC_HSTRT[i];
        s.tmc_hend[i] = TMC_HEND[i];
        s.tmc_interpolate[i] = TMC_INTERPOLATE[i];
        s.tmc_spreadcycle[i] = TMC_SPREADCYCLE[i];
        s.tmc_run_current_ma[i] = TMC_RUN_CURRENT_MA[i];
        s.tmc_hold_current_ma[i] = TMC_HOLD_CURRENT_MA[i];
    }

    s.crc32 = crc32_buf((const uint8_t *)&s, offsetof(settings_t, crc32));

    uint8_t buffer[512] = {0};
    memcpy(buffer, &s, sizeof(s));

    stop_all();

    uint32_t ints = save_and_disable_interrupts();
    flash_range_erase(SETTINGS_FLASH_OFFSET, FLASH_SECTOR_SIZE);
    flash_range_program(SETTINGS_FLASH_OFFSET, buffer, 512);
    restore_interrupts(ints);
}

static void sync_tmc_settings(int lane) {
    int idx = lane_to_idx(lane);
    tmc_t *t = (lane == 1) ? &g_tmc_l1 : &g_tmc_l2;

    // Recalculate MM_PER_STEP for this lane
    MM_PER_STEP[idx] = TMC_ROTATION_DISTANCE[idx] / (float)(TMC_FULL_STEPS[idx] * TMC_GEAR_RATIO[idx] * TMC_MICROSTEPS[idx]);

    // Re-setup CHOPCONF for this driver
    tmc_setup_chopconf(t, TMC_MICROSTEPS[idx], TMC_TOFF[idx], TMC_TBL[idx], TMC_HSTRT[idx], TMC_HEND[idx], TMC_INTERPOLATE[idx]);
    tmc_set_spreadcycle(t, TMC_SPREADCYCLE[idx]);
}

static void tmc_apply_all(void) {
    tmc_set_pwmconf(&g_tmc_l1);
    tmc_set_pwmconf(&g_tmc_l2);
    tmc_set_spreadcycle(&g_tmc_l1, TMC_SPREADCYCLE[0]);
    tmc_set_spreadcycle(&g_tmc_l2, TMC_SPREADCYCLE[1]);
    tmc_setup_chopconf(&g_tmc_l1, TMC_MICROSTEPS[0], TMC_TOFF[0], TMC_TBL[0], TMC_HSTRT[0], TMC_HEND[0], TMC_INTERPOLATE[0]);
    tmc_setup_chopconf(&g_tmc_l2, TMC_MICROSTEPS[1], TMC_TOFF[1], TMC_TBL[1], TMC_HSTRT[1], TMC_HEND[1], TMC_INTERPOLATE[1]);
    tmc_set_run_current_ma(&g_tmc_l1, TMC_RUN_CURRENT_MA[0], TMC_HOLD_CURRENT_MA[0]);
    tmc_set_run_current_ma(&g_tmc_l2, TMC_RUN_CURRENT_MA[1], TMC_HOLD_CURRENT_MA[1]);
    tmc_set_tcoolthrs(&g_tmc_l1, (uint32_t)TMC_TCOOLTHRS[0]);
    tmc_set_tcoolthrs(&g_tmc_l2, (uint32_t)TMC_TCOOLTHRS[1]);
    tmc_set_sgthrs(&g_tmc_l1, (uint8_t)TMC_SGTHRS[0]);
    tmc_set_sgthrs(&g_tmc_l2, (uint8_t)TMC_SGTHRS[1]);

    // Firmware defaults configure VSENSE=1 in CHOPCONF, so seed shadow accordingly.
    g_shadow_vsense[0] = true;
    g_shadow_vsense[1] = true;
    g_shadow_ihold_irun[0] = build_ihold_irun_reg(TMC_RUN_CURRENT_MA[0], TMC_HOLD_CURRENT_MA[0], true);
    g_shadow_ihold_irun[1] = build_ihold_irun_reg(TMC_RUN_CURRENT_MA[1], TMC_HOLD_CURRENT_MA[1], true);
    g_shadow_ihold_irun_valid[0] = true;
    g_shadow_ihold_irun_valid[1] = true;
}

static void settings_load(void) {
    const settings_t *s = (const settings_t *)(XIP_BASE + SETTINGS_FLASH_OFFSET);

    if (s->magic != SETTINGS_MAGIC || s->version != SETTINGS_VERSION) {
        settings_defaults();
        tmc_apply_all();
        return;
    }

    uint32_t crc = crc32_buf((const uint8_t *)s, offsetof(settings_t, crc32));
    if (crc != s->crc32) {
        settings_defaults();
        tmc_apply_all();
        return;
    }

    FEED_SPS = s->feed_sps;
    REV_SPS = s->rev_sps;
    AUTO_SPS = s->auto_sps;

    SYNC_MAX_SPS = s->sync_max_sps;
    SYNC_MIN_SPS = s->sync_min_sps;
    SYNC_RAMP_UP_SPS = s->sync_ramp_up;
    SYNC_RAMP_DN_SPS = s->sync_ramp_dn;
    SYNC_TICK_MS = s->sync_tick_ms;
    PRE_RAMP_SPS = s->pre_ramp_sps;
    SYNC_AUTO_STOP_MS = s->sync_auto_stop_ms;
    AUTOLOAD_MAX_MM = s->autoload_max_mm;
    LOAD_MAX_MM = s->load_max_mm;
    UNLOAD_MAX_MM = s->unload_max_mm;
    RELOAD_Y_TIMEOUT_MS = s->reload_y_timeout_ms;
    AUTO_MODE = s->auto_mode;
    AUTO_PRELOAD = (s->auto_preload != 0);
    BUF_HALF_TRAVEL_MM = s->buf_half_travel_mm;
    DIST_IN_OUT = s->dist_in_out;
    DIST_OUT_Y = s->dist_out_y;
    DIST_Y_BUF = s->dist_y_buf;
    BUF_BODY_LEN = s->buf_body_len;
    BUF_SIZE_MM = s->buf_size_mm;
    BUF_HYST_MS = s->buf_hyst_ms;
    BUF_PREDICT_THR_MS = s->buf_predict_thr_ms;
    g_baseline_alpha = s->baseline_alpha;
    BUF_INVERT = s->buf_invert;
    AUTO_PRELOAD = s->auto_preload;
    AUTOLOAD_RETRACT_MM = s->autoload_retract_mm;
    ENABLE_CUTTER = s->enable_cutter;

    for (int i = 0; i < NUM_LANES; i++) {
        SG_DERIV[i] = s->sg_deriv[i];
        SG_TARGET[i] = s->sg_target[i];
        FOLLOW_TIMEOUT_MS[i] = s->follow_timeout_ms[i];
        TMC_SGTHRS[i] = s->sgthrs[i];
        TMC_TCOOLTHRS[i] = s->tcoolthrs[i];
        SG_CURRENT_MA[i] = s->sg_current_ma[i];

        TMC_ROTATION_DISTANCE[i] = s->tmc_rotation_distance[i];
        TMC_GEAR_RATIO[i] = s->tmc_gear_ratio[i];
        TMC_FULL_STEPS[i] = s->tmc_full_steps[i];
        TMC_MICROSTEPS[i] = s->tmc_microsteps[i];
        TMC_TBL[i] = s->tmc_tbl[i];
        TMC_TOFF[i] = s->tmc_toff[i];
        TMC_HSTRT[i] = s->tmc_hstrt[i];
        TMC_HEND[i] = s->tmc_hend[i];
        TMC_INTERPOLATE[i] = s->tmc_interpolate[i];
        TMC_SPREADCYCLE[i] = s->tmc_spreadcycle[i];
        TMC_RUN_CURRENT_MA[i] = s->tmc_run_current_ma[i];
        TMC_HOLD_CURRENT_MA[i] = s->tmc_hold_current_ma[i];
        MM_PER_STEP[i] = TMC_ROTATION_DISTANCE[i] / (float)(TMC_FULL_STEPS[i] * TMC_GEAR_RATIO[i] * TMC_MICROSTEPS[i]);
    }

    SERVO_OPEN_US = s->servo_open_us;
    SERVO_CLOSE_US = s->servo_close_us;
    SERVO_BLOCK_US = s->servo_block_us;
    SERVO_SETTLE_MS = s->servo_settle_ms;
    CUT_FEED_MM = s->cut_feed_mm;
    CUT_LENGTH_MM = s->cut_length_mm;
    CUT_AMOUNT = s->cut_amount;

    TC_TIMEOUT_CUT_MS = s->tc_timeout_cut_ms;
    TC_TIMEOUT_TH_MS = s->tc_timeout_th_ms;
    TC_TIMEOUT_Y_MS = s->tc_timeout_y_ms;

    RUNOUT_COOLDOWN_MS = s->runout_cooldown_ms;

    RAMP_STEP_SPS = s->ramp_step_sps;
    RAMP_TICK_MS = s->ramp_tick_ms;
    STALL_RECOVERY_MS = s->stall_recovery_ms;


    BUF_SENSOR_TYPE = s->buf_sensor_type;
    BUF_NEUTRAL = s->buf_neutral;
    BUF_RANGE = s->buf_range;
    BUF_THR = s->buf_thr;
    BUF_ANALOG_ALPHA = s->buf_analog_alpha;
    SYNC_KP_SPS = s->sync_kp_sps;
    TS_BUF_FALLBACK_MS = s->ts_buf_fallback_ms;

    RELOAD_MODE = s->reload_mode ? 1 : 0;
    CUT_TIMEOUT_SETTLE_MS = s->cutter_settle_ms;
    JOIN_SPS = s->join_sps;
    PRESS_SPS = s->press_sps;
    TRAILING_SPS = s->trailing_sps;
    SYNC_SG_INTERP = s->sync_sg_interp;
    RELOAD_SG_INTERP = s->reload_sg_interp;

    for (int i = 0; i < NUM_LANES; i++) {
        SG_DERIV[i] = s->sg_deriv[i];
        SG_TARGET[i] = s->sg_target[i];
        FOLLOW_TIMEOUT_MS[i] = s->follow_timeout_ms[i];
        TMC_SGTHRS[i] = s->sgthrs[i];
        TMC_TCOOLTHRS[i] = s->tcoolthrs[i];
        SG_CURRENT_MA[i] = s->sg_current_ma[i];
        TMC_ROTATION_DISTANCE[i] = s->tmc_rotation_distance[i];
        TMC_GEAR_RATIO[i] = s->tmc_gear_ratio[i];
        TMC_FULL_STEPS[i] = s->tmc_full_steps[i];
        TMC_MICROSTEPS[i] = s->tmc_microsteps[i];
        TMC_TBL[i] = s->tmc_tbl[i];
        TMC_TOFF[i] = s->tmc_toff[i];
        TMC_HSTRT[i] = s->tmc_hstrt[i];
        TMC_HEND[i] = s->tmc_hend[i];
        TMC_INTERPOLATE[i] = s->tmc_interpolate[i];
        TMC_SPREADCYCLE[i] = s->tmc_spreadcycle[i];
        TMC_RUN_CURRENT_MA[i] = s->tmc_run_current_ma[i];
        TMC_HOLD_CURRENT_MA[i] = s->tmc_hold_current_ma[i];
    }

    tmc_apply_all();
}

// ===================== USB protocol =====================
typedef struct {
    char buf[64];
    int pos;
    bool overflow;
} cmd_parser_t;

static cmd_parser_t g_cmd = {0};

static void cmd_reply(const char *status, const char *data) {
    if (data && *data) printf("%s:%s\n", status, data);
    else printf("%s\n", status);
}

static void cmd_event(const char *type, const char *data) {
    if (data && *data) printf("EV:%s:%s\n", type, data);
    else printf("EV:%s\n", type);
}

static void status_dump(void) {
    static uint16_t last_sg1 = 0, last_sg2 = 0;
    static uint32_t last_sg_read_ms = 0;
    uint32_t now = to_ms_since_boot(get_absolute_time());

    if ((now - last_sg_read_ms) >= 100u) {
        last_sg_read_ms = now;
        (void)tmc_read_sg_result(&g_tmc_l1, &last_sg1);
        (void)tmc_read_sg_result(&g_tmc_l2, &last_sg2);
    }
    uint16_t sg1 = last_sg1;
    uint16_t sg2 = last_sg2;

    char b[256];
    snprintf(b, sizeof(b),
        "LN:%d,TC:%s,L1T:%s,L2T:%s,"
        "I1:%d,O1:%d,I2:%d,O2:%d,"
        "TH:%d,YS:%d,BUF:%s,SPS:%.1f,BL:%.1f,BP:%.2f,SM:%d,BI:%d,AP:%d,CU:%d,RELOAD:%d,"
        "SG1:%u,SG2:%u,SGF:%d",
        active_lane, tc_state_name(g_tc_ctx.state),
        task_name(g_lane_l1.task), task_name(g_lane_l2.task),
        lane_in_present(&g_lane_l1) ? 1 : 0,
        lane_out_present(&g_lane_l1) ? 1 : 0,
        lane_in_present(&g_lane_l2) ? 1 : 0,
        lane_out_present(&g_lane_l2) ? 1 : 0,
        toolhead_has_filament ? 1 : 0,
        on_al(&g_y_split) ? 1 : 0,
        buf_state_name(g_buf.state),
        (double)sps_to_mm_per_min(sync_current_sps),
        (double)sps_to_mm_per_min(g_baseline_sps),
        (double)g_buf_pos,
        sync_enabled ? 1 : 0,
        BUF_INVERT ? 1 : 0,
        AUTO_PRELOAD ? 1 : 0,
        ENABLE_CUTTER ? 1 : 0,
        RELOAD_MODE,
        sg1,
        sg2,
        (int)g_sg_load);

    cmd_reply("OK", b);
}

static void cmd_execute(const char *cmd, const char *p, uint32_t now_ms) {
    if (!strcmp(cmd, "TC")) {
        int ln = atoi(p);
        if (ln == 1 || ln == 2) {
            if (active_lane != 1 && active_lane != 2) {
                cmd_reply("ER", "NO_ACTIVE_LANE");
                return;
            }
            tc_start(ln, now_ms);
            cmd_reply("OK", NULL);
        } else {
            cmd_reply("ER", "ARG");
        }
    } else if (!strcmp(cmd, "T")) {
        int ln = atoi(p);
        if (ln == 1 || ln == 2) {
            active_lane = ln;
            cmd_reply("OK", NULL);
        } else {
            cmd_reply("ER", "ARG");
        }
    } else if (!strcmp(cmd, "LO")) {
        lane_t *A = lane_ptr(active_lane);
        if (!A) { cmd_reply("ER", "NO_ACTIVE_LANE"); return; }
        lane_start(A, TASK_AUTOLOAD, AUTO_SPS, true, now_ms, (float)AUTOLOAD_MAX_MM);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "UL")) {
        lane_t *A = lane_ptr(active_lane);
        if (!A) { cmd_reply("ER", "NO_ACTIVE_LANE"); return; }
        set_toolhead_filament(false);
        lane_start(A, TASK_UNLOAD, REV_SPS, false, now_ms, (float)UNLOAD_MAX_MM);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "FL")) {
        lane_t *A = lane_ptr(active_lane);
        if (!A) { cmd_reply("ER", "NO_ACTIVE_LANE"); return; }
        if (!lane_in_present(A)) { cmd_reply("ER", "NO_FILAMENT"); return; }
        set_toolhead_filament(false);
        lane_start(A, TASK_LOAD_FULL, FEED_SPS, true, now_ms, (float)LOAD_MAX_MM);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "UM")) {
        lane_t *A = lane_ptr(active_lane);
        if (!A) {
            cmd_reply("ER", "NO_ACTIVE_LANE");
            return;
        }
        set_toolhead_filament(false);
        lane_start(A, TASK_UNLOAD_MMU, REV_SPS, false, now_ms, (float)UNLOAD_MAX_MM);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "FD")) {
        lane_t *A = lane_ptr(active_lane);
        if (!A) {
            cmd_reply("ER", "NO_ACTIVE_LANE");
            return;
        }
        lane_start(A, TASK_FEED, FEED_SPS, true, now_ms, 0);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "FL")) {
        lane_t *A = lane_ptr(active_lane);
        if (!A) {
            cmd_reply("ER", "NO_ACTIVE_LANE");
            return;
        }
        if (!lane_in_present(A)) {
            cmd_reply("ER", "NO_FILAMENT");
            return;
        }
        lane_t *other = lane_ptr(other_lane(active_lane));
        if (other && lane_out_present(other) && other->task == TASK_IDLE) {
            cmd_reply("ER", "OTHER_LANE_ACTIVE");
            return;
        }
        set_toolhead_filament(false);
        lane_start(A, TASK_LOAD_FULL, FEED_SPS, true, now_ms, (float)LOAD_MAX_MM);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "CU")) {
        if (!ENABLE_CUTTER) {
            cmd_reply("ER", "CUTTER_DISABLED");
            return;
        }
        lane_t *A = lane_ptr(active_lane);
        if (!A) {
            cmd_reply("ER", "NO_ACTIVE_LANE");
            return;
        }
        cutter_start(A, now_ms);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "MV")) {
        lane_t *A = lane_ptr(active_lane);
        if (!A) {
            cmd_reply("ER", "NO_ACTIVE_LANE");
            return;
        }
        float mm = 0.0f;
        float feed_mm_min = 0.0f;
        if (sscanf(p, "%f:%f", &mm, &feed_mm_min) != 2 || feed_mm_min <= 0.0f) {
            cmd_reply("ER", "ARG");
            return;
        }
        int idx = lane_to_idx(active_lane);
        int sps = (int)(feed_mm_min / 60.0f / MM_PER_STEP[idx] + 0.5f);
        sps = clamp_i(sps, 200, 50000);
        bool forward = (mm >= 0.0f);
        float limit = mm < 0.0f ? -mm : mm;
        sync_enabled = false;
        lane_start(A, TASK_MOVE, sps, forward, now_ms, limit);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "ST")) {
        tc_abort();
        cutter_abort();
        stop_all();
        set_toolhead_filament(false);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "TS")) {
        int v = atoi(p);
        if (v == 0 || v == 1) {
            set_toolhead_filament(v == 1);
            cmd_reply("OK", NULL);
        } else {
            cmd_reply("ER", "ARG");
        }
    } else if (!strcmp(cmd, "SM")) {
        int v = atoi(p);
        if (v == 0 || v == 1) {
            sync_enabled = (v == 1);
            sync_auto_started = false; // Host control overrides auto-stop
            if (v == 0) sync_current_sps = 0;
            cmd_reply("OK", NULL);
        } else {
            cmd_reply("ER", "ARG");
        }
    } else if (!strcmp(cmd, "BI")) {
        int v = atoi(p);
        if (v == 0 || v == 1) {
            BUF_INVERT = (v == 1);
            cmd_reply("OK", NULL);
        } else {
            cmd_reply("ER", "ARG");
        }
    } else if (!strcmp(cmd, "SG")) {
        int ln = atoi(p);
        if (ln != 1 && ln != 2) {
            cmd_reply("ER", "ARG");
        } else {
            uint16_t sg = 0;
            tmc_t *t = (ln == 1) ? &g_tmc_l1 : &g_tmc_l2;
            if (tmc_read_sg_result(t, &sg)) {
                char out[24];
                snprintf(out, sizeof(out), "%d:%u", ln, sg);
                cmd_reply("OK", out);
            } else {
                cmd_reply("ER", "SG:NO_RESPONSE");
            }
        }
    } else if (!strcmp(cmd, "CA")) {
        int ln = 0;
        int ma = 0;
        if (sscanf(p, "%d:%d", &ln, &ma) == 2 && (ln == 1 || ln == 2) && ma >= 0 && ma <= 2000) {
            tmc_t *t = (ln == 1) ? &g_tmc_l1 : &g_tmc_l2;
            int idx = lane_to_idx(ln);
            if (tmc_set_run_current_ma(t, ma, TMC_HOLD_CURRENT_MA[idx])) {
                TMC_RUN_CURRENT_MA[idx] = ma;
                g_shadow_ihold_irun[idx] = build_ihold_irun_reg(TMC_RUN_CURRENT_MA[idx], TMC_HOLD_CURRENT_MA[idx], g_shadow_vsense[idx]);
                g_shadow_ihold_irun_valid[idx] = true;
                cmd_reply("OK", NULL);
            } else {
                cmd_reply("ER", "CA:NO_RESPONSE");
            }
        } else {
            cmd_reply("ER", "ARG");
        }
    } else if (!strcmp(cmd, "SV")) {
        settings_save();
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "LD")) {
        settings_load();
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "RS")) {
        settings_defaults();
        settings_save();
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "VR")) {
        cmd_reply("OK", CONF_FW_VERSION);
    } else if (!strcmp(cmd, "?")) {
        status_dump();
    } else if (!strcmp(cmd, "SET")) {
        char param[32];
        char val_str[32];
        if (sscanf(p, "%31[^:]:%31s", param, val_str) != 2) {
            cmd_reply("ER", "SET:ARG");
            return;
        }
        int iv = atoi(val_str);
        float fv = (float)atof(val_str);
        bool handled = true;

        // Determine if parameter targets a specific lane via suffix _L1 / _L2
        int lane_mask = 3; // default: both (bits 0 and 1)
        char base_param[32];
        strncpy(base_param, param, 32);
        size_t len = strlen(param);
        if (len > 3 && !strcmp(param + len - 3, "_L1")) {
            lane_mask = 1;
            base_param[len - 3] = '\0';
        } else if (len > 3 && !strcmp(param + len - 3, "_L2")) {
            lane_mask = 2;
            base_param[len - 3] = '\0';
        }

        #define SET_LANE(BLOCK) for(int l=1; l<=NUM_LANES; l++) if(lane_mask & (1<<(l-1))) { int idx=l-1; BLOCK; sync_tmc_settings(l); }

        if (!strcmp(base_param, "FEED_RATE"))    FEED_SPS = clamp_i(mm_per_min_to_sps(fv), 200, 50000);
        else if (!strcmp(base_param, "REV_RATE"))     REV_SPS = clamp_i(mm_per_min_to_sps(fv), 200, 50000);
        else if (!strcmp(base_param, "AUTO_RATE"))    AUTO_SPS = clamp_i(mm_per_min_to_sps(fv), 200, 50000);
        else if (!strcmp(base_param, "SYNC_MAX_RATE")) SYNC_MAX_SPS = clamp_i(mm_per_min_to_sps(fv), 200, 50000);
        else if (!strcmp(base_param, "SYNC_MIN_RATE")) SYNC_MIN_SPS = clamp_i(mm_per_min_to_sps(fv), 0, 50000);
        else if (!strcmp(base_param, "SYNC_UP_RATE"))   SYNC_RAMP_UP_SPS = clamp_i(mm_per_min_to_sps(fv), 1, 50000);
        else if (!strcmp(base_param, "SYNC_DN_RATE"))   SYNC_RAMP_DN_SPS = clamp_i(mm_per_min_to_sps(fv), 1, 50000);
        else if (!strcmp(base_param, "RAMP_STEP_RATE")) RAMP_STEP_SPS = clamp_i(mm_per_min_to_sps(fv), 1, 10000);
        else if (!strcmp(base_param, "PRE_RAMP_RATE"))  PRE_RAMP_SPS = clamp_i(mm_per_min_to_sps(fv), 0, 50000);
        else if (!strcmp(base_param, "BUF_TRAVEL"))   { BUF_HALF_TRAVEL_MM = fv < 1.0f ? 1.0f : fv > 50.0f ? 50.0f : fv; }
        else if (!strcmp(base_param, "BUF_HYST"))     BUF_HYST_MS = clamp_i(iv, 5, 500);
        else if (!strcmp(base_param, "AUTO_PRELOAD")) AUTO_PRELOAD = (iv != 0);
        else if (!strcmp(base_param, "RETRACT_MM"))   AUTOLOAD_RETRACT_MM = clamp_i(iv, 0, 50);
        else if (!strcmp(base_param, "CUTTER"))       ENABLE_CUTTER = (iv != 0);
        else if (!strcmp(base_param, "AUTO_MODE"))       AUTO_MODE = clamp_i(iv, 0, 1);
        else if (!strcmp(base_param, "RELOAD_MODE"))     RELOAD_MODE = (iv != 0) ? 1 : 0;
        else if (!strcmp(base_param, "RELOAD_Y_MS"))      RELOAD_Y_TIMEOUT_MS = clamp_i(iv, 100, 30000);
        else if (!strcmp(base_param, "DIST_IN_OUT"))  DIST_IN_OUT = clamp_i(iv, 10, 5000);
        else if (!strcmp(base_param, "DIST_OUT_Y"))   DIST_OUT_Y = clamp_i(iv, 0, 5000);
        else if (!strcmp(base_param, "DIST_Y_BUF"))   DIST_Y_BUF = clamp_i(iv, 0, 5000);
        else if (!strcmp(base_param, "BUF_BODY_LEN")) BUF_BODY_LEN = clamp_i(iv, 0, 5000);
        else if (!strcmp(base_param, "BUF_SIZE"))     { BUF_SIZE_MM = clamp_i(iv, 5, 1000); BUF_HALF_TRAVEL_MM = (float)BUF_SIZE_MM / 2.0f; }
        else if (!strcmp(base_param, "JOIN_RATE"))     JOIN_SPS = clamp_i(mm_per_min_to_sps(fv), 200, 50000);
        else if (!strcmp(base_param, "PRESS_RATE"))    PRESS_SPS = clamp_i(mm_per_min_to_sps(fv), 200, 50000);
        else if (!strcmp(base_param, "TRAILING_RATE")) TRAILING_SPS = clamp_i(mm_per_min_to_sps(fv), 10, 10000);
        else if (!strcmp(base_param, "RUN_CURRENT_MA")) { SET_LANE({ TMC_RUN_CURRENT_MA[idx] = clamp_i(iv, 0, 2000); }); }
        else if (!strcmp(base_param, "HOLD_CURRENT_MA")) { SET_LANE({ TMC_HOLD_CURRENT_MA[idx] = clamp_i(iv, 0, 2000); }); }
        else if (!strcmp(base_param, "MICROSTEPS")) { SET_LANE({ TMC_MICROSTEPS[idx] = clamp_i(iv, 1, 256); }); }
        else if (!strcmp(base_param, "ROTATION_DIST")) { SET_LANE({ TMC_ROTATION_DISTANCE[idx] = clamp_f(fv, 0.1, 1000.0); }); }
        else if (!strcmp(base_param, "GEAR_RATIO")) { SET_LANE({ TMC_GEAR_RATIO[idx] = clamp_f(fv, 0.001, 1000.0); }); }
        else if (!strcmp(base_param, "FULL_STEPS")) { SET_LANE({ TMC_FULL_STEPS[idx] = (iv == 400 ? 400 : 200); }); }
        else if (!strcmp(base_param, "INTERPOLATE")) { SET_LANE({ TMC_INTERPOLATE[idx] = (iv != 0); }); }
        else if (!strcmp(base_param, "STEALTHCHOP")) { SET_LANE({ TMC_SPREADCYCLE[idx] = (iv == 0); }); }
        else if (!strcmp(base_param, "DRIVER_TBL")) { SET_LANE({ TMC_TBL[idx] = clamp_i(iv, 0, 3); }); }
        else if (!strcmp(base_param, "DRIVER_TOFF")) { SET_LANE({ TMC_TOFF[idx] = clamp_i(iv, 0, 15); }); }
        else if (!strcmp(base_param, "DRIVER_HSTRT")) { SET_LANE({ TMC_HSTRT[idx] = clamp_i(iv, 0, 7); }); }
        else if (!strcmp(base_param, "DRIVER_HEND")) { SET_LANE({ TMC_HEND[idx] = clamp_i(iv, -3, 12); }); }
        else if (!strcmp(base_param, "SG_DERIV")) { SET_LANE({ SG_DERIV[idx] = clamp_i(iv, 0, 1000); }); }
        else if (!strcmp(base_param, "SG_TARGET")) { SET_LANE({ SG_TARGET[idx] = clamp_f(fv, 0.0f, 1023.0f); }); }
        else if (!strcmp(base_param, "FOLLOW_MS")) { SET_LANE({ FOLLOW_TIMEOUT_MS[idx] = clamp_i(iv, 1000, 60000); }); }
        else if (!strcmp(base_param, "BASELINE_RATE"))    g_baseline_sps = clamp_i(mm_per_min_to_sps(fv), 200, 50000);
        else if (!strcmp(base_param, "BUF_SENSOR"))   BUF_SENSOR_TYPE = clamp_i(iv, 0, 1);
        else if (!strcmp(base_param, "BUF_NEUTRAL"))  BUF_NEUTRAL = clamp_f(fv, 0.0f, 1.0f);
        else if (!strcmp(base_param, "BUF_RANGE"))    BUF_RANGE = clamp_f(fv, 0.01f, 0.5f);
        else if (!strcmp(base_param, "BUF_THR"))      BUF_THR = clamp_f(fv, 0.01f, 0.99f);
        else if (!strcmp(base_param, "BUF_ALPHA"))    BUF_ANALOG_ALPHA = clamp_f(fv, 0.01f, 1.0f);
        else if (!strcmp(base_param, "AUTOLOAD_MAX")) AUTOLOAD_MAX_MM = clamp_i(iv, 10, 10000);
        else if (!strcmp(base_param, "LOAD_MAX"))     LOAD_MAX_MM = clamp_i(iv, 100, 10000);
        else if (!strcmp(base_param, "UNLOAD_MAX"))   UNLOAD_MAX_MM = clamp_i(iv, 100, 10000);
        else if (!strcmp(base_param, "SYNC_KP_RATE")) SYNC_KP_SPS = clamp_i(mm_per_min_to_sps(fv), 0, 50000);
        else if (!strcmp(base_param, "SYNC_AUTO_STOP"))   SYNC_AUTO_STOP_MS = clamp_i(iv, 0, 30000);
        else if (!strcmp(base_param, "TS_BUF_MS"))    TS_BUF_FALLBACK_MS = clamp_i(iv, 0, 30000);
        else if (!strcmp(base_param, "STARTUP_MS"))   MOTION_STARTUP_MS = clamp_i(iv, 0, 30000);
        else if (!strcmp(base_param, "STALL_MS"))     STALL_RECOVERY_MS = clamp_i(iv, 0, 10000);
        else if (!strcmp(base_param, "SYNC_SG_INTERP"))      SYNC_SG_INTERP = (iv != 0);
        else if (!strcmp(base_param, "RELOAD_SG_INTERP"))    RELOAD_SG_INTERP = (iv != 0);
        else if (!strcmp(base_param, "SGTHRS")) {
            SET_LANE({ TMC_SGTHRS[idx] = clamp_i(iv, 0, 255); tmc_set_sgthrs((l==1?&g_tmc_l1:&g_tmc_l2), (uint8_t)TMC_SGTHRS[idx]); });
        }
        else if (!strcmp(base_param, "TCOOLTHRS")) { SET_LANE({ TMC_TCOOLTHRS[idx] = clamp_i(iv, 0, 0xFFFFF); tmc_set_tcoolthrs((l==1?&g_tmc_l1:&g_tmc_l2), (uint32_t)TMC_TCOOLTHRS[idx]); }); }
        else if (!strcmp(base_param, "SG_CURRENT_MA")) { SET_LANE({ SG_CURRENT_MA[idx] = clamp_i(iv, 0, 2000); }); }
        else if (!strcmp(base_param, "SERVO_OPEN"))   SERVO_OPEN_US = clamp_i(iv, 400, 2600);
        else if (!strcmp(base_param, "SERVO_CLOSE"))  SERVO_CLOSE_US = clamp_i(iv, 400, 2600);
        else if (!strcmp(base_param, "SERVO_SETTLE")) SERVO_SETTLE_MS = clamp_i(iv, 100, 2000);
        else if (!strcmp(base_param, "CUT_FEED"))     CUT_FEED_MM = clamp_i(iv, 1, 200);
        else if (!strcmp(base_param, "CUT_LEN"))      CUT_LENGTH_MM = clamp_i(iv, 1, 50);
        else if (!strcmp(base_param, "CUT_AMT"))      CUT_AMOUNT = clamp_i(iv, 1, 5);
        else if (!strcmp(base_param, "TC_CUT_MS"))    TC_TIMEOUT_CUT_MS = clamp_i(iv, 1000, 30000);
        else if (!strcmp(base_param, "TC_TH_MS"))     TC_TIMEOUT_TH_MS = clamp_i(iv, 0, 10000);
        else if (!strcmp(base_param, "TC_Y_MS"))      TC_TIMEOUT_Y_MS = clamp_i(iv, 0, 30000);
        else handled = false;

        if (handled) cmd_reply("OK", NULL);
        else cmd_reply("ER", "SET:UNKNOWN_PARAM");
    } else if (!strcmp(cmd, "GET")) {
        char out[64];
        char param[32];
        int lane_mask = 1; // default to L1 for get
        strncpy(param, p, 32);
        size_t len = strlen(p);
        if (len > 3 && !strcmp(p + len - 3, "_L1")) {
            lane_mask = 1;
            param[len - 3] = '\0';
        } else if (len > 3 && !strcmp(p + len - 3, "_L2")) {
            lane_mask = 2;
            param[len - 3] = '\0';
        }
        int idx = (lane_mask == 2) ? 1 : 0;

        bool handled = true;
        if      (!strcmp(param, "FEED_RATE"))    snprintf(out, sizeof(out), "FEED_RATE:%.1f", (double)sps_to_mm_per_min_idx(FEED_SPS, idx));
        else if (!strcmp(param, "REV_RATE"))     snprintf(out, sizeof(out), "REV_RATE:%.1f", (double)sps_to_mm_per_min_idx(REV_SPS, idx));
        else if (!strcmp(param, "AUTO_RATE"))    snprintf(out, sizeof(out), "AUTO_RATE:%.1f", (double)sps_to_mm_per_min_idx(AUTO_SPS, idx));
        else if (!strcmp(param, "SYNC_MAX_RATE")) snprintf(out, sizeof(out), "SYNC_MAX_RATE:%.1f", (double)sps_to_mm_per_min_idx(SYNC_MAX_SPS, idx));
        else if (!strcmp(param, "SYNC_MIN_RATE")) snprintf(out, sizeof(out), "SYNC_MIN_RATE:%.1f", (double)sps_to_mm_per_min_idx(SYNC_MIN_SPS, idx));
        else if (!strcmp(param, "SYNC_UP_RATE"))   snprintf(out, sizeof(out), "SYNC_UP_RATE:%.1f", (double)sps_to_mm_per_min_idx(SYNC_RAMP_UP_SPS, idx));
        else if (!strcmp(param, "SYNC_DN_RATE"))   snprintf(out, sizeof(out), "SYNC_DN_RATE:%.1f", (double)sps_to_mm_per_min_idx(SYNC_RAMP_DN_SPS, idx));
        else if (!strcmp(param, "RAMP_STEP_RATE")) snprintf(out, sizeof(out), "RAMP_STEP_RATE:%.1f", (double)sps_to_mm_per_min_idx(RAMP_STEP_SPS, idx));
        else if (!strcmp(param, "PRE_RAMP_RATE"))  snprintf(out, sizeof(out), "PRE_RAMP_RATE:%.1f", (double)sps_to_mm_per_min_idx(PRE_RAMP_SPS, idx));
        else if (!strcmp(param, "BUF_TRAVEL"))   snprintf(out, sizeof(out), "BUF_TRAVEL:%.3f", (double)BUF_HALF_TRAVEL_MM);
        else if (!strcmp(param, "BUF_HYST"))     snprintf(out, sizeof(out), "BUF_HYST:%d", BUF_HYST_MS);
        else if (!strcmp(param, "AUTO_PRELOAD")) snprintf(out, sizeof(out), "AUTO_PRELOAD:%d", AUTO_PRELOAD ? 1 : 0);
        else if (!strcmp(param, "RETRACT_MM"))    snprintf(out, sizeof(out), "RETRACT_MM:%d", AUTOLOAD_RETRACT_MM);
        else if (!strcmp(param, "CUTTER"))       snprintf(out, sizeof(out), "CUTTER:%d", ENABLE_CUTTER ? 1 : 0);
        else if (!strcmp(param, "AUTO_MODE"))    snprintf(out, sizeof(out), "AUTO_MODE:%d", AUTO_MODE);
        else if (!strcmp(param, "RELOAD_MODE"))     snprintf(out, sizeof(out), "RELOAD_MODE:%d", RELOAD_MODE);
        else if (!strcmp(param, "RELOAD_Y_MS"))     snprintf(out, sizeof(out), "RELOAD_Y_MS:%d", RELOAD_Y_TIMEOUT_MS);
        else if (!strcmp(param, "DIST_IN_OUT"))     snprintf(out, sizeof(out), "DIST_IN_OUT:%d", DIST_IN_OUT);
        else if (!strcmp(param, "DIST_OUT_Y"))      snprintf(out, sizeof(out), "DIST_OUT_Y:%d", DIST_OUT_Y);
        else if (!strcmp(param, "DIST_Y_BUF"))      snprintf(out, sizeof(out), "DIST_Y_BUF:%d", DIST_Y_BUF);
        else if (!strcmp(param, "BUF_BODY_LEN"))    snprintf(out, sizeof(out), "BUF_BODY_LEN:%d", BUF_BODY_LEN);
        else if (!strcmp(param, "BUF_SIZE"))        snprintf(out, sizeof(out), "BUF_SIZE:%d", BUF_SIZE_MM);
        else if (!strcmp(param, "JOIN_RATE"))     snprintf(out, sizeof(out), "JOIN_RATE:%.1f", (double)sps_to_mm_per_min_idx(JOIN_SPS, idx));
        else if (!strcmp(param, "PRESS_RATE"))    snprintf(out, sizeof(out), "PRESS_RATE:%.1f", (double)sps_to_mm_per_min_idx(PRESS_SPS, idx));
        else if (!strcmp(param, "TRAILING_RATE")) snprintf(out, sizeof(out), "TRAILING_RATE:%.1f", (double)sps_to_mm_per_min_idx(TRAILING_SPS, idx));
        else if (!strcmp(param, "SG_DERIV"))     snprintf(out, sizeof(out), "SG_DERIV:%d", SG_DERIV[idx]);
        else if (!strcmp(param, "SG_TARGET"))    snprintf(out, sizeof(out), "SG_TARGET:%.1f", (double)SG_TARGET[idx]);
        else if (!strcmp(param, "FOLLOW_MS"))    snprintf(out, sizeof(out), "FOLLOW_MS:%d", FOLLOW_TIMEOUT_MS[idx]);
        else if (!strcmp(param, "BASELINE_RATE")) snprintf(out, sizeof(out), "BASELINE_RATE:%.1f", (double)sps_to_mm_per_min_idx(g_baseline_sps, idx));
        else if (!strcmp(param, "BUF_SENSOR"))   snprintf(out, sizeof(out), "BUF_SENSOR:%d", BUF_SENSOR_TYPE);
        else if (!strcmp(param, "BUF_NEUTRAL"))  snprintf(out, sizeof(out), "BUF_NEUTRAL:%.3f", (double)BUF_NEUTRAL);
        else if (!strcmp(param, "BUF_RANGE"))    snprintf(out, sizeof(out), "BUF_RANGE:%.3f", (double)BUF_RANGE);
        else if (!strcmp(param, "BUF_THR"))      snprintf(out, sizeof(out), "BUF_THR:%.3f", (double)BUF_THR);
        else if (!strcmp(param, "BUF_ALPHA"))    snprintf(out, sizeof(out), "BUF_ALPHA:%.3f", (double)BUF_ANALOG_ALPHA);
        else if (!strcmp(param, "AUTOLOAD_MAX")) snprintf(out, sizeof(out), "AUTOLOAD_MAX:%d", AUTOLOAD_MAX_MM);
        else if (!strcmp(param, "LOAD_MAX"))     snprintf(out, sizeof(out), "LOAD_MAX:%d", LOAD_MAX_MM);
        else if (!strcmp(param, "UNLOAD_MAX"))   snprintf(out, sizeof(out), "UNLOAD_MAX:%d", UNLOAD_MAX_MM);
        else if (!strcmp(param, "TC_LOAD_MS"))   snprintf(out, sizeof(out), "TC_LOAD_MS:%d", LOAD_MAX_MM);
        else if (!strcmp(param, "TC_UNLOAD_MS")) snprintf(out, sizeof(out), "TC_UNLOAD_MS:%d", UNLOAD_MAX_MM);
        else if (!strcmp(param, "TC_CUT_MS"))    snprintf(out, sizeof(out), "TC_CUT_MS:%d", TC_TIMEOUT_CUT_MS);
        else if (!strcmp(param, "TC_TH_MS"))     snprintf(out, sizeof(out), "TC_TH_MS:%d", TC_TIMEOUT_TH_MS);
        else if (!strcmp(param, "TC_Y_MS"))      snprintf(out, sizeof(out), "TC_Y_MS:%d", TC_TIMEOUT_Y_MS);
        else if (!strcmp(param, "SYNC_KP_RATE"))     snprintf(out, sizeof(out), "SYNC_KP_RATE:%.1f", (double)sps_to_mm_per_min(SYNC_KP_SPS));
        else if (!strcmp(param, "SYNC_AUTO_STOP"))   snprintf(out, sizeof(out), "SYNC_AUTO_STOP:%d", SYNC_AUTO_STOP_MS);
        else if (!strcmp(param, "TS_BUF_MS"))    snprintf(out, sizeof(out), "TS_BUF_MS:%d", TS_BUF_FALLBACK_MS);
        else if (!strcmp(param, "STARTUP_MS"))   snprintf(out, sizeof(out), "STARTUP_MS:%d", MOTION_STARTUP_MS);
        else if (!strcmp(param, "STALL_MS"))     snprintf(out, sizeof(out), "STALL_MS:%d", STALL_RECOVERY_MS);
        else if (!strcmp(param, "SYNC_SG_INTERP")) snprintf(out, sizeof(out), "SYNC_SG_INTERP:%d", SYNC_SG_INTERP ? 1 : 0);
        else if (!strcmp(param, "RELOAD_SG_INTERP")) snprintf(out, sizeof(out), "RELOAD_SG_INTERP:%d", RELOAD_SG_INTERP ? 1 : 0);
        else if (!strcmp(param, "SGTHRS")) snprintf(out, sizeof(out), "SGTHRS:%d", TMC_SGTHRS[idx]);
        else if (!strcmp(param, "TCOOLTHRS"))    snprintf(out, sizeof(out), "TCOOLTHRS:%d", TMC_TCOOLTHRS[idx]);
        else if (!strcmp(param, "SG_CURRENT_MA")) snprintf(out, sizeof(out), "SG_CURRENT_MA:%d", SG_CURRENT_MA[idx]);
        else if (!strcmp(param, "MICROSTEPS"))   snprintf(out, sizeof(out), "MICROSTEPS:%d", TMC_MICROSTEPS[idx]);
        else if (!strcmp(param, "INTERPOLATE"))  snprintf(out, sizeof(out), "INTERPOLATE:%d", TMC_INTERPOLATE[idx] ? 1 : 0);
        else if (!strcmp(param, "STEALTHCHOP"))  snprintf(out, sizeof(out), "STEALTHCHOP:%d", TMC_SPREADCYCLE[idx] ? 0 : 1);
        else if (!strcmp(param, "DRIVER_TBL"))   snprintf(out, sizeof(out), "DRIVER_TBL:%d", TMC_TBL[idx]);
        else if (!strcmp(param, "DRIVER_TOFF"))  snprintf(out, sizeof(out), "DRIVER_TOFF:%d", TMC_TOFF[idx]);
        else if (!strcmp(param, "DRIVER_HSTRT")) snprintf(out, sizeof(out), "DRIVER_HSTRT:%d", TMC_HSTRT[idx]);
        else if (!strcmp(param, "DRIVER_HEND"))  snprintf(out, sizeof(out), "DRIVER_HEND:%d", TMC_HEND[idx]);
        else if (!strcmp(param, "ROTATION_DIST")) snprintf(out, sizeof(out), "ROTATION_DIST:%.3f", (double)TMC_ROTATION_DISTANCE[idx]);
        else if (!strcmp(param, "GEAR_RATIO"))   snprintf(out, sizeof(out), "GEAR_RATIO:%.3f", (double)TMC_GEAR_RATIO[idx]);
        else if (!strcmp(param, "FULL_STEPS"))   snprintf(out, sizeof(out), "FULL_STEPS:%d", TMC_FULL_STEPS[idx]);
        else if (!strcmp(param, "RUN_CURRENT_MA")) snprintf(out, sizeof(out), "RUN_CURRENT_MA:%d", TMC_RUN_CURRENT_MA[idx]);
        else if (!strcmp(param, "HOLD_CURRENT_MA")) snprintf(out, sizeof(out), "HOLD_CURRENT_MA:%d", TMC_HOLD_CURRENT_MA[idx]);
        else if (!strcmp(param, "SERVO_OPEN"))   snprintf(out, sizeof(out), "SERVO_OPEN:%d", SERVO_OPEN_US);
        else if (!strcmp(param, "SERVO_CLOSE"))  snprintf(out, sizeof(out), "SERVO_CLOSE:%d", SERVO_CLOSE_US);
        else if (!strcmp(param, "SERVO_SETTLE")) snprintf(out, sizeof(out), "SERVO_SETTLE:%d", SERVO_SETTLE_MS);
        else if (!strcmp(param, "CUT_FEED"))     snprintf(out, sizeof(out), "CUT_FEED:%d", CUT_FEED_MM);
        else if (!strcmp(param, "CUT_LEN"))      snprintf(out, sizeof(out), "CUT_LEN:%d", CUT_LENGTH_MM);
        else if (!strcmp(param, "CUT_AMT"))      snprintf(out, sizeof(out), "CUT_AMT:%d", CUT_AMOUNT);
        else if (!strcmp(param, "TC_CUT_MS"))    snprintf(out, sizeof(out), "TC_CUT_MS:%d", TC_TIMEOUT_CUT_MS);
        else if (!strcmp(param, "TC_TH_MS"))     snprintf(out, sizeof(out), "TC_TH_MS:%d", TC_TIMEOUT_TH_MS);
        else if (!strcmp(param, "TC_Y_MS"))      snprintf(out, sizeof(out), "TC_Y_MS:%d", TC_TIMEOUT_Y_MS);
        else handled = false;

        if (handled) cmd_reply("OK", out);
        else cmd_reply("ER", "GET:UNKNOWN_PARAM");
    } else if (!strcmp(cmd, "TW")) {
        // Usage: TW:<lane>:<reg>:<val> (val in decimal or hex)
        int ln, reg;
        uint32_t val;
        if (sscanf(p, "%d:%d:%i", &ln, &reg, &val) == 3 && (ln == 1 || ln == 2) && reg >= 0 && reg <= 127) {
            tmc_t *t = (ln == 1) ? &g_tmc_l1 : &g_tmc_l2;
            if (tmc_write(t, (uint8_t)reg, val)) {
                int idx = lane_to_idx(ln);
                if (reg == TMC_REG_IHOLD_IRUN) {
                    g_shadow_ihold_irun[idx] = val;
                    g_shadow_ihold_irun_valid[idx] = true;
                    sync_currents_from_ihold_irun(ln, val);
                } else if (reg == TMC_REG_CHOPCONF) {
                    g_shadow_vsense[idx] = ((val >> 17) & 0x1u) != 0u;
                    if (g_shadow_ihold_irun_valid[idx]) {
                        sync_currents_from_ihold_irun(ln, g_shadow_ihold_irun[idx]);
                    }
                }
                cmd_reply("OK", NULL);
            } else {
                cmd_reply("ER", "TW:FAILED");
            }
        } else {
            cmd_reply("ER", "ARG");
        }
    } else if (!strcmp(cmd, "TR")) {
        // Usage: TR:<lane>:<reg>
        int ln, reg;
        if (sscanf(p, "%d:%d", &ln, &reg) == 2 && (ln == 1 || ln == 2) && reg >= 0 && reg <= 127) {
            int idx = lane_to_idx(ln);
            if (reg == TMC_REG_IHOLD_IRUN && g_shadow_ihold_irun_valid[idx]) {
                char out[32];
                snprintf(out, sizeof(out), "%d:%d:0x%08X", ln, reg, (unsigned int)g_shadow_ihold_irun[idx]);
                cmd_reply("OK", out);
                return;
            }
            tmc_t *t = (ln == 1) ? &g_tmc_l1 : &g_tmc_l2;
            uint32_t val = 0;
            if (tmc_read(t, (uint8_t)reg, &val)) {
                char out[32];
                snprintf(out, sizeof(out), "%d:%d:0x%08X", ln, reg, (unsigned int)val);
                cmd_reply("OK", out);
            } else {
                cmd_reply("ER", "TR:NO_RESPONSE");
            }
        } else {
            cmd_reply("ER", "ARG");
        }
    } else if (!strcmp(cmd, "RR")) {
        // Raw read on tx_pin: N=0→no response, N=8→full frame. Scans addr 0-3.
        // Usage: RR:1 or RR:2
        int ln = atoi(p);
        if (ln != 1 && ln != 2) {
            cmd_reply("ER", "ARG");
        } else {
            tmc_t probe = (ln == 1) ? g_tmc_l1 : g_tmc_l2;
            char out[128];
            int pos = snprintf(out, sizeof(out), "%d:", ln);
            for (uint8_t a = 0; a < 4; a++) {
                probe.addr = a;
                uint8_t buf[8] = {0};
                int n = tmc_read_raw(&probe, TMC_REG_GCONF, buf);
                pos += snprintf(out + pos, sizeof(out) - pos,
                    "A%u:N=%d:%02X%02X%02X%02X%02X%02X%02X%02X ",
                    a, n, buf[0], buf[1], buf[2], buf[3],
                    buf[4], buf[5], buf[6], buf[7]);
            }
            cmd_reply("OK", out);
        }
    } else if (!strcmp(cmd, "BOOT")) {
        cmd_reply("OK", "REBOOTING_TO_BOOTSEL");
        sleep_ms(100);
        reset_usb_boot(0, 0);
    } else {
        cmd_reply("ER", "UNKNOWN");
    }
}

static void cmd_poll(uint32_t now_ms) {
    int c;
    while ((c = getchar_timeout_us(0)) != PICO_ERROR_TIMEOUT) {
        if (c == '\r') continue;

        if (c == '\n') {
            g_cmd.buf[g_cmd.pos] = 0;

            if (g_cmd.overflow) {
                cmd_reply("ER", "OVERFLOW");
            } else if (g_cmd.pos > 0) {
                char *colon = strchr(g_cmd.buf, ':');
                const char *payload = "";
                if (colon) {
                    *colon = 0;
                    payload = colon + 1;
                }
                cmd_execute(g_cmd.buf, payload, now_ms);
            }

            g_cmd.pos = 0;
            g_cmd.overflow = false;
            continue;
        }

        if (g_cmd.pos >= (int)sizeof(g_cmd.buf) - 1) {
            g_cmd.overflow = true;
            continue;
        }

        g_cmd.buf[g_cmd.pos++] = (char)c;
    }
}

// ===================== NeoPixel state =====================
typedef enum {
    LED_IDLE,
    LED_LOADING,
    LED_ACTIVE,
    LED_TC,
    LED_ERROR,
    LED_CUTTING
} led_state_t;

static led_state_t led_state_from_system(void) {
    if (g_lane_l1.fault || g_lane_l2.fault || g_tc_ctx.state == TC_ERROR) return LED_ERROR;
    if (g_cut.state != CUT_IDLE) return LED_CUTTING;
    if (g_tc_ctx.state != TC_IDLE) return LED_TC;
    if (g_lane_l1.task == TASK_AUTOLOAD || g_lane_l2.task == TASK_AUTOLOAD) return LED_LOADING;
    if (sync_enabled && sync_current_sps > 0) return LED_ACTIVE;
    return LED_IDLE;
}

static void neopixel_tick(uint32_t now_ms) {
    static uint32_t last_ms = 0;
    if ((now_ms - last_ms) < 50u) return;
    last_ms = now_ms;

    switch (led_state_from_system()) {
        case LED_IDLE:
            neopixel_set(0, 20, 0);
            break;
        case LED_LOADING:
            neopixel_set(0, 0, 120);
            break;
        case LED_ACTIVE:
            neopixel_set(0, 200, 0);
            break;
        case LED_TC:
            neopixel_set(180, 140, 0);
            break;
        case LED_ERROR:
            neopixel_set(200, 0, 0);
            break;
        case LED_CUTTING: {
            uint8_t phase = (uint8_t)((now_ms / 32u) & 0x0Fu);
            uint8_t v = (phase < 8u) ? (uint8_t)(phase * 32u) : (uint8_t)((15u - phase) * 32u);
            neopixel_set(v, v, v);
            break;
        }
    }
}

// ===================== Main =====================
int main(void) {
    stdio_init_all();
    sleep_ms(200);

    motor_t l1;
    motor_t l2;
    motor_init(&l1, PIN_L1_EN, PIN_L1_DIR, PIN_L1_STEP, CONF_L1_DIR_INVERT);
    motor_init(&l2, PIN_L2_EN, PIN_L2_DIR, PIN_L2_STEP, CONF_L2_DIR_INVERT);
    tmc_init(&g_tmc_l1, PIN_L1_UART_TX, PIN_L1_UART_RX, 0);
    tmc_init(&g_tmc_l2, PIN_L2_UART_TX, PIN_L2_UART_RX, 0);

    lane_setup(&g_lane_l1, PIN_L1_IN, PIN_L1_OUT, l1, 1, PIN_L1_DIAG, &g_tmc_l1);
    lane_setup(&g_lane_l2, PIN_L2_IN, PIN_L2_OUT, l2, 2, PIN_L2_DIAG, &g_tmc_l2);

    din_init(&g_y_split, PIN_Y_SPLIT);
    din_init(&g_buf_adv_din, PIN_BUF_ADVANCE);
    din_init(&g_buf_trl_din, PIN_BUF_TRAILING);

    servo_init(PIN_SERVO);
    servo_set_us(PIN_SERVO, SERVO_BLOCK_US);

    adc_init();
    adc_gpio_init(PIN_BUF_ANALOG);

    stall_init();
    neopixel_init(PIN_NEOPIXEL);

    settings_load();
    g_buf.entered_ms = to_ms_since_boot(get_absolute_time());

    // din_init reads GPIOs once without debounce; sensors may not have settled.
    // Spin din_update for 25 ms so the 10 ms debounce threshold commits correctly.
    for (int i = 0; i < 25; i++) {
        din_update(&g_lane_l1.in_sw);
        din_update(&g_lane_l1.out_sw);
        din_update(&g_lane_l2.in_sw);
        din_update(&g_lane_l2.out_sw);
        din_update(&g_y_split);
        sleep_ms(1);
    }

    active_lane = detect_active_lane_from_out();
    if (active_lane == 0) {
        // Fall back: filament parked before OUT (pre-loaded state).
        // Pick lane 1 first; if only lane 2 has filament, pick lane 2.
        if (lane_in_present(&g_lane_l1) && !lane_out_present(&g_lane_l1))
            active_lane = 1;
        else if (lane_in_present(&g_lane_l2) && !lane_out_present(&g_lane_l2))
            active_lane = 2;
    }
    prev_lane1_in_present = lane_in_present(&g_lane_l1);
    prev_lane2_in_present = lane_in_present(&g_lane_l2);

    while (true) {
        g_now_ms = to_ms_since_boot(get_absolute_time());

        // Inputs
        din_update(&g_lane_l1.in_sw);
        din_update(&g_lane_l1.out_sw);
        din_update(&g_lane_l2.in_sw);
        din_update(&g_lane_l2.out_sw);
        din_update(&g_y_split);
        din_update(&g_buf_adv_din);
        din_update(&g_buf_trl_din);

        // USB commands
        cmd_poll(g_now_ms);

        // Deferred IRQ events
        stall_pump();

        // State machines (order matters)
        cutter_tick(g_now_ms);
        tc_tick(g_now_ms);
        autopreload_tick(g_now_ms);
        lane_tick(&g_lane_l1, g_now_ms);
        lane_tick(&g_lane_l2, g_now_ms);
        buf_sensor_tick(g_now_ms);
        sync_tick(g_now_ms);

        // Local indicator
        neopixel_tick(g_now_ms);

        sleep_us(100);
    }
}
