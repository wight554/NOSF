#include "toolchange.h"

#include <stdio.h>
#include <string.h>

#include "hardware/pwm.h"

#include "motion.h"
#include "protocol.h"
#include "sync.h"

#define RELOAD_TRAILING_SOFT_WALL_MS 900.0f
#define RELOAD_TRAILING_HARD_WALL_MS 450.0f
#define RELOAD_TRAILING_HARD_PUSH_MM_S 0.25f
#define RELOAD_TRAILING_HARD_HOLD_MS 250u

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
    if (pulse_us < 500) pulse_us = 500;
    if (pulse_us > 2500) pulse_us = 2500;
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
    float secs = (float)mm / ((float)REV_SPS * MM_PER_STEP[idx]);
    if (secs < 0.0f) secs = 0.0f;
    return (uint32_t)(secs * 1000.0f);
}

static void cut_begin_feed(uint32_t now_ms, uint32_t window_ms) {
    g_cut.feed_active_ms = window_ms;
    motor_enable(&g_cut.lane->m, true);
    motor_set_dir(&g_cut.lane->m, true);
    motor_set_rate_sps(&g_cut.lane->m, REV_SPS);
    g_cut.phase_start_ms = now_ms;
    g_cut.state = CUT_FEED_WAIT;
}

void toolchange_init(void) {
    servo_init(PIN_SERVO);
    servo_set_us(PIN_SERVO, SERVO_BLOCK_US);
}

void cutter_start(lane_t *L, bool enable_feed, uint32_t now_ms) {
    if (g_cut.state != CUT_IDLE) return;

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

void cutter_tick(uint32_t now_ms) {
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
                cmd_event("CUT:DONE", NULL);
            }
            break;
    }
}

tc_state_t tc_state(void) {
    return g_tc_ctx.state;
}

void tc_enter_error(const char *reason) {
    cmd_event("TC:ERROR", reason);
    stop_all();
    cutter_abort();
    g_tc_ctx.state = TC_ERROR;
}

void tc_start(int target_lane, uint32_t now_ms) {
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
        g_tc_ctx.state = TC_UNLOAD_REVERSE;
    }
}

void tc_manual_reload(uint32_t now_ms) {
    if (g_tc_ctx.state != TC_IDLE && g_tc_ctx.state != TC_ERROR) return;
    lane_t *A = lane_ptr(active_lane);
    if (!A) return;

    memset(&g_tc_ctx, 0, sizeof(g_tc_ctx));
    g_tc_ctx.target_lane = active_lane;
    g_tc_ctx.from_lane = active_lane;
    set_toolhead_filament(false);
    
    char lane_s[2] = { (char)('0' + active_lane), 0 };
    cmd_event("RELOAD:JOINING", lane_s);
    
    int approach_sps = FEED_SPS;
    if (approach_sps <= 0) approach_sps = JOIN_SPS;
    
    lane_start(A, TASK_FEED, approach_sps, true, now_ms, 2000.0f);
    
    g_tc_ctx.ready_to_join_since_ms = 0;
    g_tc_ctx.reload_tick_ms = now_ms;
    g_tc_ctx.reload_current_sps = 0;
    g_tc_ctx.last_trailing_ms = 0;
    g_tc_ctx.phase_start_ms = now_ms;
    g_tc_ctx.state = TC_RELOAD_APPROACH;
}

void tc_abort(void) {
    if (g_tc_ctx.state == TC_IDLE) return;
    stop_all();
    cutter_abort();
    set_toolhead_filament(false);
    g_tc_ctx.state = TC_IDLE;
    cmd_event("TC:ERROR", "ABORTED");
}

void reload_trigger(int runout_lane, uint32_t now_ms) {
    memset(&g_tc_ctx, 0, sizeof(g_tc_ctx));
    int other = (runout_lane == 1) ? 2 : 1;
    lane_t *other_lane_ptr = lane_ptr(other);
    if (!other_lane_ptr || !lane_in_present(other_lane_ptr)) {
        cmd_event("RELOAD:FAULT", "NO_FILAMENT");
        return;
    }
    char event[8];
    snprintf(event, sizeof(event), "%d->%d", runout_lane, other);
    cmd_event("RELOAD:SWITCHING", event);
    g_tc_ctx.target_lane = other;
    g_tc_ctx.from_lane = runout_lane;
    g_tc_ctx.phase_start_ms = now_ms;
    g_tc_ctx.state = TC_RELOAD_WAIT_Y;
}

const char *tc_state_name(tc_state_t s) {
    switch (s) {
        case TC_IDLE: return "IDLE";
        case TC_UNLOAD_CUT: return "UNLOAD_CUT";
        case TC_UNLOAD_WAIT_CUT: return "UNLOAD_WAIT_CUT";
        case TC_UNLOAD_REVERSE: return "UNLOAD_REVERSE";
        case TC_UNLOAD_WAIT_OUT: return "UNLOAD_WAIT_OUT";
        case TC_UNLOAD_WAIT_Y: return "UNLOAD_WAIT_Y";
        case TC_UNLOAD_WAIT_TH: return "UNLOAD_WAIT_TH";
        case TC_UNLOAD_DONE: return "UNLOAD_DONE";
        case TC_SWAP: return "SWAP";
        case TC_LOAD_START: return "LOAD_START";
        case TC_LOAD_WAIT_OUT: return "LOAD_WAIT_OUT";
        case TC_LOAD_WAIT_TH: return "LOAD_WAIT_TH";
        case TC_LOAD_DONE: return "LOAD_DONE";
        case TC_RELOAD_WAIT_Y: return "RELOAD_WAIT_Y";
        case TC_RELOAD_APPROACH: return "RELOAD_APPROACH";
        case TC_RELOAD_FOLLOW: return "RELOAD_FOLLOW";
        case TC_ERROR: return "ERROR";
        default: return "?";
    }
}

const char *task_name(task_t t) {
    switch (t) {
        case TASK_IDLE: return "IDLE";
        case TASK_AUTOLOAD: return "AUTOLOAD";
        case TASK_FEED: return "FEED";
        case TASK_UNLOAD: return "UNLOAD";
        case TASK_LOAD_FULL: return "LOAD_FULL";
        case TASK_MOVE: return "MOVE";
        default: return "?";
    }
}

void tc_tick(uint32_t now_ms) {
    uint32_t age = now_ms - g_tc_ctx.phase_start_ms;
    lane_t *A = lane_ptr(active_lane);

    switch (g_tc_ctx.state) {
        case TC_IDLE:
        case TC_ERROR:
            return;

        case TC_UNLOAD_CUT:
            cutter_start(A, true, now_ms);
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
            if (lane_out_present(A)) {
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = TC_LOAD_WAIT_TH;
            } else if (A->task == TASK_IDLE) {
                tc_enter_error("LOAD_TIMEOUT");
            }
            break;

        case TC_LOAD_WAIT_TH:
            if (A->task == TASK_IDLE) {
                if (toolhead_has_filament) {
                    g_tc_ctx.state = TC_LOAD_DONE;
                } else {
                    tc_enter_error("LOAD_TIMEOUT");
                }
            }
            break;

        case TC_RELOAD_WAIT_Y: {
            lane_t *from_lane_ptr = lane_ptr(g_tc_ctx.from_lane);
            if (from_lane_ptr && lane_out_present(from_lane_ptr)) {
                if (from_lane_ptr->task != TASK_FEED) lane_start(from_lane_ptr, TASK_FEED, TRAILING_SPS, true, now_ms, 0);
            } else if (from_lane_ptr && from_lane_ptr->task == TASK_FEED) {
                lane_stop(from_lane_ptr);
            }

            bool tail_cleared = (!from_lane_ptr || !lane_out_present(from_lane_ptr));
            bool y_cleared = (!on_al(&g_y_split) || RELOAD_Y_TIMEOUT_MS == 0);
            if (tail_cleared && y_cleared) {
                if (g_tc_ctx.ready_to_join_since_ms == 0) g_tc_ctx.ready_to_join_since_ms = now_ms;
            } else {
                g_tc_ctx.ready_to_join_since_ms = 0;
            }

            bool join_delay_elapsed = (RELOAD_JOIN_DELAY_MS <= 0) ||
                                      (g_tc_ctx.ready_to_join_since_ms != 0 &&
                                       (now_ms - g_tc_ctx.ready_to_join_since_ms) >= (uint32_t)RELOAD_JOIN_DELAY_MS);

            if (tail_cleared && y_cleared && join_delay_elapsed) {
                char lane_s[2] = { (char)('0' + g_tc_ctx.target_lane), 0 };
                set_active_lane(g_tc_ctx.target_lane);
                lane_t *new_lane = lane_ptr(active_lane);
                if (from_lane_ptr && from_lane_ptr->task != TASK_IDLE) lane_stop(from_lane_ptr);
                cmd_event("RELOAD:JOINING", lane_s);
                int approach_sps = FEED_SPS;
                if (approach_sps <= 0) approach_sps = JOIN_SPS;
                lane_start(new_lane, TASK_FEED, approach_sps, true, now_ms, 2000.0f);
                g_tc_ctx.ready_to_join_since_ms = 0;
                g_tc_ctx.reload_tick_ms = now_ms;
                g_tc_ctx.reload_current_sps = 0;
                g_tc_ctx.last_trailing_ms = 0;
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = TC_RELOAD_APPROACH;
            } else if ((!tail_cleared || !y_cleared) && age > (uint32_t)RELOAD_Y_TIMEOUT_MS) {
                tc_enter_error("RELOAD_Y_TIMEOUT");
            }
            break;
        }

        case TC_RELOAD_APPROACH: {
            if (A && A->task == TASK_IDLE) {
                tc_enter_error("RELOAD_APPROACH_FAULT");
                break;
            }

            bool contacted = (g_buf.state == BUF_TRAILING);

            if (A && A->task_limit_mm > 0.0f && A->task_dist_mm >= A->task_limit_mm) {
                lane_stop(A);
                tc_enter_error("RELOAD_APPROACH_TIMEOUT");
                break;
            }

            if (contacted) {
                if (A) {
                    lane_stop(A);
                }
                g_tc_ctx.reload_current_sps = TRAILING_SPS;
                g_tc_ctx.last_trailing_ms = (g_buf.state == BUF_TRAILING) ? now_ms : 0;
                g_tc_ctx.wall_critical_since_ms = 0;
                g_tc_ctx.reload_tick_ms = now_ms;
                g_tc_ctx.phase_start_ms = now_ms;
                g_tc_ctx.state = TC_RELOAD_FOLLOW;
            } else if (A->task == TASK_IDLE) {
                tc_enter_error("RELOAD_APPROACH_TIMEOUT");
            }
            break;
        }

        case TC_RELOAD_FOLLOW: {
            buf_state_t instant_buf_state = buf_state_raw();
            if (g_buf.state == BUF_ADVANCE || instant_buf_state == BUF_ADVANCE || toolhead_has_filament) {
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

            if ((now_ms - g_tc_ctx.reload_tick_ms) < (uint32_t)SYNC_TICK_MS) break;
            g_tc_ctx.reload_tick_ms = now_ms;

            uint32_t follow_age_ms = now_ms - g_tc_ctx.phase_start_ms;
            float trailing_wall_ms = sync_trailing_wall_time_ms(A);
            float trailing_push_mm_s = sync_trailing_wall_velocity_mm_s(A);

            // We must OVER-feed to close the gap and maintain pressure on the old tail.
            // Under-feeding creates a gap because the MMU pushes slower than the extruder pulls!
            int target_sps = (int)(extruder_est_sps * RELOAD_LEAN_FACTOR);
            if (target_sps < PRESS_SPS) {
                target_sps = PRESS_SPS;
            }
            if (g_buf.state == BUF_TRAILING) {
                target_sps = TRAILING_SPS;
            } else if (g_buf.state == BUF_ADVANCE) {
                target_sps = JOIN_SPS;
            }

            if (g_buf.state != BUF_TRAILING && trailing_wall_ms < RELOAD_TRAILING_SOFT_WALL_MS) {
                float urgency = (RELOAD_TRAILING_SOFT_WALL_MS - trailing_wall_ms) / RELOAD_TRAILING_SOFT_WALL_MS;
                urgency = clamp_f(urgency, 0.0f, 1.0f);
                int wall_trim = (int)(urgency * (float)(target_sps - TRAILING_SPS));
                target_sps -= wall_trim;
            }
            target_sps = clamp_i(target_sps, TRAILING_SPS, JOIN_SPS);

            if (follow_age_ms < (uint32_t)RELOAD_TOUCH_SETTLE_MS) {
                target_sps = TRAILING_SPS;
            } else if (g_buf.state != BUF_TRAILING &&
                       follow_age_ms < (uint32_t)(RELOAD_TOUCH_SETTLE_MS + RELOAD_TOUCH_BOOST_MS)) {
                int floor_sps = (PRESS_SPS * RELOAD_TOUCH_FLOOR_PCT) / 100;
                if (floor_sps < TRAILING_SPS) floor_sps = TRAILING_SPS;
                if (target_sps < floor_sps) target_sps = floor_sps;
            }

            if (g_tc_ctx.reload_current_sps > target_sps) g_tc_ctx.reload_current_sps -= SYNC_RAMP_DN_SPS;
            else if (g_tc_ctx.reload_current_sps < target_sps) g_tc_ctx.reload_current_sps += SYNC_RAMP_UP_SPS;
            g_tc_ctx.reload_current_sps = clamp_i(g_tc_ctx.reload_current_sps, 0, PRESS_SPS);

            if (A) {
                if (g_tc_ctx.reload_current_sps > 0) {
                    if (A->task != TASK_FEED && A->fault == FAULT_NONE) {
                        lane_start(A, TASK_FEED, g_tc_ctx.reload_current_sps, true, now_ms, 0);
                    } else if (A->task == TASK_FEED) {
                        A->current_sps = g_tc_ctx.reload_current_sps;
                        A->target_sps = g_tc_ctx.reload_current_sps;
                        motor_enable(&A->m, true);
                        motor_set_dir(&A->m, true);
                        motor_set_rate_sps(&A->m, g_tc_ctx.reload_current_sps);
                    }
                } else if (A->task == TASK_FEED) {
                    motor_stop(&A->m);
                    A->current_sps = 0;
                    A->target_sps = 0;
                    int idx = A->lane_id - 1;
                    tmc_set_stealthchop_sps(A->tmc, TMC_STEALTHCHOP_SPS[idx], TMC_MICROSTEPS[idx]);
                }
            }

            if (g_buf.state == BUF_TRAILING) {
                if (g_tc_ctx.last_trailing_ms == 0) g_tc_ctx.last_trailing_ms = now_ms;
                bool wall_critical = trailing_push_mm_s > RELOAD_TRAILING_HARD_PUSH_MM_S &&
                                     trailing_wall_ms < RELOAD_TRAILING_HARD_WALL_MS;
                if (wall_critical) {
                    if (g_tc_ctx.wall_critical_since_ms == 0) g_tc_ctx.wall_critical_since_ms = now_ms;
                    else if ((now_ms - g_tc_ctx.wall_critical_since_ms) >= RELOAD_TRAILING_HARD_HOLD_MS) {
                        tc_enter_error("FOLLOW_JAM");
                        lane_stop(A);
                        break;
                    }
                } else {
                    g_tc_ctx.wall_critical_since_ms = 0;
                }
                if ((now_ms - g_tc_ctx.last_trailing_ms) > (uint32_t)FOLLOW_TIMEOUT_MS[lane_to_idx(A->lane_id)]) {
                    tc_enter_error("FOLLOW_JAM");
                    lane_stop(A);
                    break;
                }
            } else {
                g_tc_ctx.last_trailing_ms = 0;
                g_tc_ctx.wall_critical_since_ms = 0;
            }

            if ((now_ms - g_tc_ctx.phase_start_ms) > 300000) {
                tc_enter_error("FOLLOW_TIMEOUT_ABS");
                lane_stop(A);
                break;
            }

            static uint32_t last_reload_report_ms = 0;
            if ((now_ms - last_reload_report_ms) >= 500u) {
                last_reload_report_ms = now_ms;
                char ev[64];
                snprintf(ev, sizeof(ev), "%s,%.1f,%.2f",
                         buf_state_name(g_buf.state),
                         (double)sps_to_mm_per_min(g_tc_ctx.reload_current_sps),
                         (double)g_buf_pos);
                cmd_event("BS", ev);
            }
            break;
        }

        case TC_LOAD_DONE: {
            char lane_s[2] = { (char)('0' + active_lane), 0 };
            cmd_event("TC:DONE", lane_s);
            g_tc_ctx.state = TC_IDLE;
            break;
        }
    }
}