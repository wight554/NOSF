#include "neopixel.h"

#include "hardware/clocks.h"
#include "hardware/pio.h"
#include "ws2812.pio.h"

static PIO g_pio = pio1;
static uint g_sm;

static inline uint32_t rgb_to_grb(uint8_t r, uint8_t g, uint8_t b) {
    return ((uint32_t)g << 16u) | ((uint32_t)r << 8u) | (uint32_t)b;
}

static void ws2812_program_init_local(PIO pio,
                                      uint sm,
                                      uint offset,
                                      uint pin,
                                      float freq,
                                      bool rgbw) {
    pio_gpio_init(pio, pin);
    pio_sm_set_consecutive_pindirs(pio, sm, pin, 1, true);

    pio_sm_config c = ws2812_program_get_default_config(offset);
    sm_config_set_sideset_pins(&c, pin);
    sm_config_set_out_shift(&c, false, true, rgbw ? 32 : 24);
    sm_config_set_fifo_join(&c, PIO_FIFO_JOIN_TX);

    int cycles_per_bit = ws2812_T1 + ws2812_T2 + ws2812_T3;
    float div = (float)clock_get_hz(clk_sys) / (freq * (float)cycles_per_bit);
    sm_config_set_clkdiv(&c, div);

    pio_sm_init(pio, sm, offset, &c);
    pio_sm_set_enabled(pio, sm, true);
}

void neopixel_init(uint pin) {
    uint offset = (uint)pio_add_program(g_pio, &ws2812_program);
    g_sm = pio_claim_unused_sm(g_pio, true);
    ws2812_program_init_local(g_pio, g_sm, offset, pin, 800000.0f, false);
    neopixel_set(0, 0, 0);
}

void neopixel_set(uint8_t r, uint8_t g, uint8_t b) {
    uint32_t grb = rgb_to_grb(r, g, b);
    pio_sm_put_blocking(g_pio, g_sm, grb << 8u);
}
