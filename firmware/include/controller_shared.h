#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "pico/stdlib.h"
#include "config.h"
#include "tmc2209.h"

typedef struct {
    uint pin;
    bool stable;
    bool last_raw;
    absolute_time_t last_edge;
} din_t;

typedef struct {
    uint en, dir, step;
    bool dir_invert;
    uint slice;
    uint chan;
} motor_t;

typedef enum {
    TASK_IDLE = 0,
    TASK_AUTOLOAD,
    TASK_FEED,
    TASK_UNLOAD,
    TASK_LOAD_FULL,
    TASK_MOVE
} task_t;

typedef enum {
    FAULT_NONE = 0,
    FAULT_TIMEOUT,
    FAULT_SENSOR,
    FAULT_BUF,
    FAULT_CUT,
    FAULT_DRY_SPIN
} fault_t;

typedef struct lane_s {
    din_t in_sw;
    din_t out_sw;
    motor_t m;
    task_t task;
    uint32_t motion_started_ms;
    uint32_t task_started_ms;
    uint32_t dry_spin_ms;
    float task_limit_mm;
    uint32_t retract_deadline_ms;
    int target_sps;
    int current_sps;
    uint32_t ramp_last_tick_ms;
    tmc_t *tmc;
    bool unload_sensor_latch;
    bool unload_buf_recover_done;
    fault_t fault;
    int lane_id;
    uint32_t runout_block_until_ms;
    uint32_t buf_advance_since_ms;
    uint32_t reload_tail_ms;
    float task_dist_mm;
    float dist_at_out_mm;
    uint32_t last_dist_tick_ms;
    float dist_at_in_clear_mm;
    bool prev_in;
    bool unload_to_in;
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
    TC_RELOAD_WAIT_Y,
    TC_RELOAD_APPROACH,
    TC_RELOAD_FOLLOW,
    TC_ERROR
} tc_state_t;

typedef struct {
    tc_state_t state;
    int target_lane;
    int from_lane;
    uint32_t phase_start_ms;
    uint32_t ready_to_join_since_ms;
    uint32_t reload_tick_ms;
    int reload_current_sps;
    uint32_t last_trailing_ms;
    uint32_t wall_critical_since_ms;
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
    int lane_idx_at_entry;
    int mmu_sps_at_entry;
    uint32_t mmu_sps_dwell_sum;
    uint32_t mmu_sps_dwell_samples;
} buf_tracker_t;

static inline bool on_al(const din_t *d) {
    return d->stable != 0;
}

static inline bool lane_in_present(lane_t *L) {
    return on_al(&L->in_sw);
}

static inline bool lane_out_present(lane_t *L) {
    return on_al(&L->out_sw);
}

extern int FEED_SPS;
extern int REV_SPS;
extern int AUTO_SPS;
extern int MOTION_STARTUP_MS;
extern int RUNOUT_COOLDOWN_MS;
extern int POST_PRINT_STAB_DELAY_MS;
extern int RELOAD_MODE;
extern int RELOAD_Y_TIMEOUT_MS;
extern int RELOAD_JOIN_DELAY_MS;
extern int JOIN_SPS;
extern int PRESS_SPS;
extern int TRAILING_SPS;
extern int RELOAD_TOUCH_SETTLE_MS;
extern int RELOAD_TOUCH_BOOST_MS;
extern int RELOAD_TOUCH_FLOOR_PCT;
extern int BUF_STAB_SPS;
extern int FOLLOW_TIMEOUT_MS[NUM_LANES];
extern int ZONE_BIAS_BASE_SPS;
extern int ZONE_BIAS_RAMP_SPS_S;
extern int ZONE_BIAS_MAX_SPS;
extern float EST_ALPHA_MIN;
extern float EST_ALPHA_MAX;
extern float RELOAD_LEAN_FACTOR;
extern int RAMP_STEP_SPS;
extern int RAMP_TICK_MS;
extern int TMC_RUN_CURRENT_MA[NUM_LANES];
extern int TMC_HOLD_CURRENT_MA[NUM_LANES];
extern int TMC_MICROSTEPS[NUM_LANES];
extern int TMC_STEALTHCHOP_SPS[NUM_LANES];
extern float TMC_ROTATION_DISTANCE[NUM_LANES];
extern float TMC_GEAR_RATIO[NUM_LANES];
extern int TMC_FULL_STEPS[NUM_LANES];
extern int TMC_TBL[NUM_LANES];
extern int TMC_TOFF[NUM_LANES];
extern int TMC_HSTRT[NUM_LANES];
extern int TMC_HEND[NUM_LANES];
extern bool TMC_INTERPOLATE[NUM_LANES];
extern int BUF_SENSOR_TYPE;
extern float BUF_NEUTRAL;
extern float BUF_RANGE;
extern float BUF_THR;
extern float BUF_ANALOG_ALPHA;
extern int SYNC_KP_SPS;
extern int SYNC_OVERSHOOT_PCT;
extern int SYNC_RESERVE_PCT;
extern int TS_BUF_FALLBACK_MS;
extern int SERVO_OPEN_US;
extern int SERVO_CLOSE_US;
extern int SERVO_BLOCK_US;
extern int SERVO_SETTLE_MS;
extern int CUT_FEED_MM;
extern int CUT_LENGTH_MM;
extern int CUT_AMOUNT;
extern int CUT_TIMEOUT_SETTLE_MS;
extern int CUT_TIMEOUT_FEED_MS;
extern int TC_TIMEOUT_CUT_MS;
extern int LOAD_MAX_MM;
extern int UNLOAD_MAX_MM;
extern int TC_TIMEOUT_TH_MS;
extern int TC_TIMEOUT_Y_MS;
extern int SYNC_MAX_SPS;
extern int GLOBAL_MAX_SPS;
extern int SYNC_MIN_SPS;
extern int SYNC_RAMP_UP_SPS;
extern int SYNC_RAMP_DN_SPS;
extern int SYNC_TICK_MS;
extern int PRE_RAMP_SPS;
extern int BUF_HYST_MS;
extern int BUF_PREDICT_THR_MS;
extern float BUF_HALF_TRAVEL_MM;
extern int SYNC_AUTO_STOP_MS;
extern int SYNC_ADVANCE_DWELL_STOP_MS;
extern int SYNC_ADVANCE_RAMP_DELAY_MS;
extern int SYNC_OVERSHOOT_MID_EXTEND;
extern float SYNC_RESERVE_INTEGRAL_GAIN;
extern float SYNC_RESERVE_INTEGRAL_CLAMP_MM;
extern int   SYNC_RESERVE_INTEGRAL_DECAY_MS;
extern float EST_SIGMA_HARD_CAP_MM;
extern float EST_LOW_CF_WARN_THRESHOLD;
extern float EST_FALLBACK_CF_THRESHOLD;
#define BUF_DRIFT_CLAMP_LIMIT_MM 8.0f
extern int   BUF_DRIFT_EWMA_TAU_MS;
extern int   BUF_DRIFT_MIN_SAMPLES;
extern float BUF_DRIFT_APPLY_THR_MM;
extern float BUF_DRIFT_CLAMP_MM;
extern float BUF_DRIFT_APPLY_MIN_CF;
extern int   ADV_RISK_WINDOW_MS;
extern int   ADV_RISK_THRESHOLD;
extern int AUTOLOAD_MAX_MM;
extern bool BUF_INVERT;
extern int AUTO_MODE;
extern bool AUTO_PRELOAD;
extern int AUTOLOAD_RETRACT_MM;
extern bool ENABLE_CUTTER;
extern int DIST_IN_OUT;
extern int DIST_OUT_Y;
extern int DIST_Y_BUF;
extern int BUF_BODY_LEN;
extern int BUF_SIZE_MM;
extern float MM_PER_STEP[NUM_LANES];
extern uint32_t g_shadow_ihold_irun[NUM_LANES];
extern bool g_shadow_ihold_irun_valid[NUM_LANES];
extern bool g_shadow_vsense[NUM_LANES];
extern lane_t g_lane_l1;
extern lane_t g_lane_l2;
extern din_t g_y_split;
extern din_t g_buf_adv_din;
extern din_t g_buf_trl_din;
extern tmc_t g_tmc_l1;
extern tmc_t g_tmc_l2;
extern cutter_ctx_t g_cut;
extern tc_ctx_t g_tc_ctx;
extern volatile uint32_t g_now_ms;
extern int active_lane;
extern bool toolhead_has_filament;
extern bool sync_enabled;
extern bool sync_auto_started;
extern bool sync_tail_assist_active;
extern uint32_t sync_idle_since_ms;
extern int sync_current_sps;
extern int g_baseline_target_sps;
extern int g_baseline_sps;
extern float g_baseline_alpha;
extern uint32_t sync_fast_brake_until_ms;
extern buf_tracker_t g_buf;
extern float extruder_est_sps;
extern float g_buf_pos;
extern bool g_boot_stabilizing;

int mm_per_min_to_sps_idx(float mm_per_min, int idx);
int mm_per_min_to_sps(float mm_per_min);
float sps_to_mm_per_min_idx(int sps, int idx);
float sps_to_mm_per_min(int sps);
int clamp_i(int v, int lo, int hi);
float clamp_f(float v, float lo, float hi);
int lane_to_idx(int ln);
uint32_t build_ihold_irun_reg(int run_ma, int hold_ma, bool vsense);
void sync_currents_from_ihold_irun(int ln, uint32_t reg);
void set_toolhead_filament(bool present);
void set_active_lane(int lane);
lane_t *lane_ptr(int lane);
int other_lane(int lane);
