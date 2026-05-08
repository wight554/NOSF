#include "motion.h"

#include "toolchange.h"
#include "sync.h"

#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"

#include "protocol.h"

static bool lane_tail_in_transit(lane_t *L) {
    return !lane_in_present(L) && !lane_out_present(L) &&
           ((L->task_dist_mm - L->dist_at_in_clear_mm) < ((float)DIST_IN_OUT * 1.2f));
}

static bool lane_tail_runout_ready(lane_t *L) {
    if (!lane_out_present(L)) return true;
    return (L->task_dist_mm - L->dist_at_in_clear_mm) >= ((float)DIST_IN_OUT * 1.2f);
}

int motion_clamp_rate_sps(int sps) {
    if (sps <= 0) return sps;
    return (sps < GLOBAL_MAX_SPS) ? sps : GLOBAL_MAX_SPS;
}

void motion_limit_runtime_rates(bool refresh_active_motors) {
    FEED_SPS = motion_clamp_rate_sps(FEED_SPS);
    REV_SPS = motion_clamp_rate_sps(REV_SPS);
    AUTO_SPS = motion_clamp_rate_sps(AUTO_SPS);
    BUF_STAB_SPS = motion_clamp_rate_sps(BUF_STAB_SPS);
    JOIN_SPS = motion_clamp_rate_sps(JOIN_SPS);
    PRESS_SPS = motion_clamp_rate_sps(PRESS_SPS);
    TRAILING_SPS = motion_clamp_rate_sps(TRAILING_SPS);
    RAMP_STEP_SPS = motion_clamp_rate_sps(RAMP_STEP_SPS);
    PRE_RAMP_SPS = motion_clamp_rate_sps(PRE_RAMP_SPS);
    SYNC_MAX_SPS = motion_clamp_rate_sps(SYNC_MAX_SPS);
    SYNC_MIN_SPS = motion_clamp_rate_sps(SYNC_MIN_SPS);
    SYNC_RAMP_UP_SPS = motion_clamp_rate_sps(SYNC_RAMP_UP_SPS);
    SYNC_RAMP_DN_SPS = motion_clamp_rate_sps(SYNC_RAMP_DN_SPS);
    g_baseline_target_sps = motion_clamp_rate_sps(g_baseline_target_sps);
    g_baseline_sps = motion_clamp_rate_sps(g_baseline_sps);
    sync_current_sps = motion_clamp_rate_sps(sync_current_sps);
    g_tc_ctx.reload_current_sps = motion_clamp_rate_sps(g_tc_ctx.reload_current_sps);

    if (SYNC_MIN_SPS > SYNC_MAX_SPS) SYNC_MIN_SPS = SYNC_MAX_SPS;

    lane_t *lanes[] = { &g_lane_l1, &g_lane_l2 };
    for (size_t i = 0; i < NUM_LANES; i++) {
        lane_t *L = lanes[i];
        L->target_sps = motion_clamp_rate_sps(L->target_sps);
        L->current_sps = motion_clamp_rate_sps(L->current_sps);
        if (L->target_sps > 0 && L->current_sps > L->target_sps) L->current_sps = L->target_sps;
        if (refresh_active_motors && L->task != TASK_IDLE && L->current_sps > 0) {
            motor_set_rate_sps(&L->m, L->current_sps);
        }
    }
}

void din_init(din_t *d, uint pin) {
    d->pin = pin;
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_IN);
    gpio_pull_up(pin);

    bool raw = gpio_get(pin);
    d->stable = raw;
    d->last_raw = raw;
    d->last_edge = get_absolute_time();
}

void din_update(din_t *d) {
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

void motor_init(motor_t *m, uint en, uint dir, uint step, bool dir_invert) {
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

void motor_enable(motor_t *m, bool on) {
    if (EN_ACTIVE_LOW) gpio_put(m->en, on ? 0 : 1);
    else gpio_put(m->en, on ? 1 : 0);
}

void motor_set_dir(motor_t *m, bool forward) {
    bool d = forward ^ m->dir_invert;
    gpio_put(m->dir, d ? 1 : 0);
}

void motor_set_rate_sps(motor_t *m, int sps) {
    sps = motion_clamp_rate_sps(sps);
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

void motor_stop(motor_t *m) {
    pwm_set_enabled(m->slice, false);
    motor_enable(m, false);
}

void lane_setup(lane_t *L, uint pin_in, uint pin_out, motor_t m, int lane_id, tmc_t *tmc) {
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
    L->motion_started_ms = 0;
    L->task_started_ms = 0;
    L->dry_spin_ms = 0;
    L->fault = FAULT_NONE;
    L->lane_id = lane_id;
    L->runout_block_until_ms = 0;
    L->retract_deadline_ms = 0;
    L->unload_sensor_latch = false;
    L->buf_advance_since_ms = 0;
    L->dist_at_in_clear_mm = 0.0f;
    L->prev_in = false;
    L->unload_to_in = false;
}

void lane_stop(lane_t *L) {
    L->task = TASK_IDLE;
    L->task_started_ms = 0;
    L->dry_spin_ms = 0;
    L->unload_sensor_latch = false;
    L->unload_buf_recover_done = false;
    L->retract_deadline_ms = 0;
    L->buf_advance_since_ms = 0;
    L->reload_tail_ms = 0;
    L->current_sps = 0;
    L->target_sps = 0;
    motor_stop(&L->m);
    tmc_set_stealthchop_sps(L->tmc, TMC_STEALTHCHOP_SPS[L->lane_id - 1]);
}

void lane_start(lane_t *L, task_t t, int sps, bool forward, uint32_t now_ms, float limit_mm) {
    L->task = t;
    L->fault = FAULT_NONE;
    L->last_dist_tick_ms = now_ms;
    L->task_dist_mm = 0.0f;
    L->dist_at_out_mm = 0.0f;
    L->unload_sensor_latch = false;
    L->retract_deadline_ms = 0;
    L->dist_at_in_clear_mm = 0.0f;

    L->task_limit_mm = limit_mm;

    L->target_sps = motion_clamp_rate_sps(sps);
    L->current_sps = motion_clamp_rate_sps(RAMP_STEP_SPS);
    if (L->current_sps > L->target_sps) L->current_sps = L->target_sps;
    L->ramp_last_tick_ms = now_ms;
    L->motion_started_ms = now_ms;
    if (L->task_started_ms == 0) L->task_started_ms = now_ms;

    motor_enable(&L->m, true);
    motor_set_dir(&L->m, forward);
    motor_set_rate_sps(&L->m, L->current_sps);
    int idx = L->lane_id - 1;
    tmc_set_stealthchop_sps(L->tmc, TMC_STEALTHCHOP_SPS[idx]);
    tmc_set_run_current_ma(L->tmc, TMC_RUN_CURRENT_MA[idx], TMC_HOLD_CURRENT_MA[idx]);
}

void lane_tick(lane_t *L, uint32_t now_ms) {
    char lane_s[2] = { (char)('0' + L->lane_id), 0 };

    bool in_p = lane_in_present(L);
    if (L->prev_in && !in_p) {
        L->dist_at_in_clear_mm = L->task_dist_mm;
    }
    L->prev_in = in_p;

    if (L->task != TASK_IDLE && L->current_sps < L->target_sps) {
        if ((int32_t)(now_ms - L->ramp_last_tick_ms) >= RAMP_TICK_MS) {
            L->ramp_last_tick_ms = now_ms;
            L->current_sps += RAMP_STEP_SPS;
            if (L->current_sps > L->target_sps) L->current_sps = L->target_sps;
            motor_set_rate_sps(&L->m, L->current_sps);
        }
    }

    uint32_t dt_ms = now_ms - L->last_dist_tick_ms;
    if (dt_ms > 0) {
        int idx = lane_to_idx(L->lane_id);
        L->task_dist_mm += (float)L->current_sps * ((float)dt_ms / 1000.0f) * MM_PER_STEP[idx];
        L->last_dist_tick_ms = now_ms;
    }

    if (L->task == TASK_AUTOLOAD) {
        if (lane_out_present(L)) {
            if (AUTOLOAD_RETRACT_MM > 0) {
                float secs = (float)AUTOLOAD_RETRACT_MM / ((float)REV_SPS * MM_PER_STEP[L->lane_id - 1]);
                if (secs < 0.05f) secs = 0.05f;
                L->retract_deadline_ms = now_ms + (uint32_t)(secs * 1000.0f);
                L->task = TASK_UNLOAD;
                motor_set_dir(&L->m, false);
                L->target_sps = REV_SPS;
                L->current_sps = RAMP_STEP_SPS;
                L->ramp_last_tick_ms = now_ms;
                motor_set_rate_sps(&L->m, L->current_sps);
            } else {
                lane_stop(L);
            }
        } else if (L->task_dist_mm > (float)DIST_IN_OUT * 1.5f) {
            lane_stop(L);
            tc_enter_error("PRELOAD_JAM");
        } else if (L->task_limit_mm > 0 && L->task_dist_mm >= L->task_limit_mm) {
            lane_stop(L);
        }
    }

    if (L->task == TASK_UNLOAD) {
        if (L->retract_deadline_ms == 0) {
            if (L->unload_sensor_latch && !L->unload_to_in) {
                float moved_mm = L->task_dist_mm - L->dist_at_out_mm;
                if (moved_mm >= BUF_HALF_TRAVEL_MM) {
                    L->task_dist_mm = L->dist_at_out_mm;
                    L->unload_sensor_latch = false;

                    motor_stop(&L->m);
                    L->target_sps = REV_SPS;
                    L->current_sps = REV_SPS;
                    L->ramp_last_tick_ms = now_ms;
                    motor_enable(&L->m, true);
                    motor_set_dir(&L->m, false);
                    motor_set_rate_sps(&L->m, L->current_sps);
                }
            } else if (!L->unload_to_in && !L->unload_buf_recover_done && g_buf.state == BUF_ADVANCE) {
                motor_stop(&L->m);
                L->unload_buf_recover_done = true;
                L->unload_sensor_latch = true;
                L->dist_at_out_mm = L->task_dist_mm;

                L->target_sps = BUF_STAB_SPS;
                L->current_sps = BUF_STAB_SPS;
                L->ramp_last_tick_ms = now_ms;
                motor_enable(&L->m, true);
                motor_set_dir(&L->m, true);
                motor_set_rate_sps(&L->m, L->current_sps);
            } else if (lane_out_present(L)) {
            } else if (L->unload_to_in && lane_in_present(L)) {
                if (!L->unload_sensor_latch) {
                    L->unload_sensor_latch = true;
                    L->dist_at_out_mm = L->task_dist_mm;
                }
                float dist_since_out = L->task_dist_mm - L->dist_at_out_mm;
                if (dist_since_out > (float)DIST_IN_OUT * 2.0f) {
                    lane_stop(L);
                    tc_enter_error("UNLOAD_JAM");
                }
            } else {
                if (L->unload_to_in) {
                    L->retract_deadline_ms = now_ms + 500;
                } else {
                    float secs = (float)AUTOLOAD_RETRACT_MM / ((float)REV_SPS * MM_PER_STEP[L->lane_id - 1]);
                    if (secs < 0.2f) secs = 0.2f;
                    L->retract_deadline_ms = now_ms + (uint32_t)(secs * 1000.0f);
                }
            }

            if (L->task_limit_mm > 0 && L->task_dist_mm >= L->task_limit_mm) {
                lane_stop(L);
                cmd_event("UNLOAD_TIMEOUT", NULL);
            }
        } else {
            if ((int32_t)(now_ms - L->retract_deadline_ms) >= 0) {
                lane_stop(L);
                cmd_event("UNLOADED", lane_s);
            }
        }
    }

    if (L->task == TASK_LOAD_FULL) {
        if (lane_out_present(L) && !L->unload_sensor_latch) {
            L->unload_sensor_latch = true;
            L->dist_at_out_mm = L->task_dist_mm;
        }

        if (TS_BUF_FALLBACK_MS > 0 && L->unload_sensor_latch) {
            if (g_buf.state == BUF_TRAILING) {
                if (L->buf_advance_since_ms == 0) L->buf_advance_since_ms = now_ms;
                else if ((int32_t)(now_ms - L->buf_advance_since_ms) >= TS_BUF_FALLBACK_MS)
                    set_toolhead_filament(true);
            } else {
                L->buf_advance_since_ms = 0;
            }
        }

        bool buf_advance_sane = (g_buf.state == BUF_ADVANCE);
        bool buf_trailing_sane = false;
        if (L->unload_sensor_latch) {
            float dist_since_out = L->task_dist_mm - L->dist_at_out_mm;
            float threshold = (float)DIST_OUT_Y + (float)DIST_Y_BUF + (float)BUF_SIZE_MM / 2.0f;
            if (dist_since_out < threshold * 0.8f) {
                buf_advance_sane = false;
            } else if (g_buf.state == BUF_TRAILING) {
                buf_trailing_sane = true;
            }
        } else {
            buf_advance_sane = false;
        }

        bool loaded = toolhead_has_filament || buf_advance_sane || buf_trailing_sane;

        if (loaded) {
            lane_stop(L);
            cmd_event("LOADED", lane_s);
            if (AUTO_MODE) {
                sync_enabled = true;
                sync_auto_started = true;
                sync_idle_since_ms = 0;
            }
        } else if (!lane_in_present(L) && (int32_t)(now_ms - L->task_started_ms) >= 1000) {
            if (lane_out_present(L)) {
                L->reload_tail_ms = now_ms;
            } else if (lane_tail_in_transit(L)) {
            } else {
                lane_stop(L);
                cmd_event("RUNOUT", lane_s);
                if (RELOAD_MODE && tc_state() == TC_IDLE) reload_trigger(L->lane_id, now_ms);
            }
        } else if (!L->unload_sensor_latch &&
                   (int32_t)(now_ms - L->motion_started_ms) >= 10000) {
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
            cmd_event("MOVE_DONE", lane_s);
        }
    }

    if ((L->task == TASK_FEED || L->task == TASK_AUTOLOAD) && !lane_in_present(L)) {
        if ((int32_t)(now_ms - L->task_started_ms) >= 1000 &&
            (int32_t)(now_ms - L->runout_block_until_ms) >= 0) {
            if (lane_out_present(L)) {
                L->reload_tail_ms = now_ms;
                L->runout_block_until_ms = now_ms + 30000u;
            } else if (lane_tail_in_transit(L)) {
            } else {
                cmd_event("RUNOUT", lane_s);
                L->runout_block_until_ms = now_ms + (uint32_t)RUNOUT_COOLDOWN_MS;
                if (L->task == TASK_FEED) set_toolhead_filament(false);
                lane_stop(L);
                if (RELOAD_MODE && L->task == TASK_FEED && tc_state() == TC_IDLE)
                    reload_trigger(L->lane_id, now_ms);
            }
        }
    }

    if (L->task != TASK_IDLE && !lane_in_present(L) && !lane_out_present(L) && g_buf.state != BUF_ADVANCE) {
        if (L->dry_spin_ms == 0) L->dry_spin_ms = now_ms;
        if ((int32_t)(now_ms - L->dry_spin_ms) > 8000) {
            lane_stop(L);
            L->fault = FAULT_DRY_SPIN;
            cmd_event("FAULT:DRY_SPIN", lane_s);
        }
    } else {
        L->dry_spin_ms = 0;
    }

    if (L->reload_tail_ms != 0 && (L->task == TASK_FEED || L->task == TASK_LOAD_FULL || L->task == TASK_AUTOLOAD)) {
        if (lane_tail_runout_ready(L)) {
            bool tail_assist_finished = sync_tail_assist_active && L->task == TASK_FEED;
            if (tail_assist_finished) sync_disable(true);
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

void stop_all(void) {
    lane_stop(&g_lane_l1);
    lane_stop(&g_lane_l2);
}

void lane_fault(lane_t *L, fault_t f) {
    motor_stop(&L->m);
    L->task = TASK_IDLE;
    L->current_sps = 0;
    L->target_sps = 0;
    L->fault = f;
}