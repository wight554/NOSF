# NOSF – USB Serial Command Reference

All communication is over USB CDC serial at 115200 baud (line-buffered, `\n` terminated).

```
Request:   CMD:PAYLOAD\n   (payload may be empty: CMD:\n or just CMD\n)
Response:  OK:DATA\n       (data absent if not applicable: OK\n)
           ER:REASON\n
Events:    EV:TYPE:DATA\n  (unsolicited, emitted any time)
```

---

## Operation Flows

NOSF supports two primary operating modes, controlled by `RELOAD_MODE`:

### 1. Manual / Host-Controlled Flow (`RELOAD_MODE:0`)
Designed for standard MMU operation integrated with a host (Klipper).
- **Loading**: Initiated via `LO:` (Preload) or `FL:` (Full Load to Toolhead).
- **Unloading**: Initiated via `UL:` (Unload from extruder) or `UM:` (Unload from MMU).
- **Toolchange**: Managed via `TC:n` command sequence.
- **Sync**: Explicitly enabled by the host via `SM:1`.

### 2. Automated / RELOAD Flow (`RELOAD_MODE:1`)
Designed for standalone "Automatic Reload" and autonomous filament handling.
- **Seamless Auto-Load**: Inserting filament into an empty MMU automatically triggers a full load to the toolhead.
- **Pull-to-Sync**: Sync mode starts automatically when the buffer detects a pull (`BUF_ADVANCE`).
- **Auto-Reload**: If a runout is detected on the active lane, the controller automatically triggers a toolchange to the standby lane.
- **Auto-Stop**: Sync mode stops automatically if the filament is stationary for `SYNC_AUTO_STOP` ms.

---

## Motion Commands

| Command | Description |
|---------|-------------|
| `LO:` | **Preload** — runs forward at `AUTO_RATE` until OUT sensor triggers. Safety limit = `AUTOLOAD_MAX`. |
| `UL:` | **Unload (Extruder)** — runs reverse at `REV_RATE` until OUT sensor clears. Safety limit = `UNLOAD_MAX`. |
| `UM:` | **Unload (MMU)** — runs reverse at `REV_RATE` until IN sensor clears. Safety limit = `UNLOAD_MAX`. |
| `FL:` | **Full Load** — runs forward at `FEED_RATE` until toolhead sensor triggers (`TS:1`). Safety limit = `LOAD_MAX`. |
| `MV:mm:F`| **Exact Move** — move exactly `mm` distance at `F` mm/min. Disables sync. |
| `FD:` | **Continuous Feed** — runs forward until `ST:`. Use for manual purging. |
| `ST:` | **Stop** — aborts all motion and resets toolchange state. |
| `CU:` | **Cut** — performs the cutter sequence on the active lane. |

---

## Status & Diagnostics

| Command | Response |
|---------|----------|
| `?:` | **Full Status** — returns all internal state, sensor readings, and current rates. |
| `VR:` | **Version** — returns the firmware version string. |
| `SG:n` | **StallGuard** — returns the current StallGuard load for lane `n`. |
| `TS:<0\|1>`| **Toolhead Sensor** — report toolhead filament status (emitted by host). |
| `SM:<0\|1>`| **Sync Mode** — enable/disable buffer sync mode manually. |

---

## Parameters (`SET:` / `GET:`)

Parameters are categorized by function. All speeds are in **mm/min** (Klipper `F`). All limits are in **mm**.

### Motion & Speed
| Parameter | Description | Default |
|-----------|-------------|---------|
| `FEED_RATE` | Standard feeding speed (used for `FL:` and `TC:`) | 4000 |
| `REV_RATE`  | Standard retract speed (used for `UL:`, `UM:`) | 4000 |
| `AUTO_RATE` | Preload speed (used for `LO:`) | 2000 |
| `JOIN_RATE` | RELOAD: Fast approach speed for joining | 2000 |
| `PRESS_RATE`| RELOAD: Speed for follow-sync state | 1000 |
| `STARTUP_MS`| Ignore StallGuard for X ms after motor start (ramp-up) | 150 |

### Sync & Sensor Fusion
| Parameter | Description | Default |
|-----------|-------------|---------|
| `SYNC_MAX_RATE`| Maximum speed allowed during sync | 20000 |
| `SYNC_MIN_RATE`| Minimum speed allowed during sync | 200 |
| `SYNC_KP_RATE` | Sync correction gain (mm/min error to speed delta) | 500 |
| `SYNC_SG_INTERP`| Enable StallGuard-based speed regulation in buffer "blind spot" | 1 |
| `SG_TARGET`    | Target StallGuard load to maintain during sync | 40.0 |
| `SG_DERIV`     | RELOAD: Sharp load drop threshold for hit detection | 10 |
| `SYNC_AUTO_STOP`| Disable sync if idle for X ms | 2000 |

### Safety & Limits
| Parameter | Description | Default |
|-----------|-------------|---------|
| `LOAD_MAX`    | Maximum distance to push during `FL:` or `TC:LOAD` | 3000 |
| `UNLOAD_MAX`  | Maximum distance to pull during `UL:`, `UM:`, or `TC:UNLOAD` | 3000 |
| `AUTOLOAD_MAX`| Maximum distance to push during `LO:` (Preload) | 600 |
| `APPROACH_MAX`| Maximum distance to search for Y-splitter during RELOAD | 2000 |
| `STALL_MS`    | Hard-stall recovery time (ms) | 200 |

### Toolchange (Host Control)
| Parameter | Description | Default |
|-----------|-------------|---------|
| `TC_CUT_MS`   | Time to wait for cutter to cycle | 2000 |
| `TC_TH_MS`    | Time to wait for toolhead sensor (TS:1) after load start | 2000 |
| `TC_Y_MS`     | Time to wait for tail to clear Y-splitter during unload | 1000 |
| `SERVO_SETTLE`| Time for servo to reach target position | 500 |
| `RELOAD_MODE` | Enable autonomous RELOAD behavior | 0 |
