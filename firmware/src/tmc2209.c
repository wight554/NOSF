#include "tmc2209.h"
#include <math.h>
#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "pico/stdlib.h"

#define TMC_BAUD    40000u
#define TMC_BIT_NS  25000u
#define TMC_HALF_BIT_NS 12500u

static inline void tmc_delay_ns(uint32_t ns) {
    busy_wait_us_32((ns + 999u) / 1000u);
}

static inline void line_idle(uint pin) {
    gpio_put(pin, 1);
    gpio_set_dir(pin, GPIO_OUT);
}

static inline void line_listen(uint pin) {
    gpio_set_dir(pin, GPIO_IN);
    gpio_pull_up(pin);
}

static inline void line_drive_low(uint pin) {
    gpio_put(pin, 0);
    gpio_set_dir(pin, GPIO_OUT);
}

static inline void line_drive_high(uint pin) {
    gpio_put(pin, 1);
    gpio_set_dir(pin, GPIO_OUT);
}

static void tx_byte(uint pin, uint8_t b) {
    uint64_t next_us = time_us_64();

    line_drive_low(pin);
    next_us += 25;
    busy_wait_until(from_us_since_boot(next_us));

    for (int i = 0; i < 8; i++) {
        if (b & 0x01) {
            line_drive_high(pin);
        } else {
            line_drive_low(pin);
        }
        b >>= 1;
        next_us += 25;
        busy_wait_until(from_us_since_boot(next_us));
    }

    line_drive_high(pin);
    next_us += 25;
    busy_wait_until(from_us_since_boot(next_us));
}

static bool rx_wait_start(uint pin, uint32_t timeout_us, uint64_t *edge_us_out) {
    absolute_time_t deadline = make_timeout_time_us(timeout_us);

    // 1. Force wait for HIGH (guarantees we are in the stop bit / idle state)
    while (!gpio_get(pin)) {
        if (time_reached(deadline)) return false;
    }

    // 2. Now wait for the falling edge (the true start bit)
    while (gpio_get(pin)) {
        if (time_reached(deadline)) return false;
    }

    *edge_us_out = time_us_64();
    return true;
}

// Sample 8 data bits at absolute offsets from the detected start-bit edge.
// Center of data bit i = edge + (2i+3) * half_bit = edge + (2i+3) * TMC_HALF_BIT_NS.
// Using absolute timestamps keeps each sample within ~5% of center regardless
// of loop overhead.
static uint8_t rx_byte_from_edge(uint pin, uint64_t edge_us) {
    uint8_t b = 0;
    for (int i = 0; i < 8; i++) {
        uint64_t sample_us = edge_us + ((uint64_t)(2 * i + 3) * TMC_HALF_BIT_NS + 500u) / 1000u;
        busy_wait_until(from_us_since_boot(sample_us));
        if (gpio_get(pin)) b |= (1u << i);
    }
    return b;
}

uint8_t tmc_crc8(const uint8_t *data, uint8_t len) {
    uint8_t crc = 0;
    for (uint8_t i = 0; i < len; i++) {
        uint8_t b = data[i];
        for (uint8_t j = 0; j < 8; j++) {
            uint8_t mix = (crc >> 7) ^ (b & 1u);
            crc <<= 1;
            if (mix) {
                crc ^= 0x07;
            }
            b >>= 1;
        }
    }
    return crc;
}

bool tmc_write(tmc_t *t, uint8_t reg, uint32_t val) {
    uint8_t buf[8];

    buf[0] = 0x05;
    buf[1] = t->addr;
    buf[2] = reg | 0x80;
    buf[3] = (uint8_t)((val >> 24) & 0xFFu);
    buf[4] = (uint8_t)((val >> 16) & 0xFFu);
    buf[5] = (uint8_t)((val >> 8) & 0xFFu);
    buf[6] = (uint8_t)(val & 0xFFu);
    buf[7] = tmc_crc8(buf, 7);

    uint32_t ints = save_and_disable_interrupts();
    for (int i = 0; i < 8; i++) {
        tx_byte(t->tx_pin, buf[i]);
    }
    line_idle(t->tx_pin);
    restore_interrupts(ints);
    return true;
}

bool tmc_read(tmc_t *t, uint8_t reg, uint32_t *out) {
    uint8_t req[4];
    uint8_t rep[8];
    uint64_t edge_us;

    req[0] = 0x05;
    req[1] = t->addr;
    req[2] = reg & 0x7Fu;
    req[3] = tmc_crc8(req, 3);

    uint32_t ints = save_and_disable_interrupts();
    for (int i = 0; i < 4; i++) {
        tx_byte(t->tx_pin, req[i]);
    }

    line_listen(t->tx_pin);
    // Single-wire: tx_pin IS the UART wire. After TX, release to INPUT and listen
    // on the same pin for the TMC's reply (~8 bit-times after last TX bit).
    // Start polling before the 8-bit-time reply window (wait 4 bit-times).
    busy_wait_us_32(100);

    if (!rx_wait_start(t->tx_pin, 2000, &edge_us)) {
        line_idle(t->tx_pin);
        restore_interrupts(ints);
        return false;
    }
    rep[0] = rx_byte_from_edge(t->tx_pin, edge_us);

    for (int i = 1; i < 8; i++) {
        if (!rx_wait_start(t->tx_pin, 200, &edge_us)) {
            line_idle(t->tx_pin);
            restore_interrupts(ints);
            return false;
        }
        rep[i] = rx_byte_from_edge(t->tx_pin, edge_us);
    }
    line_idle(t->tx_pin);
    restore_interrupts(ints);


    if (rep[0] != 0x05 || rep[1] != 0xFF || (rep[2] & 0x7Fu) != reg) {
        return false;
    }
    if (tmc_crc8(rep, 7) != rep[7]) {
        return false;
    }

    *out = ((uint32_t)rep[3] << 24) |
           ((uint32_t)rep[4] << 16) |
           ((uint32_t)rep[5] << 8) |
           (uint32_t)rep[6];
    return true;
}

static uint8_t clamp_u5_from_ma(int ma) {
    if (ma <= 0) return 0;
    float irms   = (float)ma / 1000.0f;
    // TMC2209 current to CS — matches Klipper TMCCurrentHelper formula
    // CS = 32 * Irms * (Rsense + 0.020) * sqrt(2) / Vsense_ref - 1
    // For ERB Rsense=0.110: effective = 0.130
    float reff   = 0.110f + 0.020f;          // Rsense + 20mΩ per TMC docs
    float sqrt2  = 1.41421356f;
    // Try VSENSE=0 (Vref=0.32V) first
    int v = (int)(32.0f * irms * reff * sqrt2 / 0.32f - 1.0f + 0.5f);
    if (v < 16) {
        // Switch to VSENSE=1 (Vref=0.18V) for better CS resolution
        v = (int)(32.0f * irms * reff * sqrt2 / 0.18f - 1.0f + 0.5f);
    }
    if (v < 0)  v = 0;
    if (v > 31) v = 31;
    return (uint8_t)v;
}

bool tmc_set_run_current_ma(tmc_t *t, int run_ma, int hold_ma) {
    uint8_t irun = clamp_u5_from_ma(run_ma);
    uint8_t ihold = clamp_u5_from_ma(hold_ma);
    uint32_t reg = ((uint32_t)ihold) | ((uint32_t)irun << 8) | (8u << 16);
    return tmc_write(t, TMC_REG_IHOLD_IRUN, reg);
}

bool tmc_setup_chopconf(tmc_t *t, int microsteps, int toff, int tbl, int hstrt, int hend) {
    int mres;
    switch (microsteps) {
        case 256: mres = 0; break;
        case 128: mres = 1; break;
        case 64:  mres = 2; break;
        case 32:  mres = 3; break;
        case 16:  mres = 4; break;
        case 8:   mres = 5; break;
        case 4:   mres = 6; break;
        case 2:   mres = 7; break;
        case 1:   mres = 8; break;
        default:  return false;
    }

    // Convert actual spreadsheet values to register values
    uint32_t reg_toff = (uint32_t)(toff & 0x0F);
    uint32_t reg_tbl = (uint32_t)(tbl & 0x03);
    uint32_t reg_hstrt = (uint32_t)((hstrt - 1) & 0x07);
    uint32_t reg_hend = (uint32_t)((hend + 3) & 0x0F);

    uint32_t chop = 0;
    // CHOPCONF register layout (TMC2209 datasheet):
    // bits[3:0]   TOFF
    // bits[6:4]   HSTRT
    // bits[10:7]  HEND
    // bits[16:15] TBL
    // bit[17]     VSENSE=1
    // bits[27:24] MRES
    chop |= (reg_toff  << 0);
    chop |= (reg_hstrt << 4);
    chop |= (reg_hend  << 7);
    chop |= (reg_tbl   << 15);
    chop |= (1u        << 17);   // VSENSE=1
    chop |= ((uint32_t)mres << 24); // MRES
    return tmc_write(t, TMC_REG_CHOPCONF, chop);
}

bool tmc_set_spreadcycle(tmc_t *t, bool spreadcycle) {
    uint32_t gconf = 0;
    if (spreadcycle) gconf |= (1u << 2);
    gconf |= (1u << 6) | (1u << 7);
    return tmc_write(t, TMC_REG_GCONF, gconf);
}

bool tmc_set_sgthrs(tmc_t *t, uint8_t sgt) {
    return tmc_write(t, TMC_REG_SGTHRS, (uint32_t)sgt);
}

bool tmc_set_tcoolthrs(tmc_t *t, uint32_t v) {
    return tmc_write(t, TMC_REG_TCOOLTHRS, v);
}

bool tmc_read_sg_result(tmc_t *t, uint16_t *out) {
    uint32_t v = 0;
    if (!tmc_read(t, TMC_REG_SG_RESULT, &v)) {
        return false;
    }
    *out = (uint16_t)(v & 0x03FFu);
    return true;
}

int tmc_read_raw(tmc_t *t, uint8_t reg, uint8_t *buf_out) {
    uint8_t req[4];
    uint64_t edge_us;

    req[0] = 0x05;
    req[1] = t->addr;
    req[2] = reg & 0x7Fu;
    req[3] = tmc_crc8(req, 3);

    uint32_t ints = save_and_disable_interrupts();
    for (int i = 0; i < 4; i++) {
        tx_byte(t->tx_pin, req[i]);
    }

    line_listen(t->tx_pin);
    // Single-wire: tx_pin IS the UART wire. After TX, release to INPUT and listen
    // on the same pin for the TMC's reply (~8 bit-times after last TX bit).
    // Start polling before the 8-bit-time reply window (wait 4 bit-times).
    busy_wait_us_32(100);

    if (!rx_wait_start(t->tx_pin, 2000, &edge_us)) {
        line_idle(t->tx_pin);
        restore_interrupts(ints);
        return 0;
    }
    buf_out[0] = rx_byte_from_edge(t->tx_pin, edge_us);

    int n = 1;
    for (; n < 8; n++) {
        if (!rx_wait_start(t->tx_pin, 200, &edge_us)) break;
        buf_out[n] = rx_byte_from_edge(t->tx_pin, edge_us);
    }
    line_idle(t->tx_pin);
    restore_interrupts(ints);
    return n;
}

bool tmc_init(tmc_t *t, uint tx_pin, uint rx_pin, uint8_t addr) {
    t->tx_pin = tx_pin;
    t->rx_pin = rx_pin;
    t->addr = addr;

    gpio_init(tx_pin);
    line_idle(tx_pin);
    sleep_us(100);

    gpio_init(rx_pin);
    gpio_set_dir(rx_pin, GPIO_IN);
    gpio_pull_up(rx_pin);
    return true;
}
