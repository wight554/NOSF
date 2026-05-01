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
#include "hardware/pwm.h"
#include "hardware/sync.h"

#include "neopixel.h"
#include "tmc2209.h"

// ===================== PINOUT (ERB V2.0 draft, verify against schematic) =====================
#define PIN_L1_IN        2
#define PIN_L1_OUT       3
#define PIN_L2_IN        4
#define PIN_L2_OUT       5
#define PIN_Y_SPLIT      6

#define PIN_BUF_ADVANCE  7
#define PIN_BUF_TRAILING 12

#define PIN_M1_EN        8
#define PIN_M1_DIR       9
#define PIN_M1_STEP      10
#define PIN_M1_UART_TX   11
#define PIN_M1_UART_RX   13
#define PIN_M1_DIAG      13  // same as UART_RX on ERB

#define PIN_M2_EN        14
#define PIN_M2_DIR       15
#define PIN_M2_STEP      16
#define PIN_M2_UART_TX   17
#define PIN_M2_UART_RX   19
#define PIN_M2_DIAG      19  // same as UART_RX on ERB

#define PIN_SERVO        23
#define PIN_NEOPIXEL     21

#define M1_DIR_INVERT    CONF_M1_DIR_INVERT
#define M2_DIR_INVERT    CONF_M2_DIR_INVERT
#define EN_ACTIVE_LOW    1

// ===================== Tunables =====================
static int FEED_SPS = CONF_FEED_SPS;
static int REV_SPS = CONF_REV_SPS;
static int AUTO_SPS = CONF_AUTO_SPS;

static int MOTION_STARTUP_MS = 10000;

static int LOW_DELAY_MS = 400;
static int SWAP_COOLDOWN_MS = 500;
static int RUNOUT_COOLDOWN_MS = 12000;
static bool REQUIRE_Y_EMPTY_SWAP = true;

static int RAMP_STEP_SPS = 200;
static int RAMP_TICK_MS = 5;

static int TMC_RUN_CURRENT_MA = 850;
static int TMC_HOLD_CURRENT_MA = 300;
static int TMC_MICROSTEPS = CONF_MICROSTEPS;
static bool TMC_SPREADCYCLE = true;
static int TMC_SGT_L1 = 80;
static int TMC_SGT_L2 = 80;
static int TMC_TCOOLTHRS = 400;

static int SERVO_OPEN_US = 500;
static int SERVO_CLOSE_US = 1400;
static int SERVO_BLOCK_US = 950;
static int SERVO_SETTLE_MS = 500;
static int CUT_FEED_MM = 48;
static int CUT_LENGTH_MM = 10;
static int CUT_AMOUNT = 1;
static int CUT_TIMEOUT_SETTLE_MS = 1500;
static int CUT_TIMEOUT_FEED_MS = 5000;

static int TC_TIMEOUT_CUT_MS = 5000;
static int TC_TIMEOUT_UNLOAD_MS = 8000;
static int TC_TIMEOUT_TH_MS = 3000;
static int TC_TIMEOUT_LOAD_MS = 15000;

static int SYNC_MAX_SPS = 8000;
static int SYNC_MIN_SPS = 0;
static int SYNC_RAMP_UP_SPS = 300;
static int SYNC_RAMP_DN_SPS = 150;
static int SYNC_TICK_MS = 20;
static int PRE_RAMP_SPS = 400;
static int BUF_HYST_MS = 30;
static int BUF_PREDICT_THR_MS = 250;
static float BUF_HALF_TRAVEL_MM = 5.0f;
static float SYNC_RATIO = 1.0f;
static bool BUF_INVERT = false;

static float MM_PER_STEP = CONF_MM_PER_STEP; // TUNE: gear + microstep derived.

// ===================== Helpers =====================
static inline int clamp_i(int v, int lo, int hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
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
    return d->stable == 0;
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
    TASK_UNLOAD
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
    uint32_t autoload_deadline_ms;

    tmc_t *tmc;
    uint diag_pin;
    uint32_t motion_started_ms;
    bool stall_armed;
    fault_t fault;
    int lane_id;
    uint32_t runout_block_until_ms;
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
    TC_UNLOAD_WAIT_TH,
    TC_UNLOAD_DONE,
    TC_SWAP,
    TC_LOAD_START,
    TC_LOAD_WAIT_OUT,
    TC_LOAD_WAIT_TH,
    TC_LOAD_DONE,
    TC_ERROR
} tc_state_t;

typedef struct {
    tc_state_t state;
    int target_lane;
    int from_lane;
    uint32_t phase_start_ms;
    bool sync_was_enabled;
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

// ===================== Globals =====================
static lane_t g_lane1;
static lane_t g_lane2;
static din_t g_y_split;

static din_t g_buf_adv_din;
static din_t g_buf_trl_din;

static tmc_t g_tmc1;
static tmc_t g_tmc2;

static cutter_ctx_t g_cut = {0};
static tc_ctx_t g_tc_ctx = { .state = TC_IDLE };

static volatile uint32_t g_now_ms = 0;
static int active_lane = 1;
static bool toolhead_has_filament = false;

static bool sync_enabled = false;
static int sync_current_sps = 0;
static int g_baseline_sps = 3000;
static float g_baseline_alpha = 0.15f;

static buf_tracker_t g_buf = { .state = BUF_MID };
static zone_event_t g_history[HISTORY_LEN] = {0};
static int g_hist_idx = 0;

static uint32_t sync_last_tick_ms = 0;
static uint32_t sync_last_evt_ms = 0;

static volatile bool stall_pending_l1 = false;
static volatile bool stall_pending_l2 = false;

// ===================== Forward declarations =====================
static void cmd_event(const char *type, const char *data);

// ===================== Lane helpers =====================
static inline bool lane_in_present(lane_t *L) { return on_al(&L->in_sw); }
static inline bool lane_out_present(lane_t *L) { return on_al(&L->out_sw); }

static inline lane_t *lane_ptr(int lane) {
    return (lane == 1) ? &g_lane1 : &g_lane2;
}

static inline int other_lane(int lane) {
    return (lane == 1) ? 2 : 1;
}

static void lane_setup(lane_t *L, uint pin_in, uint pin_out, motor_t m, int lane_id, uint diag_pin, tmc_t *tmc) {
    din_init(&L->in_sw, pin_in);
    din_init(&L->out_sw, pin_out);
    L->m = m;
    L->task = TASK_IDLE;
    L->autoload_deadline_ms = 0;
    L->tmc = tmc;
    L->diag_pin = diag_pin;
    L->motion_started_ms = 0;
    L->stall_armed = false;
    L->fault = FAULT_NONE;
    L->lane_id = lane_id;
    L->runout_block_until_ms = 0;
}

static void lane_stop(lane_t *L) {
    L->task = TASK_IDLE;
    L->stall_armed = false;
    motor_stop(&L->m);
}

static void lane_start(lane_t *L, task_t t, int sps, bool forward, uint32_t now_ms, int autoload_timeout_ms) {
    L->task = t;
    L->fault = FAULT_NONE;
    L->motion_started_ms = now_ms;
    L->stall_armed = false;

    if (t == TASK_AUTOLOAD) {
        L->autoload_deadline_ms = now_ms + (uint32_t)autoload_timeout_ms;
    }

    motor_enable(&L->m, true);
    motor_set_dir(&L->m, forward);
    motor_set_rate_sps(&L->m, sps);
}

static void lane_tick(lane_t *L, uint32_t now_ms) {
    if (!L->stall_armed && L->task != TASK_IDLE) {
        if ((int32_t)(now_ms - L->motion_started_ms) >= MOTION_STARTUP_MS) {
            L->stall_armed = true;
        }
    }

    if (L->task == TASK_AUTOLOAD) {
        if (lane_out_present(L) || (int32_t)(now_ms - L->autoload_deadline_ms) >= 0) {
            lane_stop(L);
        }
    }

    if ((L->task == TASK_FEED || L->task == TASK_AUTOLOAD) && !lane_in_present(L)) {
        if ((int32_t)(now_ms - L->motion_started_ms) >= MOTION_STARTUP_MS &&
            (int32_t)(now_ms - L->runout_block_until_ms) >= 0) {
            char lane_s[2] = { (char)('0' + L->lane_id), 0 };
            cmd_event("RUNOUT", lane_s);
            L->runout_block_until_ms = now_ms + (uint32_t)RUNOUT_COOLDOWN_MS;
        }
    }
}

static void stop_all(void) {
    lane_stop(&g_lane1);
    lane_stop(&g_lane2);
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

static uint32_t cut_feed_ms_for_mm(int mm) {
    float secs = (float)mm / ((float)REV_SPS * MM_PER_STEP);
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

    g_cut.lane = L;
    g_cut.repeats_done = 0;
    g_cut.feed_initial_ms = cut_feed_ms_for_mm(CUT_FEED_MM);
    g_cut.feed_repeat_ms = cut_feed_ms_for_mm(CUT_LENGTH_MM);
    g_cut.phase_start_ms = now_ms;
    g_cut.state = CUT_OPENING;

    char lane_s[2] = { (char)('0' + L->lane_id), 0 };
    cmd_event("TC:CUTTING", lane_s);
}

static void cutter_abort(void) {
    if (g_cut.lane) {
        motor_stop(&g_cut.lane->m);
    }
    servo_set_us(PIN_SERVO, SERVO_OPEN_US);
    sleep_ms(50);
    servo_idle(PIN_SERVO);
    g_cut.state = CUT_IDLE;
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
            if (age >= (uint32_t)SERVO_SETTLE_MS) {
                g_cut.phase_start_ms = now_ms;
                g_cut.state = CUT_FEEDING;
            } else if (age > (uint32_t)CUT_TIMEOUT_SETTLE_MS) {
                cutter_abort();
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
            if (age >= (uint32_t)SERVO_SETTLE_MS) {
                g_cut.phase_start_ms = now_ms;
                g_cut.state = CUT_REOPENING;
            } else if (age > (uint32_t)CUT_TIMEOUT_SETTLE_MS) {
                cutter_abort();
            }
            break;

        case CUT_REOPENING:
            servo_set_us(PIN_SERVO, SERVO_OPEN_US);
            g_cut.phase_start_ms = now_ms;
            g_cut.state = CUT_REOPEN_WAIT;
            break;

        case CUT_REOPEN_WAIT:
            if (age >= (uint32_t)SERVO_SETTLE_MS) {
                g_cut.state = CUT_REPEAT_CHECK;
            } else if (age > (uint32_t)CUT_TIMEOUT_SETTLE_MS) {
                cutter_abort();
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
    if (target_lane != 1 && target_lane != 2) return;

    g_tc_ctx.target_lane = target_lane;
    g_tc_ctx.from_lane = active_lane;
    g_tc_ctx.sync_was_enabled = sync_enabled;
    g_tc_ctx.phase_start_ms = now_ms;

    sync_enabled = false;
    g_tc_ctx.state = (target_lane == active_lane) ? TC_LOAD_START : TC_UNLOAD_CUT;
}

static void tc_abort(void) {
    if (g_tc_ctx.state == TC_IDLE) return;
    stop_all();
    cutter_abort();
    sync_enabled = false;
    g_tc_ctx.state = TC_IDLE;
    cmd_event("TC:ERROR", "ABORTED");
}

static const char *tc_state_name(tc_state_t s) {
    switch (s) {
        case TC_IDLE: return "IDLE";
        case TC_UNLOAD_CUT: return "UNLOAD_CUT";
        case TC_UNLOAD_WAIT_CUT: return "UNLOAD_WAIT_CUT";
        case TC_UNLOAD_REVERSE: return "UNLOAD_REVERSE";
        case TC_UNLOAD_WAIT_OUT: return "UNLOAD_WAIT_OUT";
        case TC_UNLOAD_WAIT_TH: return "UNLOAD_WAIT_TH";
        case TC_UNLOAD_DONE: return "UNLOAD_DONE";
        case TC_SWAP: return "SWAP";
        case TC_LOAD_START: return "LOAD_START";
        case TC_LOAD_WAIT_OUT: return "LOAD_WAIT_OUT";
        case TC_LOAD_WAIT_TH: return "LOAD_WAIT_TH";
        case TC_LOAD_DONE: return "LOAD_DONE";
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
            lane_start(A, TASK_UNLOAD, REV_SPS, false, now_ms, 0);
            g_tc_ctx.phase_start_ms = now_ms;
            g_tc_ctx.state = TC_UNLOAD_WAIT_OUT;
            break;
        }

        case TC_UNLOAD_WAIT_OUT:
            if (!lane_out_present(A)) {
                lane_stop(A);
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = (TC_TIMEOUT_TH_MS > 0) ? TC_UNLOAD_WAIT_TH : TC_UNLOAD_DONE;
            } else if (age > (uint32_t)TC_TIMEOUT_UNLOAD_MS) {
                tc_enter_error("UNLOAD_TIMEOUT");
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
            // A speed-ramp phase can be inserted here if needed.
            char lane_s[2] = { (char)('0' + active_lane), 0 };
            cmd_event("TC:LOADING", lane_s);
            lane_start(A, TASK_AUTOLOAD, AUTO_SPS, true, now_ms, TC_TIMEOUT_LOAD_MS);
            g_tc_ctx.phase_start_ms = now_ms;
            g_tc_ctx.state = TC_LOAD_WAIT_OUT;
            break;
        }

        case TC_LOAD_WAIT_OUT:
            if (lane_out_present(A)) {
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = (TC_TIMEOUT_TH_MS > 0) ? TC_LOAD_WAIT_TH : TC_LOAD_DONE;
            } else if (age > (uint32_t)TC_TIMEOUT_LOAD_MS) {
                tc_enter_error("LOAD_TIMEOUT");
            }
            break;

        case TC_LOAD_WAIT_TH:
            if (toolhead_has_filament || age > (uint32_t)TC_TIMEOUT_TH_MS) {
                g_tc_ctx.state = TC_LOAD_DONE;
            }
            break;

        case TC_LOAD_DONE: {
            char lane_s[2] = { (char)('0' + active_lane), 0 };
            cmd_event("TC:DONE", lane_s);
            sync_enabled = g_tc_ctx.sync_was_enabled;
            g_tc_ctx.state = TC_IDLE;
            break;
        }
    }
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

static buf_state_t buf_read(void) {
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
    if (sync_current_sps > 0) {
        if (A->task != TASK_FEED) {
            lane_start(A, TASK_FEED, sync_current_sps, true, g_now_ms, 0);
        } else {
            motor_set_rate_sps(&A->m, sync_current_sps);
            motor_enable(&A->m, true);
            motor_set_dir(&A->m, true);
        }
    } else {
        if (A->task == TASK_FEED) {
            lane_stop(A);
        }
    }
}

static void sync_on_transition(buf_state_t prev, buf_state_t now_state) {
    int delta = (int)(g_buf.arm_vel_mm_s / MM_PER_STEP * SYNC_RATIO);
    if (now_state == BUF_ADVANCE) {
        sync_current_sps += delta;
    } else if (now_state == BUF_TRAILING) {
        sync_current_sps -= delta;
    }

    if (prev == BUF_ADVANCE && now_state == BUF_MID) {
        baseline_update_on_settle(g_buf.dwell_ms);
    }
}

static void sync_tick(uint32_t now_ms) {
    if (!sync_enabled || tc_state() != TC_IDLE) return;
    if ((now_ms - sync_last_tick_ms) < (uint32_t)SYNC_TICK_MS) return;

    sync_last_tick_ms = now_ms;

    buf_state_t prev = g_buf.state;
    buf_state_t s = buf_read_stable(now_ms);

    if (s == BUF_FAULT) {
        sync_current_sps = 0;
        sync_apply_to_active();
        cmd_event("BS", "FAULT,0");
        return;
    }

    if (s != prev) {
        buf_update(s, now_ms);
        sync_on_transition(prev, s);
    } else {
        switch (s) {
            case BUF_ADVANCE:
                sync_current_sps += SYNC_RAMP_UP_SPS;
                break;
            case BUF_TRAILING:
                sync_current_sps -= SYNC_RAMP_DN_SPS;
                if (sync_current_sps < 0) sync_current_sps = 0;
                break;
            case BUF_MID: {
                int target = g_baseline_sps + (predict_advance_coming() ? PRE_RAMP_SPS : 0);
                if (sync_current_sps > target) sync_current_sps -= SYNC_RAMP_DN_SPS;
                else if (sync_current_sps < target) sync_current_sps += SYNC_RAMP_UP_SPS;
                break;
            }
            case BUF_FAULT:
                break;
        }
    }

    sync_current_sps = clamp_i(sync_current_sps, SYNC_MIN_SPS, SYNC_MAX_SPS);
    sync_apply_to_active();

    if ((now_ms - sync_last_evt_ms) >= 500u) {
        sync_last_evt_ms = now_ms;
        char ev[32];
        snprintf(ev, sizeof(ev), "%s,%d", buf_state_name(s), sync_current_sps);
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

static void stall_irq(uint gpio, uint32_t events) {
    if (!(events & GPIO_IRQ_EDGE_RISE)) return;

    if (gpio == PIN_M1_DIAG && g_lane1.stall_armed) {
        motor_stop(&g_lane1.m);
        g_lane1.task = TASK_IDLE;
        stall_pending_l1 = true;
    }
    if (gpio == PIN_M2_DIAG && g_lane2.stall_armed) {
        motor_stop(&g_lane2.m);
        g_lane2.task = TASK_IDLE;
        stall_pending_l2 = true;
    }
}

static void stall_init(void) {
    gpio_init(PIN_M1_DIAG);
    gpio_set_dir(PIN_M1_DIAG, GPIO_IN);
    gpio_pull_down(PIN_M1_DIAG);

    gpio_init(PIN_M2_DIAG);
    gpio_set_dir(PIN_M2_DIAG, GPIO_IN);
    gpio_pull_down(PIN_M2_DIAG);

    gpio_set_irq_enabled_with_callback(PIN_M1_DIAG, GPIO_IRQ_EDGE_RISE, true, &stall_irq);
    gpio_set_irq_enabled(PIN_M2_DIAG, GPIO_IRQ_EDGE_RISE, true);
}

static void stall_pump(void) {
    if (stall_pending_l1) {
        stall_pending_l1 = false;
        lane_fault(&g_lane1, FAULT_STALL);
        cmd_event("STALL", "1");
    }

    if (stall_pending_l2) {
        stall_pending_l2 = false;
        lane_fault(&g_lane2, FAULT_STALL);
        cmd_event("STALL", "2");
    }
}

// ===================== Settings persistence =====================
#define SETTINGS_FLASH_OFFSET (PICO_FLASH_SIZE_BYTES - FLASH_SECTOR_SIZE)
#define SETTINGS_MAGIC 0x4E314F57u // 'N1OW' - NightOwl settings sentinel.
#define SETTINGS_VERSION 1u

typedef struct {
    uint32_t magic;
    uint32_t version;

    int feed_sps, rev_sps, auto_sps;
    int sync_max_sps, sync_min_sps;
    int sync_ramp_up, sync_ramp_dn;
    int sync_tick_ms, pre_ramp_sps;
    float sync_ratio;
    float buf_half_travel_mm;
    int buf_hyst_ms, buf_predict_thr_ms;
    float baseline_alpha;
    bool buf_invert;

    int motion_startup_ms;
    int sgt_l1, sgt_l2;
    int tcoolthrs;

    int run_current_ma, hold_current_ma;
    int microsteps;
    bool spreadcycle;

    int servo_open_us, servo_close_us, servo_block_us;
    int servo_settle_ms;
    int cut_feed_mm, cut_length_mm, cut_amount;

    int tc_timeout_cut_ms, tc_timeout_unload_ms;
    int tc_timeout_th_ms, tc_timeout_load_ms;

    int low_delay_ms, swap_cooldown_ms, runout_cooldown_ms;
    bool require_y_empty_swap;

    int ramp_step_sps, ramp_tick_ms;

    uint32_t crc32;
} settings_t;
_Static_assert(sizeof(settings_t) <= 256,
    "settings_t exceeds one flash page - add multi-page loop to settings_save()");

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

    SYNC_MAX_SPS = 8000;
    SYNC_MIN_SPS = 0;
    SYNC_RAMP_UP_SPS = 300;
    SYNC_RAMP_DN_SPS = 150;
    SYNC_TICK_MS = 20;
    PRE_RAMP_SPS = 400;
    SYNC_RATIO = 1.0f;
    BUF_HALF_TRAVEL_MM = 5.0f;
    BUF_HYST_MS = 30;
    BUF_PREDICT_THR_MS = 250;
    g_baseline_alpha = 0.15f;
    BUF_INVERT = false;

    MOTION_STARTUP_MS = 10000;
    TMC_SGT_L1 = 80;
    TMC_SGT_L2 = 80;
    TMC_TCOOLTHRS = 400;

    TMC_RUN_CURRENT_MA = 850;
    TMC_HOLD_CURRENT_MA = 300;
    TMC_MICROSTEPS = CONF_MICROSTEPS;
    TMC_SPREADCYCLE = true;

    SERVO_OPEN_US = 500;
    SERVO_CLOSE_US = 1400;
    SERVO_BLOCK_US = 950;
    SERVO_SETTLE_MS = 500;
    CUT_FEED_MM = 48;
    CUT_LENGTH_MM = 10;
    CUT_AMOUNT = 1;

    TC_TIMEOUT_CUT_MS = 5000;
    TC_TIMEOUT_UNLOAD_MS = 8000;
    TC_TIMEOUT_TH_MS = 3000;
    TC_TIMEOUT_LOAD_MS = 15000;

    LOW_DELAY_MS = 400;
    SWAP_COOLDOWN_MS = 500;
    RUNOUT_COOLDOWN_MS = 12000;
    REQUIRE_Y_EMPTY_SWAP = true;

    RAMP_STEP_SPS = 200;
    RAMP_TICK_MS = 5;
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
    s.sync_ratio = SYNC_RATIO;
    s.buf_half_travel_mm = BUF_HALF_TRAVEL_MM;
    s.buf_hyst_ms = BUF_HYST_MS;
    s.buf_predict_thr_ms = BUF_PREDICT_THR_MS;
    s.baseline_alpha = g_baseline_alpha;
    s.buf_invert = BUF_INVERT;

    s.motion_startup_ms = MOTION_STARTUP_MS;
    s.sgt_l1 = TMC_SGT_L1;
    s.sgt_l2 = TMC_SGT_L2;
    s.tcoolthrs = TMC_TCOOLTHRS;

    s.run_current_ma = TMC_RUN_CURRENT_MA;
    s.hold_current_ma = TMC_HOLD_CURRENT_MA;
    s.microsteps = TMC_MICROSTEPS;
    s.spreadcycle = TMC_SPREADCYCLE;

    s.servo_open_us = SERVO_OPEN_US;
    s.servo_close_us = SERVO_CLOSE_US;
    s.servo_block_us = SERVO_BLOCK_US;
    s.servo_settle_ms = SERVO_SETTLE_MS;
    s.cut_feed_mm = CUT_FEED_MM;
    s.cut_length_mm = CUT_LENGTH_MM;
    s.cut_amount = CUT_AMOUNT;

    s.tc_timeout_cut_ms = TC_TIMEOUT_CUT_MS;
    s.tc_timeout_unload_ms = TC_TIMEOUT_UNLOAD_MS;
    s.tc_timeout_th_ms = TC_TIMEOUT_TH_MS;
    s.tc_timeout_load_ms = TC_TIMEOUT_LOAD_MS;

    s.low_delay_ms = LOW_DELAY_MS;
    s.swap_cooldown_ms = SWAP_COOLDOWN_MS;
    s.runout_cooldown_ms = RUNOUT_COOLDOWN_MS;
    s.require_y_empty_swap = REQUIRE_Y_EMPTY_SWAP;

    s.ramp_step_sps = RAMP_STEP_SPS;
    s.ramp_tick_ms = RAMP_TICK_MS;

    s.crc32 = crc32_buf((const uint8_t *)&s, offsetof(settings_t, crc32));

    uint8_t page[FLASH_PAGE_SIZE] = {0};
    memcpy(page, &s, sizeof(s));

    stop_all();

    uint32_t ints = save_and_disable_interrupts();
    flash_range_erase(SETTINGS_FLASH_OFFSET, FLASH_SECTOR_SIZE);
    flash_range_program(SETTINGS_FLASH_OFFSET, page, FLASH_PAGE_SIZE);
    restore_interrupts(ints);
}

static void tmc_apply_all(void) {
    tmc_set_spreadcycle(&g_tmc1, TMC_SPREADCYCLE);
    tmc_set_spreadcycle(&g_tmc2, TMC_SPREADCYCLE);
    tmc_set_microsteps(&g_tmc1, TMC_MICROSTEPS);
    tmc_set_microsteps(&g_tmc2, TMC_MICROSTEPS);
    tmc_set_run_current_ma(&g_tmc1, TMC_RUN_CURRENT_MA, TMC_HOLD_CURRENT_MA);
    tmc_set_run_current_ma(&g_tmc2, TMC_RUN_CURRENT_MA, TMC_HOLD_CURRENT_MA);
    tmc_set_tcoolthrs(&g_tmc1, (uint32_t)TMC_TCOOLTHRS);
    tmc_set_tcoolthrs(&g_tmc2, (uint32_t)TMC_TCOOLTHRS);
    tmc_set_sgthrs(&g_tmc1, (uint8_t)TMC_SGT_L1);
    tmc_set_sgthrs(&g_tmc2, (uint8_t)TMC_SGT_L2);
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
    SYNC_RATIO = s->sync_ratio;
    BUF_HALF_TRAVEL_MM = s->buf_half_travel_mm;
    BUF_HYST_MS = s->buf_hyst_ms;
    BUF_PREDICT_THR_MS = s->buf_predict_thr_ms;
    g_baseline_alpha = s->baseline_alpha;
    BUF_INVERT = s->buf_invert;

    MOTION_STARTUP_MS = s->motion_startup_ms;
    TMC_SGT_L1 = s->sgt_l1;
    TMC_SGT_L2 = s->sgt_l2;
    TMC_TCOOLTHRS = s->tcoolthrs;

    TMC_RUN_CURRENT_MA = s->run_current_ma;
    TMC_HOLD_CURRENT_MA = s->hold_current_ma;
    TMC_MICROSTEPS = s->microsteps;
    TMC_SPREADCYCLE = s->spreadcycle;

    SERVO_OPEN_US = s->servo_open_us;
    SERVO_CLOSE_US = s->servo_close_us;
    SERVO_BLOCK_US = s->servo_block_us;
    SERVO_SETTLE_MS = s->servo_settle_ms;
    CUT_FEED_MM = s->cut_feed_mm;
    CUT_LENGTH_MM = s->cut_length_mm;
    CUT_AMOUNT = s->cut_amount;

    TC_TIMEOUT_CUT_MS = s->tc_timeout_cut_ms;
    TC_TIMEOUT_UNLOAD_MS = s->tc_timeout_unload_ms;
    TC_TIMEOUT_TH_MS = s->tc_timeout_th_ms;
    TC_TIMEOUT_LOAD_MS = s->tc_timeout_load_ms;

    LOW_DELAY_MS = s->low_delay_ms;
    SWAP_COOLDOWN_MS = s->swap_cooldown_ms;
    RUNOUT_COOLDOWN_MS = s->runout_cooldown_ms;
    REQUIRE_Y_EMPTY_SWAP = s->require_y_empty_swap;

    RAMP_STEP_SPS = s->ramp_step_sps;
    RAMP_TICK_MS = s->ramp_tick_ms;

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
    uint16_t sg1 = 0;
    uint16_t sg2 = 0;
    (void)tmc_read_sg_result(&g_tmc1, &sg1);
    (void)tmc_read_sg_result(&g_tmc2, &sg2);

    char b[224];
    snprintf(b, sizeof(b),
        "LN:%d,TC:%s,L1T:%s,L2T:%s,"
        "I1:%d,O1:%d,I2:%d,O2:%d,"
        "TH:%d,YS:%d,BUF:%s,SPS:%d,BL:%d,SM:%d,BI:%d,"
        "SG1:%u,SG2:%u",
        active_lane, tc_state_name(g_tc_ctx.state),
        task_name(g_lane1.task), task_name(g_lane2.task),
        lane_in_present(&g_lane1) ? 1 : 0,
        lane_out_present(&g_lane1) ? 1 : 0,
        lane_in_present(&g_lane2) ? 1 : 0,
        lane_out_present(&g_lane2) ? 1 : 0,
        toolhead_has_filament ? 1 : 0,
        on_al(&g_y_split) ? 1 : 0,
        buf_state_name(g_buf.state),
        sync_current_sps,
        g_baseline_sps,
        sync_enabled ? 1 : 0,
        BUF_INVERT ? 1 : 0,
        sg1,
        sg2);

    cmd_reply("OK", b);
}

static void cmd_execute(const char *cmd, const char *p, uint32_t now_ms) {
    if (!strcmp(cmd, "TC")) {
        int ln = atoi(p);
        if (ln == 1 || ln == 2) {
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
        sync_enabled = false;
        lane_start(A, TASK_AUTOLOAD, AUTO_SPS, true, now_ms, 6000);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "UL")) {
        lane_t *A = lane_ptr(active_lane);
        sync_enabled = false;
        lane_start(A, TASK_UNLOAD, REV_SPS, false, now_ms, 0);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "CU")) {
        lane_t *A = lane_ptr(active_lane);
        sync_enabled = false;
        cutter_start(A, now_ms);
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "ST")) {
        tc_abort();
        cutter_abort();
        stop_all();
        cmd_reply("OK", NULL);
    } else if (!strcmp(cmd, "TS")) {
        int v = atoi(p);
        if (v == 0 || v == 1) {
            toolhead_has_filament = (v == 1);
            cmd_reply("OK", NULL);
        } else {
            cmd_reply("ER", "ARG");
        }
    } else if (!strcmp(cmd, "SM")) {
        int v = atoi(p);
        if (v == 0 || v == 1) {
            sync_enabled = (v == 1);
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
            tmc_t *t = (ln == 1) ? &g_tmc1 : &g_tmc2;
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
            tmc_t *t = (ln == 1) ? &g_tmc1 : &g_tmc2;
            if (tmc_set_run_current_ma(t, ma, TMC_HOLD_CURRENT_MA)) {
                TMC_RUN_CURRENT_MA = ma;
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
            cmd_reply("ER", "SET:UNKNOWN_PARAM");
        } else {
            int iv = atoi(val_str);
            float fv = (float)atof(val_str);
            bool handled = true;
            if      (!strcmp(param, "FEED_SPS"))     FEED_SPS = clamp_i(iv, 200, 30000);
            else if (!strcmp(param, "REV_SPS"))      REV_SPS = clamp_i(iv, 200, 30000);
            else if (!strcmp(param, "AUTO_SPS"))     AUTO_SPS = clamp_i(iv, 200, 30000);
            else if (!strcmp(param, "SYNC_MAX"))     SYNC_MAX_SPS = clamp_i(iv, 200, 30000);
            else if (!strcmp(param, "SYNC_MIN"))     SYNC_MIN_SPS = clamp_i(iv, 0, 30000);
            else if (!strcmp(param, "SYNC_UP"))      SYNC_RAMP_UP_SPS = clamp_i(iv, 10, 2000);
            else if (!strcmp(param, "SYNC_DN"))      SYNC_RAMP_DN_SPS = clamp_i(iv, 10, 2000);
            else if (!strcmp(param, "SYNC_RATIO"))   { SYNC_RATIO = fv < 0.1f ? 0.1f : fv > 5.0f ? 5.0f : fv; }
            else if (!strcmp(param, "PRE_RAMP"))     PRE_RAMP_SPS = clamp_i(iv, 0, 2000);
            else if (!strcmp(param, "BUF_TRAVEL"))   { BUF_HALF_TRAVEL_MM = fv < 1.0f ? 1.0f : fv > 50.0f ? 50.0f : fv; }
            else if (!strcmp(param, "BUF_HYST"))     BUF_HYST_MS = clamp_i(iv, 5, 500);
            else if (!strcmp(param, "BASELINE"))     g_baseline_sps = clamp_i(iv, 200, 30000);
            else if (!strcmp(param, "STARTUP_MS"))   MOTION_STARTUP_MS = clamp_i(iv, 0, 30000);
            else if (!strcmp(param, "SERVO_OPEN"))   SERVO_OPEN_US = clamp_i(iv, 400, 2600);
            else if (!strcmp(param, "SERVO_CLOSE"))  SERVO_CLOSE_US = clamp_i(iv, 400, 2600);
            else if (!strcmp(param, "SERVO_SETTLE")) SERVO_SETTLE_MS = clamp_i(iv, 100, 2000);
            else if (!strcmp(param, "CUT_FEED"))     CUT_FEED_MM = clamp_i(iv, 1, 200);
            else if (!strcmp(param, "CUT_LEN"))      CUT_LENGTH_MM = clamp_i(iv, 1, 50);
            else if (!strcmp(param, "CUT_AMT"))      CUT_AMOUNT = clamp_i(iv, 1, 5);
            else if (!strcmp(param, "TC_CUT_MS"))    TC_TIMEOUT_CUT_MS = clamp_i(iv, 1000, 30000);
            else if (!strcmp(param, "TC_UNLOAD_MS")) TC_TIMEOUT_UNLOAD_MS = clamp_i(iv, 1000, 30000);
            else if (!strcmp(param, "TC_TH_MS"))     TC_TIMEOUT_TH_MS = clamp_i(iv, 0, 10000);
            else if (!strcmp(param, "TC_LOAD_MS"))   TC_TIMEOUT_LOAD_MS = clamp_i(iv, 1000, 60000);
            else handled = false;
            if (handled) cmd_reply("OK", NULL);
            else cmd_reply("ER", "SET:UNKNOWN_PARAM");
        }
    } else if (!strcmp(cmd, "GET")) {
        char out[48];
        bool handled = true;
        if      (!strcmp(p, "FEED_SPS"))     snprintf(out, sizeof(out), "FEED_SPS:%d", FEED_SPS);
        else if (!strcmp(p, "REV_SPS"))      snprintf(out, sizeof(out), "REV_SPS:%d", REV_SPS);
        else if (!strcmp(p, "AUTO_SPS"))     snprintf(out, sizeof(out), "AUTO_SPS:%d", AUTO_SPS);
        else if (!strcmp(p, "SYNC_MAX"))     snprintf(out, sizeof(out), "SYNC_MAX:%d", SYNC_MAX_SPS);
        else if (!strcmp(p, "SYNC_MIN"))     snprintf(out, sizeof(out), "SYNC_MIN:%d", SYNC_MIN_SPS);
        else if (!strcmp(p, "SYNC_UP"))      snprintf(out, sizeof(out), "SYNC_UP:%d", SYNC_RAMP_UP_SPS);
        else if (!strcmp(p, "SYNC_DN"))      snprintf(out, sizeof(out), "SYNC_DN:%d", SYNC_RAMP_DN_SPS);
        else if (!strcmp(p, "SYNC_RATIO"))   snprintf(out, sizeof(out), "SYNC_RATIO:%.3f", (double)SYNC_RATIO);
        else if (!strcmp(p, "PRE_RAMP"))     snprintf(out, sizeof(out), "PRE_RAMP:%d", PRE_RAMP_SPS);
        else if (!strcmp(p, "BUF_TRAVEL"))   snprintf(out, sizeof(out), "BUF_TRAVEL:%.3f", (double)BUF_HALF_TRAVEL_MM);
        else if (!strcmp(p, "BUF_HYST"))     snprintf(out, sizeof(out), "BUF_HYST:%d", BUF_HYST_MS);
        else if (!strcmp(p, "BASELINE"))     snprintf(out, sizeof(out), "BASELINE:%d", g_baseline_sps);
        else if (!strcmp(p, "STARTUP_MS"))   snprintf(out, sizeof(out), "STARTUP_MS:%d", MOTION_STARTUP_MS);
        else if (!strcmp(p, "SERVO_OPEN"))   snprintf(out, sizeof(out), "SERVO_OPEN:%d", SERVO_OPEN_US);
        else if (!strcmp(p, "SERVO_CLOSE"))  snprintf(out, sizeof(out), "SERVO_CLOSE:%d", SERVO_CLOSE_US);
        else if (!strcmp(p, "SERVO_SETTLE")) snprintf(out, sizeof(out), "SERVO_SETTLE:%d", SERVO_SETTLE_MS);
        else if (!strcmp(p, "CUT_FEED"))     snprintf(out, sizeof(out), "CUT_FEED:%d", CUT_FEED_MM);
        else if (!strcmp(p, "CUT_LEN"))      snprintf(out, sizeof(out), "CUT_LEN:%d", CUT_LENGTH_MM);
        else if (!strcmp(p, "CUT_AMT"))      snprintf(out, sizeof(out), "CUT_AMT:%d", CUT_AMOUNT);
        else if (!strcmp(p, "TC_CUT_MS"))    snprintf(out, sizeof(out), "TC_CUT_MS:%d", TC_TIMEOUT_CUT_MS);
        else if (!strcmp(p, "TC_UNLOAD_MS")) snprintf(out, sizeof(out), "TC_UNLOAD_MS:%d", TC_TIMEOUT_UNLOAD_MS);
        else if (!strcmp(p, "TC_TH_MS"))     snprintf(out, sizeof(out), "TC_TH_MS:%d", TC_TIMEOUT_TH_MS);
        else if (!strcmp(p, "TC_LOAD_MS"))   snprintf(out, sizeof(out), "TC_LOAD_MS:%d", TC_TIMEOUT_LOAD_MS);
        else handled = false;
        if (handled) cmd_reply("OK", out);
        else cmd_reply("ER", "GET:UNKNOWN_PARAM");
    } else if (!strcmp(cmd, "RR")) {
        // Raw read on tx_pin: N=0→no response, N=8→full frame. Scans addr 0-3.
        // Usage: RR:1 or RR:2
        int ln = atoi(p);
        if (ln != 1 && ln != 2) {
            cmd_reply("ER", "ARG");
        } else {
            tmc_t probe = (ln == 1) ? g_tmc1 : g_tmc2;
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
    if (g_lane1.fault || g_lane2.fault || g_tc_ctx.state == TC_ERROR) return LED_ERROR;
    if (g_cut.state != CUT_IDLE) return LED_CUTTING;
    if (g_tc_ctx.state != TC_IDLE) return LED_TC;
    if (g_lane1.task == TASK_AUTOLOAD || g_lane2.task == TASK_AUTOLOAD) return LED_LOADING;
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

    motor_t m1;
    motor_t m2;
    motor_init(&m1, PIN_M1_EN, PIN_M1_DIR, PIN_M1_STEP, M1_DIR_INVERT);
    motor_init(&m2, PIN_M2_EN, PIN_M2_DIR, PIN_M2_STEP, M2_DIR_INVERT);

    tmc_init(&g_tmc1, PIN_M1_UART_TX, PIN_M1_UART_RX, 0);
    tmc_init(&g_tmc2, PIN_M2_UART_TX, PIN_M2_UART_RX, 0);

    lane_setup(&g_lane1, PIN_L1_IN, PIN_L1_OUT, m1, 1, PIN_M1_DIAG, &g_tmc1);
    lane_setup(&g_lane2, PIN_L2_IN, PIN_L2_OUT, m2, 2, PIN_M2_DIAG, &g_tmc2);

    din_init(&g_y_split, PIN_Y_SPLIT);
    din_init(&g_buf_adv_din, PIN_BUF_ADVANCE);
    din_init(&g_buf_trl_din, PIN_BUF_TRAILING);

    servo_init(PIN_SERVO);
    servo_set_us(PIN_SERVO, SERVO_BLOCK_US);

    stall_init();
    neopixel_init(PIN_NEOPIXEL);

    settings_load();
    g_buf.entered_ms = to_ms_since_boot(get_absolute_time());

    while (true) {
        g_now_ms = to_ms_since_boot(get_absolute_time());

        // Inputs
        din_update(&g_lane1.in_sw);
        din_update(&g_lane1.out_sw);
        din_update(&g_lane2.in_sw);
        din_update(&g_lane2.out_sw);
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
        lane_tick(&g_lane1, g_now_ms);
        lane_tick(&g_lane2, g_now_ms);
        sync_tick(g_now_ms);

        // Local indicator
        neopixel_tick(g_now_ms);

        sleep_us(100);
    }
}
