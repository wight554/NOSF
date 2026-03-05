#pragma once
#include <stdint.h>
#include "hardware/gpio.h"

typedef void (*gpio_irq_cb_t)(void *arg);

void button_system_init(void);
void listen(uint gpio, uint32_t events, gpio_irq_cb_t cb, void *arg);
