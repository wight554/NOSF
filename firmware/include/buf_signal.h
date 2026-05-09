#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "controller_shared.h"

typedef enum {
    BUF_SRC_VIRTUAL_ENDSTOP = 0,
    BUF_SRC_ANALOG = 1,
} buf_source_kind_t;

typedef struct {
    float pos_norm;         /* normalized [-1..+1]: -1 = full trailing, +1 = full advance */
    float pos_mm;           /* physical mm (endstop: virtual position; analog: norm × half_travel) */
    float confidence;       /* 0.0..1.0 — signal reliability; low = treat as stale */
    uint32_t age_ms;        /* ms since this signal was last meaningfully updated */
    buf_state_t zone;       /* quantized: BUF_MID / ADVANCE / TRAILING / FAULT */
    buf_source_kind_t kind; /* which source produced this signal */
    bool fault;             /* source-reported hard fault */
} buf_signal_t;

/*
 * buf_source_t — vtable for a buffer signal source.
 * Populated by the adapter's init function; tick/read are called each control
 * cycle by buf_sensor_tick().  The full adapter split into separate .c files
 * is Phase 3 work (deferred until PSF hardware is available).
 */
typedef struct buf_source_s {
    void (*tick)(struct buf_source_s *src, uint32_t now_ms);
    void (*read)(struct buf_source_s *src, buf_signal_t *out);
    const char *name;
} buf_source_t;

/* The canonical signal produced by the active source for the current tick. */
extern buf_signal_t g_buf_signal;
