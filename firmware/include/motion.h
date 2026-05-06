#pragma once

#include "controller_shared.h"

void din_init(din_t *d, uint pin);
void din_update(din_t *d);

void motor_init(motor_t *m, uint en, uint dir, uint step, bool dir_invert);
void motor_enable(motor_t *m, bool on);
void motor_set_dir(motor_t *m, bool forward);
void motor_set_rate_sps(motor_t *m, int sps);
void motor_stop(motor_t *m);

void lane_setup(lane_t *L, uint pin_in, uint pin_out, motor_t m, int lane_id, uint diag_pin, tmc_t *tmc);
void lane_start(lane_t *L, task_t t, int sps, bool forward, uint32_t now_ms, float limit_mm);
void lane_stop(lane_t *L);
void lane_tick(lane_t *L, uint32_t now_ms);
void lane_fault(lane_t *L, fault_t f);
void stop_all(void);