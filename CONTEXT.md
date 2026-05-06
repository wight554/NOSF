# NOSF — Project Context

Deep-dive reference for AI agents. Do not read this top-to-bottom — use the
navigation guide at the bottom to load only the sections relevant to your task.

---

## Firmware Architecture

Firmware is split into focused modules under `firmware/src/`.
There is no RTOS. The main loop calls a set of tick functions every iteration;
each tick function is a non-blocking state machine that checks `now_ms` and
returns immediately if nothing needs doing.

Primary ownership by module:

- `main.c`: top-level init, autopreload, LED state, main loop
- `motion.c`: per-lane motor/sensor state and task execution
- `sync.c`: buffer sensing, estimator-driven sync, boot stabilization
- `toolchange.c`: cutter, toolchange, RELOAD orchestration
- `protocol.c`: USB serial command parser, replies, status dump
- `settings_store.c`: persisted settings defaults/save/load and TMC apply

Key shared globals (declared via `controller_shared.h`):

```c
static lane_t   g_lane1, g_lane2;   // per-lane state
static tmc_t    g_tmc1,  g_tmc2;    // TMC2209 UART handles
static tc_ctx_t g_tc_ctx;           // toolchange / RELOAD state
static buf_t    g_buf;              // buffer arm position + zone
static int      active_lane;        // 1 or 2
```

All runtime-tunable parameters follow the same pattern — declared as a
`static` variable initialised from a `CONF_` compile-time constant:

```c
static int FEED_SPS = CONF_FEED_SPS;   // runtime var
// CONF_FEED_SPS is defined in firmware/include/config.h
```

---

## Key Data Structures

### `lane_t` — per-lane state

```c
typedef struct lane_s {
    din_t    in_sw, out_sw;          // IN / OUT sensor debounce state
    motor_t  m;                      // step/dir PWM motor handle
    task_t   task;                   // current lane task (see below)
    tmc_t   *tmc;                    // pointer to TMC2209 handle
    uint32_t motion_started_ms;      // timestamp of last lane_start()
  fault_t  fault;                  // FAULT_NONE / FAULT_TIMEOUT / …
    int      lane_id;                // 1 or 2
    // … other fields
} lane_t;
```

`task_t` values: `TASK_IDLE`, `TASK_FEED`, `TASK_AUTOLOAD`, `TASK_UNLOAD`,
`TASK_UNLOAD_MMU`, `TASK_LOAD_FULL`, `TASK_MOVE`

`fault_t` values: `FAULT_NONE`, `FAULT_TIMEOUT`,
`FAULT_SENSOR`, `FAULT_BUF`, `FAULT_CUT`

### `tc_state_t` — toolchange / RELOAD state machine

```c
TC_IDLE
TC_UNLOAD_CUT → TC_UNLOAD_WAIT_CUT → TC_UNLOAD_REVERSE →
  TC_UNLOAD_WAIT_OUT → TC_UNLOAD_WAIT_Y → TC_UNLOAD_WAIT_TH → TC_UNLOAD_DONE
TC_SWAP
TC_LOAD_START → TC_LOAD_WAIT_OUT → TC_LOAD_WAIT_TH → TC_LOAD_DONE
TC_RELOAD_WAIT_Y → TC_RELOAD_APPROACH → TC_RELOAD_FOLLOW          ← RELOAD path
TC_ERROR
```

RELOAD path detail:
- `TC_RELOAD_WAIT_Y`: old tail cleared OUT; wait for Y-splitter sensor to clear
- `TC_RELOAD_APPROACH`: motor at `JOIN_SPS`; exits to FOLLOW when the buffer
  enters `BUF_TRAILING`
- `TC_RELOAD_FOLLOW`: estimator-driven under-feed bounded by buffer state;
  exits on `BUF_ADVANCE` (extruder pickup confirmed) or timeout

### `tc_ctx_t` — RELOAD context

```c
typedef struct {
    tc_state_t state;
    int   target_lane, from_lane;
    uint32_t phase_start_ms;
    uint32_t reload_tick_ms;              // rate-limiter for SYNC_TICK_MS ticks
    int      sync_current_sps;
} tc_ctx_t;
```

### Buffer zones

`BUF_MID`, `BUF_ADVANCE`, `BUF_TRAILING` — declared in `buf_state_t`.
`BUF_SENSOR_TYPE`: `0` = dual-endstop, `1` = analog PSF.

---

## Settings Pattern

Every runtime parameter that must survive reboot goes through `settings_t`.

**To add a new runtime parameter — complete checklist:**

1. Add key to `config.ini.example` (and `config.ini` when needed).
2. Add default key to `scripts/gen_config.py` `DEFAULTS` and emit `CONF_*` in generated output.
3. Regenerate `firmware/include/tune.h` with `python3 scripts/gen_config.py`.
4. Add the runtime variable to the owning module (or shared runtime declaration if needed).
5. Add field to `settings_t` struct (around line 1660)
6. Add to `settings_defaults()`: `MY_PARAM = CONF_MY_PARAM;`
7. Add to `settings_save()`: `s.my_param = MY_PARAM;`
8. Add to `settings_load()`: `MY_PARAM = s->my_param;`
9. If the value must be written to hardware on load, add to the hardware-apply
  block after `settings_load()` in `settings_store.c`
10. Add `SET:` handler in `protocol.c`
11. Add `GET:` handler in `protocol.c`
12. **Bump `SETTINGS_VERSION`** at line ~1659

Current `SETTINGS_VERSION`: **21** (grep `main.c` to confirm before bumping)

---

## Critical Gotchas

### 1. `lane_start()` always resets `stall_armed` to `false`

```c
static void lane_start(lane_t *L, ...) {
    L->stall_armed = false;   // ← always cleared
    L->fault = FAULT_NONE;
    L->motion_started_ms = now_ms;
    // …
}
```

In normal operation the main loop re-arms it after `MOTION_STARTUP_MS`.
For RELOAD approach, `stall_armed = true` must be set **after** `lane_start()`:

```c
lane_start(NL, TASK_FEED, JOIN_SPS, true, now_ms, 0);
NL->stall_armed = true;   // must come after lane_start, not before
```

### 2. `sync_tick()` guard — only runs in `TC_IDLE`

```c
if (!sync_enabled || tc_state() != TC_IDLE) return;
```

Buffer sync must not run during any toolchange or RELOAD state.

### 3. RELOAD is buffer-driven

- `TC_RELOAD_APPROACH` no longer depends on driver load telemetry.
- Contact is defined by the buffer entering `BUF_TRAILING`.
- Safety comes from lane travel limits, `RELOAD_Y_MS`, and follow timeouts.

### 4. Sync and RELOAD share the same estimator

- Normal sync uses the extruder-rate estimator plus bounded zone bias.
- RELOAD follow reuses that estimator with an intentional under-feed factor.
- Dual-endstop and analog buffers differ only in how buffer state/position
  constrains the final target speed.

### 5. Fault handling is sensor- and timeout-driven

- Lane tasks still raise `FAULT_TIMEOUT`, `FAULT_SENSOR`, `FAULT_BUF`, and
  `FAULT_CUT`.
- `FAULT:DRY_SPIN` remains the sticky protection against spinning an empty lane.
- There is no driver-load-specific fault or recovery path anymore.

### 6. `MM_PER_STEP` and Speed Conversion

Speed params exposed over serial (SET/GET) are in **mm/min** and use the **`_RATE`** suffix. Internally all speeds are **SPS (steps per second)**. Helper functions in `main.c` handle the conversion:

```c
static inline int mm_per_min_to_sps(float mm_per_min);
static inline float sps_to_mm_per_min(int sps);
```

When adding a speed parameter, ensure the SET handler uses `mm_per_min_to_sps()` and the GET handler uses `sps_to_mm_per_min()`.

---

## Common Operations

### Add a SET/GET parameter (quick reference)

Look for `!strcmp(param, "STARTUP_MS")` in the SET block and
`snprintf(out, sizeof(out), "STARTUP_MS:%d"` in the GET block.

Pattern for speed parameters:
```c
// SET:
else if (!strcmp(param, "FEED_RATE")) FEED_SPS = mm_per_min_to_sps(fv);

// GET:
else if (!strcmp(param, "FEED_RATE")) snprintf(out, sizeof(out), "FEED_RATE:%.1f", (double)sps_to_mm_per_min(FEED_SPS));
```

### Emit an event

```c
cmd_event("MY_EVENT", "payload");   // → prints EV:MY_EVENT:payload\n
```

### Emit a reply

```c
cmd_reply("OK", NULL);              // → OK:
cmd_reply("OK", "KEY:VALUE");       // → OK:KEY:VALUE
cmd_reply("ER", "REASON");          // → ER:REASON
```

---

## Navigation Guide — What to Read for Each Task

| Task | Read |
|------|------|
| Add/modify runtime parameter | This file §Settings Pattern + §Gotcha 1 |
| Modify RELOAD approach or follow logic | `toolchange.c` around `TC_RELOAD_APPROACH` / `TC_RELOAD_FOLLOW` + `BEHAVIOR.md` §RELOAD contact and follow |
| Modify buffer sync | `sync.c` `sync_tick()` + `BEHAVIOR.md` §Buffer sync speed control |
| RELOAD tuning | `BEHAVIOR.md` §RELOAD contact and follow |
| Add a new serial command | `protocol.c` command dispatch block |
| Hardware pinout / sensor wiring | `HARDWARE.md` |
| Klipper macros or shell helper | `KLIPPER.md` |
| Full command / parameter reference | `MANUAL.md` |
| Cutter / servo logic | `toolchange.c` `cutter_tick()` + `BEHAVIOR.md` §Toolchange |
