#include "tmc2209.h"

#include <math.h>

#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "pico/stdlib.h"

#define TMC_BAUD 115200u
#define TMC_BIT_NS 8681u

static inline void tmc_delay_ns(uint32_t ns) {
    busy_wait_us_32((ns + 999u) / 1000u);
}

static inline void line_idle(uint pin) {
    gpio_set_dir(pin, GPIO_IN);
    gpio_pull_up(pin);
}

static inline void line_drive_low(uint pin) {
    gpio_put(pin, 0);
    gpio_set_dir(pin, GPIO_OUT);
}

// ERB V2.0 has no external pull-up on PDN_UART; the TMC's internal PDN pull-down
// overcomes the RP2040 weak pull-up (~50kΩ). Drive HIGH actively so '1' bits and
// stop bits reach the required idle-high level on the wire.
static inline void line_drive_high(uint pin) {
    gpio_put(pin, 1);
    gpio_set_dir(pin, GPIO_OUT);
}

static void tx_byte(uint pin, uint8_t b) {
    line_drive_low(pin);
    tmc_delay_ns(TMC_BIT_NS);

    for (int i = 0; i < 8; i++) {
        if (b & 0x01) {
            line_drive_high(pin);
        } else {
            line_drive_low(pin);
        }
        b >>= 1;
        tmc_delay_ns(TMC_BIT_NS);
    }

    line_drive_high(pin);  // stop bit — must be driven, not floated
    tmc_delay_ns(TMC_BIT_NS);
}

// Returns true and captures edge timestamp when start bit (LOW) detected within timeout.
// Each 9µs rounded delay accumulates ~320ns/bit drift; capturing the edge time once
// and computing absolute sample offsets below eliminates that cumulative error.
static bool rx_wait_start(uint pin, uint32_t timeout_us, uint64_t *edge_us_out) {
    absolute_time_t deadline = make_timeout_time_us(timeout_us);
    line_idle(pin);
    while (gpio_get(pin)) {
        if (time_reached(deadline)) return false;
    }
    *edge_us_out = time_us_64();
    return true;
}

// Sample 8 data bits at absolute offsets from the detected start-bit edge.
// Center of data bit i = edge + (2i+3) * half_bit = edge + (2i+3) * 4340 ns.
// Using absolute timestamps keeps each sample within ~5% of center regardless
// of loop overhead, unlike the relative-delay approach that drifted ~37% by bit 7.
static uint8_t rx_byte_from_edge(uint pin, uint64_t edge_us) {
    uint8_t b = 0;
    for (int i = 0; i < 8; i++) {
        uint64_t sample_us = edge_us + ((uint64_t)(2 * i + 3) * 4340u + 500u) / 1000u;
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
    // Single-wire: tx_pin IS the UART wire. After TX, release to INPUT and listen
    // on the same pin for the TMC's reply (~8 bit-times after last TX bit).
    line_idle(t->tx_pin);
    tmc_delay_ns(TMC_BIT_NS * 4); // start polling before the 8-bit-time reply window

    if (!rx_wait_start(t->tx_pin, 2000, &edge_us)) {
        restore_interrupts(ints);
        return false;
    }
    rep[0] = rx_byte_from_edge(t->tx_pin, edge_us);

    for (int i = 1; i < 8; i++) {
        if (!rx_wait_start(t->tx_pin, 200, &edge_us)) {
            restore_interrupts(ints);
            return false;
        }
        rep[i] = rx_byte_from_edge(t->tx_pin, edge_us);
    }
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
    // TMC2209 current to CS — matches Klipper TMCCurrentHelper formula
    // CS = 32 * Irms * (Rsense + 0.020) * sqrt(2) / Vsense_ref - 1
    // Try VSENSE=0 (Vref=0.32V) first; if CS<16 switch to VSENSE=1 (Vref=0.18V)
    // Caller must set VSENSE bit in CHOPCONF to match.
    // For ERB Rsense=0.110: effective = 0.130
    // At 800mA VSENSE=0: CS=13 (<16) → VSENSE=1: CS=25 ✓ matches spreadsheet
    if (ma <= 0) return 0;
    float irms   = (float)ma / 1000.0f;
    float reff   = 0.110f + 0.020f;          // Rsense + 20mΩ per TMC docs
    float sqrt2  = 1.41421356f;
    // Try VSENSE=0 first
    int v = (int)(32.0f * irms * reff * sqrt2 / 0.32f - 1.0f + 0.5f);
    if (v < 16) {
        // Switch to VSENSE=1 for better CS resolution
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

bool tmc_set_microsteps(tmc_t *t, int microsteps) {
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

    uint32_t chop = 0;
    // CHOPCONF register layout (TMC2209 datasheet):
    // bits[3:0]   TOFF=3
    // bits[6:4]   HSTRT=7  (register value direct from spreadsheet)
    // bits[10:7]  HEND=10  (register value direct from spreadsheet)
    // bits[16:15] TBL=1
    // bit[17]     VSENSE=1
    // bits[27:24] MRES
    chop |= (3u  << 0);    // TOFF=3
    chop |= (7u  << 4);    // HSTRT=7
    chop |= (10u << 7);    // HEND=10
    chop |= (1u  << 15);   // TBL=1
    chop |= (1u  << 17);   // VSENSE=1
    chop |= ((uint32_t)mres << 24); // MRES
    return tmc_write(t, TMC_REG_CHOPCONF, chop);
}

bool tmc_set_spreadcycle(tmc_t *t, bool spreadcycle) {
    uint32_t gconf = 0;
    // Write GCONF without read — reads fail on ERB
    // bit2=en_spreadcycle, bit6=pdn_disable, bit7=mstep_reg_select
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


// Sends a read request and captures raw bytes on tx_pin without validating.
// Returns number of bytes received: 0=timeout (no response), 1-7=partial, 8=full frame.
int tmc_read_raw(tmc_t *t, uint8_t reg, uint8_t buf[8]) {
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
    line_idle(t->tx_pin);
    tmc_delay_ns(TMC_BIT_NS * 4);

    if (!rx_wait_start(t->tx_pin, 2000, &edge_us)) {
        restore_interrupts(ints);
        return 0;
    }
    buf[0] = rx_byte_from_edge(t->tx_pin, edge_us);

    int n = 1;
    for (; n < 8; n++) {
        if (!rx_wait_start(t->tx_pin, 200, &edge_us)) break;
        buf[n] = rx_byte_from_edge(t->tx_pin, edge_us);
    }
    restore_interrupts(ints);
    return n;
}

bool tmc_init(tmc_t *t, uint tx_pin, uint rx_pin, uint8_t addr) {
    t->tx_pin = tx_pin;
    t->rx_pin = rx_pin;
    t->addr = addr;
    // Precondition: actively drive tx_pin HIGH for 10ms to overcome the TMC's
    // internal PDN pull-down and establish idle-high before first UART byte.
    gpio_init(tx_pin);
    gpio_put(tx_pin, 1);
    gpio_set_dir(tx_pin, GPIO_OUT);
    sleep_ms(10);
    line_idle(tx_pin);  // release to INPUT/PULL_UP; line stays HIGH once TMC
                        // enables pdn_disable=1 (its own pull-up activates)
    // rx_pin is DIAG (active-high stall); pull-up prevents floating during init.
    gpio_init(rx_pin);
    gpio_set_dir(rx_pin, GPIO_IN);
    gpio_pull_up(rx_pin);
    return true;
}
