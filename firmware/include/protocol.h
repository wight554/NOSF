#pragma once

#include <stdint.h>

void cmd_reply(const char *status, const char *data);
void cmd_event(const char *type, const char *data);
void cmd_poll(uint32_t now_ms);
