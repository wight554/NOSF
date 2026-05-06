# NOSF – USB Serial Command Reference

All communication is over USB CDC serial at 115200 baud (line-buffered, `\n` terminated).

```
Request:   CMD:PAYLOAD\n   (payload may be empty: CMD:\n or just CMD\n)
Response:  OK:DATA\n       (data absent if not applicable: OK\n)
           ER:REASON\n
Events:    EV:TYPE:DATA\n  (unsolicited, emitted any time)
```

Unsolicited `EV:` traffic is best-effort. Firmware drops events when USB CDC is not connected and rate-limits event emission to protect the control loop from serial backpressure.

---

## Operating Modes

NOSF behavior is controlled by two independent flags: **`AUTO_MODE`** (Flow Control) and **`RELOAD_MODE`** (Redundancy Control).

### 1. Flow Control (`AUTO_MODE`)
Controls whether the MMU handles internal breakpoints automatically or waits for the host.

- **Automated Flow (`AUTO_MODE:1`)** [Default]:
    - **Auto-Preload**: Inserting filament triggers a load to the OUT sensor (if `AUTO_PRELOAD` is 1).
    - **Auto-Sync**: Pulling the buffer arm (`BUF_ADVANCE`) automatically enables sync mode.
    - **Post-Load Sync**: Completing a `FL:` or `TC:` load automatically enables sync.
    - **Auto-Load**: If the MMU is empty, inserting filament triggers a full load to the toolhead.
- **Host-Controlled Flow (`AUTO_MODE:0`)**:
    - **Wait for Commands**: No unsolicited motion. NOSF only moves when it receives a serial command (`LO:`, `FL:`, `UL:`, `SM:1`, etc.).
    - **Status Only**: Emits runtime events (`EV:RUNOUT`, `EV:ACTIVE`, `EV:SYNC:...`, etc.) and waits for host instructions.

### 2. Redundancy Control (`RELOAD_MODE`)
Controls whether the MMU automatically swaps lanes on filament runout.

- **RELOAD Enabled (`RELOAD_MODE:1`)**:
    - **Auto-Swap**: If the active lane runs out, the controller automatically triggers a toolchange to the standby lane.
    - **Standalone Redundancy**: Designed to keep a print running without requiring host-side macros for runout recovery.
- **RELOAD Disabled (`RELOAD_MODE:0`)**:
    - **Standard MMU**: Runout events are reported to the host, but no autonomous swapping occurs.

> [!TIP]
> These flags can be combined. For example, `AUTO_MODE:0` + `RELOAD_MODE:1` allows a host to control all loading logic while still letting the MMU handle a runout swap autonomously if needed.

---

## Command Reference

### Motion Control
| Command | Mode | Description |
|---------|------|-------------|
| `T:n` | Both | **Select Active Lane** — set active lane to `1` or `2` without moving filament. |
| `LO:` | Manual| **Preload** — runs forward until OUT sensor triggers. Limit: `AUTOLOAD_MAX`. |
| `FL:` | Manual| **Full Load** — runs forward until toolhead sensor triggers (`TS:1`). Limit: `LOAD_MAX`. |
| `UL:` | Both  | **Unload (Extruder)** — reverse until OUT sensor clears. If buffer enters `ADVANCE`, performs a one-shot gentle forward recovery move (~half buffer travel), then resumes reverse unload. Limit: `UNLOAD_MAX`. |
| `UM:` | Both  | **Unload (MMU)** — reverse until IN sensor clears. Limit: `UNLOAD_MAX`. |
| `TC:n` | Manual| **Toolchange** — Unload active lane and load lane `n`. |
| `MV:mm:F[:D]`| Both | **Exact Move** — move `abs(mm)` at `F` mm/min. Direction from sign of `mm` or optional `D` (`F`/`R`/`B`, `+`/`-`). Disables sync. |
| `FD:` | Both  | **Continuous Feed** — runs forward until `ST:`. |
| `ST:` | Both  | **Stop** — aborts all motion and resets toolchange state. |
| `CU:` | Both  | **Cut** — performs the cutter sequence on the active lane. |

### Status & Configuration
| Command | Response | Description |
|---------|----------|-------------|
| `?:` | Status | **Full Status** — returns all sensors, tasks, and rates. |
| `VR:` | Version| **Version** — returns firmware version. |
| `TS:<0\|1>`| OK | **Toolhead Sensor** — report toolhead filament status (sent by host). |
| `SM:<0\|1>`| OK | **Sync Mode** — manually toggle buffer sync. |
| `BI:<0\|1>`| OK | **Buffer Invert** — invert buffer endstop logic. |
| `SV:` | OK | **Save Settings** — persist current runtime parameters to flash. Rejected with `ER:PERSIST_BUSY` while motion, toolchange, cutter activity, or boot buffer stabilization is active. |
| `LD:` | OK | **Load Settings** — reload persisted settings from flash. Rejected with `ER:PERSIST_BUSY` while motion, toolchange, cutter activity, or boot buffer stabilization is active. |
| `RS:` | OK | **Reset Settings** — restore defaults and save them to flash. Rejected with `ER:PERSIST_BUSY` while motion, toolchange, cutter activity, or boot buffer stabilization is active. |
| `CA:lane:ma` | OK | **Set Run Current** — immediately program the lane TMC run current in mA. |
| `BOOT:` | OK | **Reboot To BOOTSEL** — reboot into RP2040 USB boot mode for flashing. |

### Driver Access
| Command | Response | Description |
|---------|----------|-------------|
| `TW:lane:reg:val` | OK | **TMC Write** — write raw TMC register value. Bring-up / diagnostics only. |
| `TR:lane:reg` | `OK:lane:reg:0x...` | **TMC Read** — read raw TMC register value. |
| `RR:lane` | Probe dump | **UART Probe** — try TMC addresses `0..3` and return the raw reply frames for bring-up/debug. |

These commands are intended for low-level diagnostics and board bring-up. Prefer normal `SET:` / `GET:` parameters for supported runtime configuration.

---

## Parameters (`SET:` / `GET:`)

### Physical Model (Hardware Dimensions)
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `DIST_IN_OUT` | `dist_in_out` | Distance between IN and OUT sensors | 150 |
| `DIST_OUT_Y` | `dist_out_y` | Distance between OUT sensor and Y-splitter | 100 |
| `DIST_Y_BUF` | `dist_y_buf` | Distance between Y-splitter and buffer entry | 300 |
| `BUF_BODY_LEN`| `buf_body_len`| Physical length of the buffer body/tube | 200 |
| `BUF_SIZE` | `buf_size_mm` | Travel distance of the buffer arm | 50 |

### Speeds & Rates (mm/min)
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `FEED_RATE` | `feed_rate` | Standard feeding speed | 4000 |
| `REV_RATE` | `rev_rate` | Standard retract speed | 4000 |
| `AUTO_RATE` | `auto_rate` | Preload speed (`LO:`) | 2000 |
| `BUF_STAB_RATE` | `buf_stab_rate` | Buffer stabilization speed for boot neutralization and UL advance-recovery move | 600 |
| `JOIN_RATE` | `join_rate` | RELOAD: Fast approach speed | 1600 |
| `PRESS_RATE` | `press_rate` | RELOAD: Slow follow-sync speed | 1200 |
| `SYNC_HARD_MAX_RATE` | `sync_hard_max_rate` | Absolute sync speed ceiling (independent of `SYNC_MAX_RATE`) | 2500 |
| `SYNC_MAX_RATE` | `sync_max_rate` | Max speed allowed during sync | 20000 |

### Smarter Sync (Estimator)
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `EST_ALPHA_MIN`| `est_alpha_min` | Estimator responsiveness for slow drifts | 0.10 |
| `EST_ALPHA_MAX`| `est_alpha_max` | Estimator responsiveness for sharp jumps | 0.60 |
| `ZONE_BIAS_BASE`| `zone_bias_base_rate`| Base centering pull (mm/min) | 100 |
| `ZONE_BIAS_RAMP`| `zone_bias_ramp_rate`| Centering ramp (mm/min per second stuck in zone) | 50 |
| `ZONE_BIAS_MAX` | `zone_bias_max_rate` | Max centering correction (mm/min) | 400 |
| `RELOAD_LEAN`  | `reload_lean_factor` | RELOAD follow under-feed factor (0.0 to 1.0) | 0.85 |

### Safety & Timeouts
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `LOAD_MAX` | `load_max_mm` | Max distance for `FL:` or **Auto-Load** | 3000 |
| `UNLOAD_MAX` | `unload_max_mm` | Max distance for `UL:`, `UM:` | 3000 |
| `AUTO_MODE` | `auto_mode` | Enable autonomous Flow (Auto-Sync, Toolhead load) | 1 |
| `AUTO_PRELOAD`| `auto_preload` | Enable parking preload on insertion | 1 |
| `RELOAD_MODE`| `reload_mode` | Enable autonomous RELOAD behavior (Auto-Swap) | 0 |
| `SYNC_OVERSHOOT_PCT` | `sync_overshoot_pct` | ADVANCE-only extra push as percent of sync KP correction (0..200) | 50 |
| `SYNC_AUTO_STOP` | `sync_auto_stop_ms` | Auto-mode only: disable auto-started sync after sustained `TRAILING` for X ms | 5000 |
| `RELOAD_Y_MS` | `reload_y_timeout_ms` | Max time for tail to clear Y during RELOAD | 10000 |

---

## Events (`EV:`)

| Event | Data | Description |
|-------|------|-------------|
| `AUTO_LOAD` | `lane` | Automatic full-load was started because the controller was empty when filament was inserted. |
| `PRELOAD` | `lane` | Automatic preload-to-OUT was started on filament insertion. |
| `RUNOUT` | `lane` | Filament runout detected on specified lane. |
| `LOADED` | `lane` | Filament successfully reached the toolhead/gears. |
| `UNLOADED`| `lane` | Filament successfully retracted past the OUT or IN sensor. |
| `LOAD_TIMEOUT` | `lane` | A load task hit its configured distance limit before completion. |
| `UNLOAD_TIMEOUT` | `lane` | An unload task hit its configured distance limit before completion. |
| `MOVE_DONE` | `lane` | Exact move completed. |
| `ACTIVE` | `lane\|NONE`| Reported when the active lane changes. |
| `FAULT:DRY_SPIN`| `lane` | Motor spinning > 8s without filament (`IN` clear). |
| `SYNC` | `AUTO_START\|AUTO_STOP` | Automatic sync state transitions. |
| `BS` | Mode-specific snapshot | Periodic buffer/sync status event used during sync and RELOAD follow. |
| `TC:*` | Phase-specific | Toolchange progress events such as `TC:UNLOADING`, `TC:SWAPPING`, `TC:LOADING`, `TC:DONE`, `TC:ERROR`. |
| `RELOAD:*` | Phase-specific | RELOAD progress and fault events such as `RELOAD:SWITCHING`, `RELOAD:JOINING`, `RELOAD:LOADED`, `RELOAD:FAULT`. |
| `CUT:FEEDING` | — | Cutter feed phase started. |

### Fault Recovery
Most faults (`TIMEOUT`, sensor-related faults) are transient and reset on the next command.
**`FAULT:DRY_SPIN`** is sticky: it blocks automatic background tasks (Sync, RELOAD follow) to prevent motor wear. It clears automatically when a new spool is inserted (`IN` sensor triggers) or when a manual load command (`LO:`, `FL:`, etc.) is issued.
