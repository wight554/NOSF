#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "controller_shared.h"

void cutter_init(void);
bool cutter_busy(void);
void cutter_start(lane_t *L, bool enable_feed, uint32_t now_ms);
void cutter_abort(void);
void cutter_tick(uint32_t now_ms);
