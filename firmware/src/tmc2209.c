#include "tmc2209.h"
#include <math.h>
#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "pico/stdlib.h"
#include "hardware/pio.h"
#include "tmc_uart.pio.h"

#define TMC_BAUD    40000u

// PIO program cache — keyed per PIO block (index 0 = pio0, 1 = pio1)
// Allows both PIO blocks to be used independently by different TMC instances.
typedef struct {
    bool loaded;
    uint offset_tx;
    uint offset_rx;
} tmc_pio_cache_t;

static tmc_pio_cache_t pio_cache[2];

static int pio_block_index(PIO pio) {
    return pio == pio1 ? 1 : 0;
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

bool tmc_init(tmc_t *t, uint tx_pin, uint rx_pin, uint8_t addr) {
    t->tx_pin = tx_pin;
    t->rx_pin = rx_pin; // Note: For ERB v2.0 hardware, tx_pin is single-wire bidirectional. rx_pin is typically unused or DIAG.
    t->addr = addr;
    t->pio = pio0; 

    int pidx = pio_block_index(t->pio);
    tmc_pio_cache_t *cache = &pio_cache[pidx];

    if (!cache->loaded) {
        if (pio_can_add_program(t->pio, &tmc_uart_tx_program) && pio_can_add_program(t->pio, &tmc_uart_rx_program)) {
            cache->offset_tx = pio_add_program(t->pio, &tmc_uart_tx_program);
            cache->offset_rx = pio_add_program(t->pio, &tmc_uart_rx_program);
            cache->loaded = true;
        } else {
            return false;
        }
    }

    t->offset_tx = cache->offset_tx;
    t->offset_rx = cache->offset_rx;

    t->sm_tx = pio_claim_unused_sm(t->pio, true);
    t->sm_rx = pio_claim_unused_sm(t->pio, true);

    tmc_uart_tx_program_init(t->pio, t->sm_tx, t->offset_tx, t->tx_pin, TMC_BAUD);
    tmc_uart_rx_program_init(t->pio, t->sm_rx, t->offset_rx, t->tx_pin, TMC_BAUD);

    pio_sm_set_enabled(t->pio, t->sm_tx, true); // Keep TX SM enabled permanently to manage pin state
    pio_sm_set_enabled(t->pio, t->sm_rx, true); // Keep RX SM enabled permanently to autonomously track line state

    // MUST enable internal pull-up so that when we switch to INPUT during reads,
    // the line is held HIGH (if TMC2209 pdn_disable=1 turns off its internal pull-down)
    gpio_pull_up(t->tx_pin);

    // Guarantee the pin is actively driven HIGH (OUTPUT) while idle
    pio_sm_set_consecutive_pindirs(t->pio, t->sm_tx, t->tx_pin, 1, true);

    return true;
}

static void tmc_uart_send_bytes(tmc_t *t, const uint8_t *buf, size_t len) {
    for (size_t i = 0; i < len; i++) {
        pio_sm_put_blocking(t->pio, t->sm_tx, buf[i]);
    }
    
    // Wait for FIFO to empty
    uint64_t timeout_us = time_us_64() + 2000;
    while (!pio_sm_is_tx_fifo_empty(t->pio, t->sm_tx) && time_us_64() < timeout_us) tight_loop_contents();
    
    // Wait for the state machine to stall at the 'pull' instruction (offset_tx).
    // NOTE: the PC advances to 'pull' the moment it fetches the stop-bit instruction,
    // before the 7-cycle [7] delay executes. So the SM stalls at pull ~21us BEFORE
    // the stop bit actually finishes on the wire. We add a mandatory 30us inter-frame
    // gap here so back-to-back calls never collide on the bus.
    timeout_us = time_us_64() + 2000;
    while (pio_sm_get_pc(t->pio, t->sm_tx) != t->offset_tx && time_us_64() < timeout_us) tight_loop_contents();
    busy_wait_us_32(30); // inter-frame gap: ensures stop bit fully clears the wire
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

    tmc_uart_send_bytes(t, buf, 8);
    return true;
}

// Perform the bus turnaround and receive 8 bytes from the TMC2209.
// Returns the number of bytes received (0-8).
static int tmc_read_bytes(tmc_t *t, uint8_t reg, uint8_t *buf) {
    uint8_t req[4];
    req[0] = 0x05;
    req[1] = t->addr;
    req[2] = reg & 0x7Fu;
    req[3] = tmc_crc8(req, 3);

    // Clear RX FIFO before starting to remove any old junk
    pio_sm_clear_fifos(t->pio, t->sm_rx);
    // Also force-reset the ISR to zero. If the RX SM was mid-frame receiving noise
    // when clear_fifos was called, the ISR could have stale bits that would corrupt
    // the MSB of the first received byte. MOV ISR, NULL zeroes it cleanly.
    pio_sm_exec(t->pio, t->sm_rx, pio_encode_mov(pio_isr, pio_null));

    tmc_uart_send_bytes(t, req, 4);

    // tmc_uart_send_bytes includes a 30us inter-frame gap covering the stop bit.
    // Add 10 more us to ensure the RX SM has pushed the final echo byte to FIFO.
    busy_wait_us_32(10);
    
    // Completely drain the RX FIFO of all echo bytes (4 bytes) and any preceding garbage
    while (!pio_sm_is_rx_fifo_empty(t->pio, t->sm_rx)) {
        pio_sm_get(t->pio, t->sm_rx);
    }

    // Now release the pin to INPUT so the TMC2209 can reply. 
    // (Requires internal pull-up and pdn_disable=1 so it stays HIGH during idle)
    pio_sm_set_consecutive_pindirs(t->pio, t->sm_tx, t->tx_pin, 1, false);

    uint64_t timeout_us = time_us_64() + 5000;
    int received = 0;
    
    while (received < 8 && time_us_64() < timeout_us) {
        if (!pio_sm_is_rx_fifo_empty(t->pio, t->sm_rx)) {
            buf[received++] = (uint8_t)(pio_sm_get(t->pio, t->sm_rx) >> 24);
        }
    }

    // Restore pin to OUTPUT HIGH
    pio_sm_set_consecutive_pindirs(t->pio, t->sm_tx, t->tx_pin, 1, true);

    return received;
}

bool tmc_read(tmc_t *t, uint8_t reg, uint32_t *out) {
    uint8_t rep[8];

    for (int attempt = 0; attempt < 2; attempt++) {
        int n = tmc_read_bytes(t, reg, rep);
        if (n < 8) continue;
        if (rep[0] != 0x05 || rep[1] != 0xFF || (rep[2] & 0x7Fu) != reg) continue;
        if (tmc_crc8(rep, 7) != rep[7]) continue;

        *out = ((uint32_t)rep[3] << 24) |
               ((uint32_t)rep[4] << 16) |
               ((uint32_t)rep[5] << 8) |
               (uint32_t)rep[6];
        return true;
    }
    return false;
}

int tmc_read_raw(tmc_t *t, uint8_t reg, uint8_t *buf_out) {
    return tmc_read_bytes(t, reg, buf_out);
}

static uint8_t clamp_u5_from_ma(int ma) {
    if (ma <= 0) return 0;
    float irms   = (float)ma / 1000.0f;
    float reff   = 0.110f + 0.020f;
    float sqrt2  = 1.41421356f;
    int v = (int)(32.0f * irms * reff * sqrt2 / 0.32f - 1.0f + 0.5f);
    if (v < 16) {
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

bool tmc_setup_chopconf(tmc_t *t, int microsteps, int toff, int tbl, int hstrt, int hend, bool intpol) {
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

    uint32_t reg_toff = (uint32_t)(toff & 0x0F);
    uint32_t reg_tbl = (uint32_t)(tbl & 0x03);
    uint32_t reg_hstrt = (uint32_t)(hstrt & 0x07);
    uint32_t reg_hend = (uint32_t)(hend & 0x0F);

    uint32_t chop = 0;
    chop |= (reg_toff  << 0);
    chop |= (reg_hstrt << 4);
    chop |= (reg_hend  << 7);
    chop |= (reg_tbl   << 15);
    chop |= (1u        << 17);
    chop |= ((uint32_t)mres << 24);
    if (intpol) {
        chop |= (1u << 28);
    }
    return tmc_write(t, TMC_REG_CHOPCONF, chop);
}

bool tmc_set_spreadcycle(tmc_t *t, bool spreadcycle) {
    uint32_t gconf = 0;
    if (spreadcycle) gconf |= (1u << 2);
    gconf |= (1u << 6);
    gconf |= (1u << 7);
    gconf |= (1u << 8);
    return tmc_write(t, TMC_REG_GCONF, gconf);
}

bool tmc_set_pwmconf(tmc_t *t) {
    uint32_t val = 0;
    val |= (36u  & 0xFFu);
    val |= (14u  & 0xFFu) << 8;
    val |= (1u   & 0x03u) << 16;
    val |= (1u << 18);
    val |= (1u << 19);
    val |= (8u   & 0x0Fu) << 24;
    val |= (12u  & 0x0Fu) << 28;
    return tmc_write(t, TMC_REG_PWMCONF, val);
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
