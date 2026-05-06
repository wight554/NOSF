#pragma once

#include "controller_shared.h"

const char *buf_state_name(buf_state_t s);
int sync_clamp_max_sps(int requested_sps);

void boot_stabilize_start(uint32_t now_ms);
void boot_stabilize_tick(uint32_t now_ms);
void buf_sensor_tick(uint32_t now_ms);
void sync_tick(uint32_t now_ms);