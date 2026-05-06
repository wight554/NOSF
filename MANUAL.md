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
- **Seamless Auto-Load**: Inserting filament into an empty MMU automatically triggers a full load to the toolhead. Safety limit = `LOAD_MAX` (default 3000mm).
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
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `FEED_RATE` | `feed_rate` | Standard feeding speed (used for `FL:` and `TC:`) | 4000 |
| `REV_RATE` | `rev_rate` | Standard retract speed (used for `UL:`, `UM:`) | 4000 |
| `AUTO_RATE` | `auto_rate` | Preload speed (used for `LO:`) | 2000 |
| `JOIN_RATE` | `join_rate` | RELOAD: Fast approach speed for joining | 2000 |
| `PRESS_RATE` | `press_rate` | RELOAD: Speed for follow-sync state | 1000 |
| `STARTUP_MS` | `motion_startup_ms` | Ignore StallGuard for X ms after motor start (ramp-up) | 150 |

### Sync & Sensor Fusion
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `SYNC_MAX_RATE` | `sync_max_rate` | Maximum speed allowed during sync | 20000 |
| `SYNC_MIN_RATE` | `sync_min_rate` | Minimum speed allowed during sync | 200 |
| `SYNC_KP_RATE` | `sync_kp_rate` | Sync correction gain (mm/min error to speed delta) | 500 |
| `SYNC_SG_INTERP` | `sync_sg_interp` | Enable StallGuard speed regulation in "blind spot" | 1 |
| `SG_TARGET` | `sg_target` | Target StallGuard load to maintain during sync | 40.0 |
| `SG_DERIV` | `sg_deriv` | RELOAD: Sharp load drop threshold for hit detection | 10 |
| `SYNC_AUTO_STOP` | `sync_auto_stop` | Disable sync if idle for X ms | 2000 |

### Safety & Limits
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `LOAD_MAX` | `load_max_mm` | Max distance for standard `FL:`, `TC:LOAD`, or **Auto-Load** | 3000 |
| `UNLOAD_MAX` | `unload_max_mm` | Max distance for `UL:`, `UM:`, or `TC:UNLOAD` | 3000 |
| `AUTOLOAD_MAX` | `autoload_max_mm` | Max distance for `LO:` (Preload to Y) | 600 |
| `STALL_MS` | `stall_recovery_ms` | Hard-stall recovery time (ms) | 200 |

### Physical Model (Hardware Dimensions)
These parameters describe the physical layout of your MMU and are used for intelligent safety modeling and sensor validation. All values are in **mm**.

| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `DIST_IN_OUT` | `dist_in_out` | Distance between IN and OUT sensors | 150 |
| `DIST_OUT_Y` | `dist_out_y` | Distance between OUT sensor and Y-splitter | 100 |
| `DIST_Y_BUF` | `dist_y_buf` | Distance between Y-splitter and buffer entry | 300 |
| `BUF_BODY_LEN`| `buf_body_len`| Physical length of the buffer body/tube | 200 |
| `BUF_SIZE` | `buf_size_mm` | Travel distance between absolute trailing and advance | 50 |

#### Measurement Tips:
- **DIST_IN_OUT**: Measure the tube distance between the IN and OUT sensors.
- **DIST_OUT_Y**: Measure the tube distance between the OUT sensor and the Y-splitter entry.
- **DIST_Y_BUF**: Measure the tube distance between the Y-splitter exit and the entry of the buffer body.
- **Y_TO_BUF_NEUTRAL**: The firmware calculates the center point as `DIST_Y_BUF + (BUF_SIZE / 2)`.
- **BUF_NEUTRAL_TO_EXIT**: The distance from neutral to the exit is `BUF_BODY_LEN - Y_TO_BUF_NEUTRAL`.
- **BUF_SIZE**: Total physical travel distance of the buffer arm between endstops.

### Toolchange & Hardware
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `TC_CUT_MS` | `tc_timeout_cut_ms` | Time to wait for cutter to cycle | 2000 |
| `TC_TH_MS` | `tc_timeout_th_ms` | Time to wait for toolhead sensor (TS:1) after load start | 2000 |
| `TC_Y_MS` | `tc_timeout_y_ms` | Time to wait for tail to clear Y during unload | 1000 |
| `RELOAD_Y_MS` | `reload_y_timeout_ms` | Max time for tail to clear Y during RELOAD | 10000 |
| `SERVO_SETTLE` | `servo_settle_ms` | Time for servo to reach target position | 500 |
| `RELOAD_MODE` | `reload_mode` | Enable autonomous RELOAD behavior | 0 |
| `RETRACT_MM` | `autoload_retract_mm` | Retract distance after reaching OUT during preload | 5 |
| `CUTTER` | `enable_cutter` | Master toggle for cutter logic | 1 |
