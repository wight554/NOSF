#include "button.h"
#include "hardware/gpio.h"

typedef struct {
    gpio_irq_cb_t cb;
    void *arg;
    uint32_t events;
} entry_t;

static entry_t g_entries[30];

static void gpio_irq_dispatch(uint gpio, uint32_t events) {
    if (gpio < 30) {
        entry_t *e = &g_entries[gpio];
        if (e->cb && (events & e->events)) e->cb(e->arg);
    }
}

void button_system_init(void) {
    for (int i=0;i<30;i++){
        g_entries[i].cb = 0;
        g_entries[i].arg = 0;
        g_entries[i].events = 0;
    }
    // No-op: callback is set when first listen() happens
}

void listen(uint gpio, uint32_t events, gpio_irq_cb_t cb, void *arg) {
    if (gpio < 30) {
        g_entries[gpio].cb = cb;
        g_entries[gpio].arg = arg;
        g_entries[gpio].events = events;
        gpio_set_irq_enabled_with_callback(gpio, events, true, &gpio_irq_dispatch);
    }
}
