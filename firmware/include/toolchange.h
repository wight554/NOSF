#pragma once

#include "controller_shared.h"

void toolchange_init(void);
bool cutter_busy(void);
void cutter_start(lane_t *L, uint32_t now_ms);
void cutter_abort(void);
void cutter_tick(uint32_t now_ms);
void tc_start(int target_lane, uint32_t now_ms);
void tc_abort(void);
tc_state_t tc_state(void);
void tc_tick(uint32_t now_ms);
void tc_enter_error(const char *reason);
void reload_trigger(int runout_lane, uint32_t now_ms);
const char *tc_state_name(tc_state_t s);
const char *task_name(task_t t);