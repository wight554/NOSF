#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "pico/flash.h"

#include "hardware/flash.h"
#include "hardware/sync.h"

#include "controller_shared.h"
#include "motion.h"
#include "settings_store.h"
#include "sync.h"

#define SETTINGS_FLASH_OFFSET (PICO_FLASH_SIZE_BYTES - FLASH_SECTOR_SIZE)
#define SETTINGS_MAGIC 0x4e4f5346u
#define SETTINGS_VERSION 38u

typedef struct {
    uint32_t magic;
    uint32_t version;

    int feed_sps, rev_sps, auto_sps;
    int sync_max_sps, sync_hard_max_sps, sync_min_sps;
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

    int servo_open_us, servo_close_us, servo_block_us;
    int servo_settle_ms;
    int cut_feed_mm, cut_length_mm, cut_amount;

    int tc_timeout_cut_ms;
    int tc_timeout_th_ms;
    int tc_timeout_y_ms;

    int runout_cooldown_ms;

    int ramp_step_sps, ramp_tick_ms;

    int buf_sensor_type;
    float buf_neutral, buf_range, buf_thr, buf_analog_alpha;
    int sync_kp_sps;
    int sync_overshoot_pct;
    int ts_buf_fallback_ms;

    int join_sps;
    int press_sps;
    int trailing_sps;
    int buf_stab_sps;
    int follow_timeout_ms[NUM_LANES];

    float est_alpha_min, est_alpha_max;
    int zone_bias_base_sps, zone_bias_ramp_sps_s, zone_bias_max_sps;
    float reload_lean_factor;

    bool buf_invert;
    bool auto_preload;
    bool enable_cutter;
    bool reload_mode;

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

void settings_defaults(void) {
    FEED_SPS = CONF_FEED_SPS;
    REV_SPS = CONF_REV_SPS;
    AUTO_SPS = CONF_AUTO_SPS;

    SYNC_HARD_MAX_SPS = clamp_i(CONF_SYNC_HARD_MAX_SPS, mm_per_min_to_sps(1000.0f), mm_per_min_to_sps(5000.0f));
    SYNC_MAX_SPS = sync_clamp_max_sps(CONF_SYNC_MAX_SPS);
    SYNC_MIN_SPS = CONF_SYNC_MIN_SPS;
    SYNC_RAMP_UP_SPS = CONF_SYNC_RAMP_UP_SPS;
    SYNC_RAMP_DN_SPS = CONF_SYNC_RAMP_DN_SPS;
    SYNC_TICK_MS = CONF_SYNC_TICK_MS;
    PRE_RAMP_SPS = CONF_PRE_RAMP_SPS;
    SYNC_AUTO_STOP_MS = CONF_SYNC_AUTO_STOP_MS;
    AUTOLOAD_MAX_MM = CONF_AUTOLOAD_MAX_MM;
    LOAD_MAX_MM = CONF_LOAD_MAX_MM;
    UNLOAD_MAX_MM = CONF_UNLOAD_MAX_MM;
    RELOAD_Y_TIMEOUT_MS = CONF_RELOAD_Y_TIMEOUT_MS;
    AUTO_MODE = 1;
    AUTO_PRELOAD = true;
    DIST_IN_OUT = CONF_DIST_IN_OUT;
    DIST_OUT_Y = CONF_DIST_OUT_Y;
    DIST_Y_BUF = CONF_DIST_Y_BUF;
    BUF_BODY_LEN = CONF_BUF_BODY_LEN;
    BUF_SIZE_MM = CONF_BUF_SIZE_MM;
    BUF_HALF_TRAVEL_MM = (float)BUF_SIZE_MM / 2.0f;
    BUF_HYST_MS = CONF_BUF_HYST_MS;
    EST_ALPHA_MIN = CONF_EST_ALPHA_MIN;
    EST_ALPHA_MAX = CONF_EST_ALPHA_MAX;
    ZONE_BIAS_BASE_SPS = CONF_ZONE_BIAS_BASE_SPS;
    ZONE_BIAS_RAMP_SPS_S = CONF_ZONE_BIAS_RAMP_SPS_S;
    ZONE_BIAS_MAX_SPS = CONF_ZONE_BIAS_MAX_SPS;
    RELOAD_LEAN_FACTOR = CONF_RELOAD_LEAN_FACTOR;
    BUF_PREDICT_THR_MS = CONF_BUF_PREDICT_THR_MS;
    g_baseline_sps = CONF_BASELINE_SPS;
    g_baseline_alpha = CONF_BASELINE_ALPHA;
    BUF_INVERT = false;
    AUTO_PRELOAD = true;
    AUTOLOAD_RETRACT_MM = 10;
    ENABLE_CUTTER = false;

    MOTION_STARTUP_MS = CONF_MOTION_STARTUP_MS;
    for (int i = 0; i < NUM_LANES; i++) {
        FOLLOW_TIMEOUT_MS[i] = (i == 0) ? CONF_L1_FOLLOW_TIMEOUT_MS : CONF_L2_FOLLOW_TIMEOUT_MS;
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
    SYNC_OVERSHOOT_PCT = clamp_i(CONF_SYNC_OVERSHOOT_PCT, 0, 200);
    TS_BUF_FALLBACK_MS = CONF_TS_BUF_FALLBACK_MS;
    BUF_STAB_SPS = clamp_i(CONF_BUF_STAB_SPS, 10, 10000);

    MM_PER_STEP[0] = CONF_L1_MM_PER_STEP;
    MM_PER_STEP[1] = CONF_L2_MM_PER_STEP;

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

void settings_save(void) {
    settings_t s = {0};
    s.magic = SETTINGS_MAGIC;
    s.version = SETTINGS_VERSION;

    s.feed_sps = FEED_SPS;
    s.rev_sps = REV_SPS;
    s.auto_sps = AUTO_SPS;

    s.sync_max_sps = SYNC_MAX_SPS;
    s.sync_hard_max_sps = SYNC_HARD_MAX_SPS;
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
    s.est_alpha_min = EST_ALPHA_MIN;
    s.est_alpha_max = EST_ALPHA_MAX;
    s.zone_bias_base_sps = ZONE_BIAS_BASE_SPS;
    s.zone_bias_ramp_sps_s = ZONE_BIAS_RAMP_SPS_S;
    s.zone_bias_max_sps = ZONE_BIAS_MAX_SPS;
    s.reload_lean_factor = RELOAD_LEAN_FACTOR;

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

    s.buf_sensor_type = BUF_SENSOR_TYPE;
    s.buf_neutral = BUF_NEUTRAL;
    s.buf_range = BUF_RANGE;
    s.buf_thr = BUF_THR;
    s.buf_analog_alpha = BUF_ANALOG_ALPHA;
    s.sync_kp_sps = SYNC_KP_SPS;
    s.sync_overshoot_pct = SYNC_OVERSHOOT_PCT;
    s.ts_buf_fallback_ms = TS_BUF_FALLBACK_MS;

    s.reload_mode = (bool)RELOAD_MODE;
    s.cutter_settle_ms = CUT_TIMEOUT_SETTLE_MS;
    for (int i = 0; i < NUM_LANES; i++) {
        s.follow_timeout_ms[i] = FOLLOW_TIMEOUT_MS[i];
    }

    s.buf_stab_sps = BUF_STAB_SPS;

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

void sync_tmc_settings(int lane) {
    int idx = lane_to_idx(lane);
    tmc_t *t = (lane == 1) ? &g_tmc_l1 : &g_tmc_l2;

    MM_PER_STEP[idx] = TMC_ROTATION_DISTANCE[idx] / (float)(TMC_FULL_STEPS[idx] * TMC_GEAR_RATIO[idx] * TMC_MICROSTEPS[idx]);

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

    g_shadow_vsense[0] = true;
    g_shadow_vsense[1] = true;
    g_shadow_ihold_irun[0] = build_ihold_irun_reg(TMC_RUN_CURRENT_MA[0], TMC_HOLD_CURRENT_MA[0], true);
    g_shadow_ihold_irun[1] = build_ihold_irun_reg(TMC_RUN_CURRENT_MA[1], TMC_HOLD_CURRENT_MA[1], true);
    g_shadow_ihold_irun_valid[0] = true;
    g_shadow_ihold_irun_valid[1] = true;
}

void settings_load(void) {
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

    SYNC_HARD_MAX_SPS = clamp_i(s->sync_hard_max_sps, mm_per_min_to_sps(1000.0f), mm_per_min_to_sps(5000.0f));
    SYNC_MAX_SPS = sync_clamp_max_sps(s->sync_max_sps);
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
    EST_ALPHA_MIN = s->est_alpha_min;
    EST_ALPHA_MAX = s->est_alpha_max;
    ZONE_BIAS_BASE_SPS = s->zone_bias_base_sps;
    ZONE_BIAS_RAMP_SPS_S = s->zone_bias_ramp_sps_s;
    ZONE_BIAS_MAX_SPS = s->zone_bias_max_sps;
    RELOAD_LEAN_FACTOR = s->reload_lean_factor;

    for (int i = 0; i < NUM_LANES; i++) {
        FOLLOW_TIMEOUT_MS[i] = s->follow_timeout_ms[i];
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

    BUF_SENSOR_TYPE = s->buf_sensor_type;
    BUF_NEUTRAL = s->buf_neutral;
    BUF_RANGE = s->buf_range;
    BUF_THR = s->buf_thr;
    BUF_ANALOG_ALPHA = s->buf_analog_alpha;
    SYNC_KP_SPS = s->sync_kp_sps;
    SYNC_OVERSHOOT_PCT = clamp_i(s->sync_overshoot_pct, 0, 200);
    TS_BUF_FALLBACK_MS = s->ts_buf_fallback_ms;

    RELOAD_MODE = s->reload_mode ? 1 : 0;
    CUT_TIMEOUT_SETTLE_MS = s->cutter_settle_ms;
    JOIN_SPS = s->join_sps;
    PRESS_SPS = s->press_sps;
    TRAILING_SPS = s->trailing_sps;
    BUF_STAB_SPS = s->buf_stab_sps;
    for (int i = 0; i < NUM_LANES; i++) {
        FOLLOW_TIMEOUT_MS[i] = s->follow_timeout_ms[i];
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
