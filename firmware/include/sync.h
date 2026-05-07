#pragma once

#include "controller_shared.h"

const char *buf_state_name(buf_state_t s);
buf_state_t buf_state_raw(void);
int sync_clamp_max_sps(int requested_sps);
void sync_disable(bool reset_estimator);
float sync_trailing_wall_velocity_mm_s(lane_t *lane);
float sync_trailing_wall_time_ms(lane_t *lane);

void boot_stabilize_start(uint32_t now_ms);
void boot_stabilize_tick(uint32_t now_ms);
void buf_sensor_tick(uint32_t now_ms);
void sync_tick(uint32_t now_ms);