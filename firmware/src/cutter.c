#include "cutter.h"

#include <stdio.h>
#include <string.h>

#include "hardware/pwm.h"

#include "motion.h"
#include "protocol.h"
#include "toolchange.h"

typedef enum {
    CUT_IDLE,
    CUT_BOOT_PARK,
    CUT_TEST,
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
    int current_sps;
    uint32_t ramp_last_ms;
} cutter_ctx_t;

cutter_ctx_t g_cut;

static uint g_servo_slice = 0;
static uint g_servo_chan = 0;

static void servo_init(uint pin) {
    gpio_set_function(pin, GPIO_FUNC_PWM);
    g_servo_slice = pwm_gpio_to_slice_num(pin);
    g_servo_chan = pwm_gpio_to_channel(pin);

    pwm_config config = pwm_get_default_config();
    pwm_config_set_clkdiv(&config, 125.0f);
    pwm_config_set_wrap(&config, 20000 - 1);
    pwm_init(g_servo_slice, &config, false);
}

static void servo_set_us(uint pin, uint pulse_us) {
    (void)pin;
    if (pulse_us < 400) pulse_us = 400;
    if (pulse_us > 2700) pulse_us = 2700;
    pwm_set_chan_level(g_servo_slice, g_servo_chan, (uint16_t)pulse_us);
    pwm_set_enabled(g_servo_slice, true);
}

static void servo_idle(uint pin) {
    (void)pin;
    pwm_set_enabled(g_servo_slice, false);
}

bool cutter_busy(void) {
    return g_cut.state != CUT_IDLE;
}

static uint32_t cut_feed_ms_for_mm(int mm, int idx) {
    float secs = (float)mm / ((float)CUT_FEED_SPS * MM_PER_STEP[idx]);
    if (secs < 0.0f) secs = 0.0f;
    return (uint32_t)(secs * 1000.0f);
}

static void cut_begin_feed(uint32_t now_ms, uint32_t window_ms) {
    g_cut.feed_active_ms = window_ms;
    if (g_cut.lane && window_ms > 0) {
        motor_enable(&g_cut.lane->m, true);
        motor_set_dir(&g_cut.lane->m, true);
        g_cut.current_sps = RAMP_STEP_SPS;
        motor_set_rate_sps(&g_cut.lane->m, g_cut.current_sps);
        g_cut.ramp_last_ms = now_ms;
    } else {
        g_cut.current_sps = 0;
    }
    g_cut.phase_start_ms = now_ms;
    g_cut.state = CUT_FEED_WAIT;
}

void cutter_init(void) {
    servo_init(PIN_SERVO);
    servo_set_us(PIN_SERVO, SERVO_BLOCK_US);
    g_cut.state = CUT_BOOT_PARK;
    g_cut.phase_start_ms = to_ms_since_boot(get_absolute_time());
}

void cutter_start(lane_t *L, bool enable_feed, uint32_t now_ms) {
    if (g_cut.state != CUT_IDLE && g_cut.state != CUT_BOOT_PARK) return;

    g_cut.lane = L;
    g_cut.repeats_done = 0;

    if (L && enable_feed) {
        int idx = L->lane_id - 1;
        g_cut.feed_initial_ms = cut_feed_ms_for_mm(CUT_FEED_MM, idx);
        g_cut.feed_repeat_ms = cut_feed_ms_for_mm(CUT_LENGTH_MM, idx);
    } else {
        g_cut.feed_initial_ms = 0;
        g_cut.feed_repeat_ms = 0;
    }

    g_cut.phase_start_ms = now_ms;
    g_cut.state = CUT_OPENING;

    if (L) {
        char lane_s[2] = { (char)('0' + L->lane_id), 0 };
        cmd_event("TC:CUTTING", lane_s);
    } else {
        cmd_event("TC:CUTTING", "BARE");
    }
}

void cutter_abort(void) {
    if (g_cut.state == CUT_IDLE) return;
    servo_set_us(PIN_SERVO, SERVO_BLOCK_US);
    if (g_cut.lane) motor_stop(&g_cut.lane->m);
    g_cut.state = CUT_IDLE;
    cmd_event("CUT:ERROR", "ABORTED");
}

void cutter_test_us(uint32_t us) {
    if (g_cut.lane) motor_stop(&g_cut.lane->m);
    servo_set_us(PIN_SERVO, us);
    g_cut.state = CUT_TEST;
    g_cut.phase_start_ms = to_ms_since_boot(get_absolute_time());
}

void cutter_tick(uint32_t now_ms) {
    uint32_t age = now_ms - g_cut.phase_start_ms;

    switch (g_cut.state) {
        case CUT_IDLE:
            return;
            
        case CUT_BOOT_PARK:
        case CUT_TEST:
            if (age >= (uint32_t)SERVO_SETTLE_MS) {
                servo_idle(PIN_SERVO);
                g_cut.state = CUT_IDLE;
            }
            break;

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
            if (g_cut.lane && g_cut.current_sps < CUT_FEED_SPS) {
                if ((int32_t)(now_ms - g_cut.ramp_last_ms) >= RAMP_TICK_MS) {
                    g_cut.current_sps += RAMP_STEP_SPS;
                    if (g_cut.current_sps > CUT_FEED_SPS) g_cut.current_sps = CUT_FEED_SPS;
                    motor_set_rate_sps(&g_cut.lane->m, g_cut.current_sps);
                    g_cut.ramp_last_ms = now_ms;
                }
            }
            if (age >= g_cut.feed_active_ms) {
                if (g_cut.lane && g_cut.feed_active_ms > 0) {
                    motor_stop(&g_cut.lane->m);
                }
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
                cmd_event("CUT:DONE", NULL);
            }
            break;
    }
}
