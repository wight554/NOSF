#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pico/bootrom.h"
#include "pico/flash.h"
#include "pico/stdlib.h"
#include "pico/stdio_usb.h"
#include "config.h"
#include "controller_shared.h"

#include "hardware/clocks.h"
#include "hardware/flash.h"
#include "hardware/gpio.h"
#include "hardware/adc.h"
#include "hardware/pwm.h"
#include "hardware/sync.h"

#include "neopixel.h"
#include "motion.h"
#include "tmc2209.h"
#include "protocol.h"
#include "settings_store.h"
#include "sync.h"
#include "toolchange.h"
#include "cutter.h"
#include <math.h>



// ===================== Tunables =====================
int FEED_SPS = CONF_FEED_SPS;
int REV_SPS = CONF_REV_SPS;
int AUTO_SPS = CONF_AUTO_SPS;

int MOTION_STARTUP_MS = CONF_MOTION_STARTUP_MS;

int RUNOUT_COOLDOWN_MS = CONF_RUNOUT_COOLDOWN_MS;
int POST_PRINT_STAB_DELAY_MS = CONF_POST_PRINT_STAB_DELAY_MS;
int RELOAD_MODE = CONF_RELOAD_MODE;
int RELOAD_Y_TIMEOUT_MS = CONF_RELOAD_Y_TIMEOUT_MS;
int RELOAD_JOIN_DELAY_MS = CONF_RELOAD_JOIN_DELAY_MS;
int JOIN_SPS = CONF_JOIN_SPS;
int PRESS_SPS = CONF_PRESS_SPS;
int TRAILING_SPS = CONF_TRAILING_SPS;
int RELOAD_TOUCH_SETTLE_MS = CONF_RELOAD_TOUCH_SETTLE_MS;
int RELOAD_TOUCH_BOOST_MS = CONF_RELOAD_TOUCH_BOOST_MS;
int RELOAD_TOUCH_FLOOR_PCT = CONF_RELOAD_TOUCH_FLOOR_PCT;
int BUF_STAB_SPS = 0;
int FOLLOW_TIMEOUT_MS[NUM_LANES] = {CONF_L1_FOLLOW_TIMEOUT_MS, CONF_L2_FOLLOW_TIMEOUT_MS};

int ZONE_BIAS_BASE_SPS = CONF_ZONE_BIAS_BASE_SPS;
int ZONE_BIAS_RAMP_SPS_S = CONF_ZONE_BIAS_RAMP_SPS_S;
int ZONE_BIAS_MAX_SPS = CONF_ZONE_BIAS_MAX_SPS;
float EST_ALPHA_MIN = CONF_EST_ALPHA_MIN;
float EST_ALPHA_MAX = CONF_EST_ALPHA_MAX;
float RELOAD_LEAN_FACTOR = CONF_RELOAD_LEAN_FACTOR;

int RAMP_STEP_SPS = CONF_RAMP_STEP_SPS;
int RAMP_TICK_MS = CONF_RAMP_TICK_MS;

int TMC_RUN_CURRENT_MA[NUM_LANES] = {CONF_L1_RUN_CURRENT_MA, CONF_L2_RUN_CURRENT_MA};
int TMC_HOLD_CURRENT_MA[NUM_LANES] = {CONF_L1_HOLD_CURRENT_MA, CONF_L2_HOLD_CURRENT_MA};
int TMC_MICROSTEPS[NUM_LANES] = {CONF_L1_MICROSTEPS, CONF_L2_MICROSTEPS};
int TMC_STEALTHCHOP_SPS[NUM_LANES] = {CONF_L1_STEALTHCHOP_THRESHOLD, CONF_L2_STEALTHCHOP_THRESHOLD};
float TMC_ROTATION_DISTANCE[NUM_LANES] = {CONF_L1_ROTATION_DISTANCE, CONF_L2_ROTATION_DISTANCE};
float TMC_GEAR_RATIO[NUM_LANES] = {CONF_L1_GEAR_RATIO, CONF_L2_GEAR_RATIO};
int TMC_FULL_STEPS[NUM_LANES] = {CONF_L1_FULL_STEPS, CONF_L2_FULL_STEPS};
int TMC_TBL[NUM_LANES] = {CONF_L1_TBL, CONF_L2_TBL};
int TMC_TOFF[NUM_LANES] = {CONF_L1_TOFF, CONF_L2_TOFF};
int TMC_HSTRT[NUM_LANES] = {CONF_L1_HSTRT, CONF_L2_HSTRT};
int TMC_HEND[NUM_LANES] = {CONF_L1_HEND, CONF_L2_HEND};
bool TMC_INTERPOLATE[NUM_LANES] = {CONF_L1_INTPOL, CONF_L2_INTPOL};

int BUF_SENSOR_TYPE = CONF_BUF_SENSOR_TYPE;
float BUF_NEUTRAL = CONF_BUF_NEUTRAL;
float BUF_RANGE = CONF_BUF_RANGE;
float BUF_THR = CONF_BUF_THR;
float BUF_ANALOG_ALPHA = CONF_BUF_ANALOG_ALPHA;
int SYNC_KP_SPS = CONF_SYNC_KP_SPS;
int SYNC_OVERSHOOT_PCT = CONF_SYNC_OVERSHOOT_PCT;
int SYNC_RESERVE_PCT = CONF_SYNC_RESERVE_PCT;
int TS_BUF_FALLBACK_MS = CONF_TS_BUF_FALLBACK_MS;

int SERVO_OPEN_US = CONF_SERVO_OPEN_US;
int SERVO_CLOSE_US = CONF_SERVO_CLOSE_US;
int SERVO_BLOCK_US = CONF_SERVO_BLOCK_US;
int SERVO_SETTLE_MS = CONF_SERVO_SETTLE_MS;
int CUT_FEED_MM = CONF_CUT_FEED_MM;
int CUT_LENGTH_MM = CONF_CUT_LENGTH_MM;
int CUT_AMOUNT = CONF_CUT_AMOUNT;
int CUT_TIMEOUT_SETTLE_MS = CONF_CUT_SETTLE_MS;
int CUT_TIMEOUT_FEED_MS = CONF_CUT_FEED_MS;

int TC_TIMEOUT_CUT_MS = CONF_TC_TIMEOUT_CUT_MS;
int LOAD_MAX_MM = CONF_LOAD_MAX_MM;
int UNLOAD_MAX_MM = CONF_UNLOAD_MAX_MM;
int UNLOAD_ADV_BLOCK_MS = CONF_UNLOAD_ADV_BLOCK_MS;
int TC_TIMEOUT_TH_MS = CONF_TC_TIMEOUT_TH_MS;
int TC_TIMEOUT_Y_MS = CONF_TC_TIMEOUT_Y_MS;

int SYNC_MAX_SPS = CONF_SYNC_MAX_SPS;
int GLOBAL_MAX_SPS = CONF_GLOBAL_MAX_SPS;
int SYNC_MIN_SPS = CONF_SYNC_MIN_SPS;
int SYNC_RAMP_UP_SPS = CONF_SYNC_RAMP_UP_SPS;
int SYNC_RAMP_DN_SPS = CONF_SYNC_RAMP_DN_SPS;
int SYNC_TICK_MS = CONF_SYNC_TICK_MS;
int PRE_RAMP_SPS = CONF_PRE_RAMP_SPS;
int BUF_HYST_MS = CONF_BUF_HYST_MS;
int BUF_PREDICT_THR_MS = CONF_BUF_PREDICT_THR_MS;
float BUF_HALF_TRAVEL_MM = CONF_BUF_HALF_TRAVEL_MM;
int SYNC_AUTO_STOP_MS = CONF_SYNC_AUTO_STOP_MS;
int SYNC_ADVANCE_DWELL_STOP_MS = CONF_SYNC_ADVANCE_DWELL_STOP_MS;
int SYNC_ADVANCE_RAMP_DELAY_MS = CONF_SYNC_ADVANCE_RAMP_DELAY_MS;
int SYNC_OVERSHOOT_MID_EXTEND = CONF_SYNC_OVERSHOOT_MID_EXTEND;
float SYNC_TRAILING_BIAS_FRAC = CONF_SYNC_TRAILING_BIAS_FRAC;
int MID_CREEP_TIMEOUT_MS = CONF_MID_CREEP_TIMEOUT_MS;
int MID_CREEP_RATE_SPS_PER_S = CONF_MID_CREEP_RATE_SPS_PER_S;
int MID_CREEP_CAP_FRAC = CONF_MID_CREEP_CAP_FRAC;
float BUF_VARIANCE_BLEND_FRAC = CONF_BUF_VARIANCE_BLEND_FRAC;
float BUF_VARIANCE_BLEND_REF_MM = CONF_BUF_VARIANCE_BLEND_REF_MM;
float SYNC_RESERVE_INTEGRAL_GAIN = CONF_SYNC_RESERVE_INTEGRAL_GAIN;
float SYNC_RESERVE_INTEGRAL_CLAMP_MM = CONF_SYNC_RESERVE_INTEGRAL_CLAMP_MM;
int   SYNC_RESERVE_INTEGRAL_DECAY_MS = CONF_SYNC_RESERVE_INTEGRAL_DECAY_MS;
float EST_SIGMA_HARD_CAP_MM = CONF_EST_SIGMA_HARD_CAP_MM;
float EST_LOW_CF_WARN_THRESHOLD = CONF_EST_LOW_CF_WARN_THRESHOLD;
float EST_FALLBACK_CF_THRESHOLD = CONF_EST_FALLBACK_CF_THRESHOLD;
int   BUF_DRIFT_EWMA_TAU_MS = CONF_BUF_DRIFT_EWMA_TAU_MS;
int   BUF_DRIFT_MIN_SAMPLES = CONF_BUF_DRIFT_MIN_SAMPLES;
float BUF_DRIFT_APPLY_THR_MM = CONF_BUF_DRIFT_APPLY_THR_MM;
float BUF_DRIFT_CLAMP_MM = CONF_BUF_DRIFT_CLAMP_MM;
float BUF_DRIFT_APPLY_MIN_CF = CONF_BUF_DRIFT_APPLY_MIN_CF;
int   ADV_RISK_WINDOW_MS = CONF_ADV_RISK_WINDOW_MS;
int   ADV_RISK_THRESHOLD = CONF_ADV_RISK_THRESHOLD;
int AUTOLOAD_MAX_MM = CONF_AUTOLOAD_MAX_MM;
bool BUF_INVERT = false;
int AUTO_MODE = 1; // 1=Automated flow, 0=Host-controlled flow
bool AUTO_PRELOAD = true;
int AUTOLOAD_RETRACT_MM = 10;
bool ENABLE_CUTTER = CONF_ENABLE_CUTTER;
bool TC_AUTO_CUT = CONF_TC_AUTO_CUT;

int DIST_IN_OUT = CONF_DIST_IN_OUT;
int DIST_OUT_Y  = CONF_DIST_OUT_Y;
int DIST_Y_BUF  = CONF_DIST_Y_BUF;
int BUF_BODY_LEN = CONF_BUF_BODY_LEN;
int BUF_SIZE_MM = CONF_BUF_SIZE_MM;

// Derived Physical Path Constants
#define Y_TO_BUF_NEUTRAL      ((float)DIST_Y_BUF + (float)BUF_SIZE_MM / 2.0f)

float MM_PER_STEP[NUM_LANES] = {CONF_L1_MM_PER_STEP, CONF_L2_MM_PER_STEP};

int mm_per_min_to_sps_idx(float mm_per_min, int idx) {
    return (int)(mm_per_min / 60.0f / MM_PER_STEP[idx] + 0.5f);
}
int mm_per_min_to_sps(float mm_per_min) {
    return mm_per_min_to_sps_idx(mm_per_min, 0);
}
float sps_to_mm_per_min_idx(int sps, int idx) {
    return (float)sps * MM_PER_STEP[idx] * 60.0f + 0.05f; // Small offset for display rounding
}
float sps_to_mm_per_min(int sps) {
    return sps_to_mm_per_min_idx(sps, 0);
}

uint32_t g_shadow_ihold_irun[NUM_LANES] = {0, 0};
bool g_shadow_ihold_irun_valid[NUM_LANES] = {false, false};
bool g_shadow_vsense[NUM_LANES] = {true, true};

// ===================== Helpers =====================
int clamp_i(int v, int lo, int hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

float clamp_f(float v, float lo, float hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

int lane_to_idx(int ln) {
    return (ln == 1) ? 0 : 1;
}

static int cs_to_ma(uint8_t cs, bool vsense) {
    const float reff = CONF_RSENSE_OHM + 0.020f;
    const float vref = vsense ? 0.180f : 0.325f;
    const float sqrt2 = 1.41421356f;
    float irms = ((float)cs + 1.0f) * vref / (32.0f * reff * sqrt2);
    int ma = (int)(irms * 1000.0f + 0.5f);
    return clamp_i(ma, 0, 2000);
}

static uint8_t ma_to_cs(int ma, bool vsense) {
    if (ma <= 0) return 0;
    const float reff = CONF_RSENSE_OHM + 0.020f;
    const float vref = vsense ? 0.180f : 0.325f;
    const float sqrt2 = 1.41421356f;
    float irms = (float)ma / 1000.0f;
    int cs = (int)(32.0f * irms * reff * sqrt2 / vref - 1.0f + 0.5f);
    return (uint8_t)clamp_i(cs, 0, 31);
}

uint32_t build_ihold_irun_reg(int run_ma, int hold_ma, bool vsense) {
    uint8_t irun = ma_to_cs(run_ma, vsense);
    uint8_t ihold = ma_to_cs(hold_ma, vsense);
    return ((uint32_t)ihold) | ((uint32_t)irun << 8) | (8u << 16);
}

void sync_currents_from_ihold_irun(int ln, uint32_t reg) {
    int idx = lane_to_idx(ln);
    uint8_t ihold = (uint8_t)(reg & 0x1Fu);
    uint8_t irun = (uint8_t)((reg >> 8) & 0x1Fu);
    bool vsense = g_shadow_vsense[idx];
    TMC_RUN_CURRENT_MA[idx] = cs_to_ma(irun, vsense);
    TMC_HOLD_CURRENT_MA[idx] = cs_to_ma(ihold, vsense);
}

// ===================== Globals =====================
lane_t g_lane_l1;
lane_t g_lane_l2;
din_t g_y_split;

din_t g_buf_adv_din;
din_t g_buf_trl_din;

tmc_t g_tmc_l1;
tmc_t g_tmc_l2;

tc_ctx_t g_tc_ctx = { .state = TC_IDLE };

volatile uint32_t g_now_ms = 0;
int active_lane = 0;
bool toolhead_has_filament = false;

bool prev_lane1_in_present = false;
bool prev_lane2_in_present = false;

// ===================== Forward declarations =====================
// Toolhead sensor state is always tracked, but in AUTO_MODE sync is governed
// by buffer state rather than TS events.
void set_toolhead_filament(bool present) {
    toolhead_has_filament = present;
    if (!AUTO_MODE) {
        sync_enabled = present;
        if (!present) {
            sync_current_sps = 0;
            sync_auto_started = false;
            sync_tail_assist_active = false;
            sync_idle_since_ms = 0;
        }
    }
}

static int detect_active_lane_from_out(void) {
    bool l1 = lane_out_present(&g_lane_l1);
    bool l2 = lane_out_present(&g_lane_l2);
    if (l1 && !l2) return 1;
    if (l2 && !l1) return 2;
    return 0;
}

void set_active_lane(int lane) {
    active_lane = lane;
    if (lane == 1 || lane == 2) {
        char lane_s[2] = { (char)('0' + lane), 0 };
        cmd_event("ACTIVE", lane_s);
    } else {
        cmd_event("ACTIVE", "NONE");
    }
}

lane_t *lane_ptr(int lane) {
    if (lane == 1) return &g_lane_l1;
    if (lane == 2) return &g_lane_l2;
    return NULL;
}

int other_lane(int lane) {
    return (lane == 1) ? 2 : 1;
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
        if (g_lane_l1.fault == FAULT_DRY_SPIN) g_lane_l1.fault = FAULT_NONE;
        if (g_tc_ctx.state == TC_ERROR) tc_abort();
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
        if (g_lane_l2.fault == FAULT_DRY_SPIN) g_lane_l2.fault = FAULT_NONE;
        if (g_tc_ctx.state == TC_ERROR) tc_abort();
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
    if (cutter_busy()) return LED_CUTTING;
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

    lane_setup(&g_lane_l1, PIN_L1_IN, PIN_L1_OUT, l1, 1, &g_tmc_l1);
    lane_setup(&g_lane_l2, PIN_L2_IN, PIN_L2_OUT, l2, 2, &g_tmc_l2);

    din_init(&g_y_split, PIN_Y_SPLIT);
    din_init(&g_buf_adv_din, PIN_BUF_ADVANCE);
    din_init(&g_buf_trl_din, PIN_BUF_TRAILING);

    adc_init();
    adc_gpio_init(PIN_BUF_ANALOG);

    neopixel_init(PIN_NEOPIXEL);

    settings_load();
    cutter_init();
    g_buf.entered_ms = to_ms_since_boot(get_absolute_time());

    // din_init reads GPIOs once without debounce; sensors may not have settled.
    // Spin din_update for 25 ms so the 10 ms debounce threshold commits correctly.
    for (int i = 0; i < 25; i++) {
        din_update(&g_lane_l1.in_sw);
        din_update(&g_lane_l1.out_sw);
        din_update(&g_lane_l2.in_sw);
        din_update(&g_lane_l2.out_sw);
        din_update(&g_y_split);
        din_update(&g_buf_adv_din);
        din_update(&g_buf_trl_din);
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

    // Start any needed dual-endstop buffer neutralization in the background so
    // commands and state machines are responsive immediately after boot.
    if (active_lane != 0) {
        boot_stabilize_start(to_ms_since_boot(get_absolute_time()));
    }

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

        // Background buffer neutralization: boot startup and optional post-print cleanup.
        buffer_stabilize_tick(g_now_ms);

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
