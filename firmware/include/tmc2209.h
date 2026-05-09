#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "pico/types.h"
#include "hardware/pio.h"
typedef struct {
    uint tx_pin;
    uint rx_pin;
    uint8_t addr;
    PIO pio;
    uint sm_tx;
    uint sm_rx;
    uint offset_tx;
    uint offset_rx;
    uint32_t chopconf;
} tmc_t;
#define TMC_REG_GCONF       0x00
#define TMC_REG_GSTAT       0x01
#define TMC_REG_IFCNT       0x02
#define TMC_REG_IHOLD_IRUN  0x10
#define TMC_REG_TPWMTHRS    0x13
#define TMC_REG_TCOOLTHRS   0x14
#define TMC_REG_SGTHRS      0x40
#define TMC_REG_SG_RESULT   0x41
#define TMC_REG_CHOPCONF    0x6C
#define TMC_REG_DRV_STATUS  0x6F
#define TMC_REG_PWMCONF     0x70
bool tmc_init(tmc_t *t, uint tx_pin, uint rx_pin, uint8_t addr);
bool tmc_write(tmc_t *t, uint8_t reg, uint32_t val);
bool tmc_read(tmc_t *t, uint8_t reg, uint32_t *out);
uint8_t tmc_crc8(const uint8_t *data, uint8_t len);
bool tmc_set_run_current_ma(tmc_t *t, int run_ma, int hold_ma);
bool tmc_setup_chopconf(tmc_t *t, int microsteps, int toff, int tbl, int hstrt, int hend, bool intpol);
bool tmc_set_spreadcycle(tmc_t *t, bool spreadcycle);
bool tmc_set_stealthchop_sps(tmc_t *t, int sps, int microsteps);
bool tmc_set_sgthrs(tmc_t *t, uint8_t sgthrs);
bool tmc_set_tcoolthrs(tmc_t *t, uint32_t v);
bool tmc_set_pwmconf(tmc_t *t);
bool tmc_read_sg_result(tmc_t *t, uint16_t *out);
int  tmc_read_raw(tmc_t *t, uint8_t reg, uint8_t buf[8]);
