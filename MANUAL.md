# NOSF – USB Serial Command Reference

All communication is over USB CDC serial at 115200 baud (line-buffered, `\n` terminated).

```
Request:   CMD:PAYLOAD\n   (payload may be empty: CMD:\n or just CMD\n)
Response:  OK:DATA\n       (data absent if not applicable: OK\n)
           ER:REASON\n
Events:    EV:TYPE:DATA\n  (unsolicited, emitted any time)
```

---

## Operating Modes

NOSF behavior is fundamentally defined by the `RELOAD_MODE` flag.

### 1. Manual / Host-Controlled Mode (`RELOAD_MODE:0`)
In this mode, NOSF acts as a standard MMU, waiting for explicit commands from a host (e.g., Klipper).
- **Manual Loading**: Use `LO:` to preload and `FL:` to load to the toolhead.
- **Manual Swaps**: Swaps are initiated by the host sending `TC:n`.
- **Explicit Sync**: Sync mode must be enabled via `SM:1` and disabled via `SM:0`.
- **Runout Reporting**: If a lane runs out, NOSF emits `EV:RUNOUT:n` and stops. The host must decide how to recover.

### 2. Automated / RELOAD Mode (`RELOAD_MODE:1`)
In this mode, NOSF acts as an autonomous redundancy controller.
- **Autonomous Loading**: Simply inserting filament into an empty lane (`IN:1`) triggers an automatic load to the toolhead if it's currently empty.
- **Automatic Sync**: Sync mode starts automatically when the buffer is pulled (`BUF_ADVANCE`) and stops when stationary for `SYNC_AUTO_STOP` ms.
- **Auto-Reload**: If the active lane runs out, NOSF automatically triggers a toolchange to the standby lane (if filament is present there).
- **Handshake-Free**: Designed to keep the printer running without requiring complex host-side macros for runout recovery.

---

## Command Reference

### Motion Control
| Command | Mode | Description |
|---------|------|-------------|
| `LO:` | Manual| **Preload** — runs forward until OUT sensor triggers. Limit: `AUTOLOAD_MAX`. |
| `FL:` | Manual| **Full Load** — runs forward until toolhead sensor triggers (`TS:1`). Limit: `LOAD_MAX`. |
| `UL:` | Both  | **Unload (Extruder)** — reverse until OUT sensor clears. Limit: `UNLOAD_MAX`. |
| `UM:` | Both  | **Unload (MMU)** — reverse until IN sensor clears. Limit: `UNLOAD_MAX`. |
| `TC:n` | Manual| **Toolchange** — Unload active lane and load lane `n`. |
| `MV:mm:F`| Both | **Exact Move** — move `mm` distance at `F` mm/min. Disables sync. |
| `FD:` | Both  | **Continuous Feed** — runs forward until `ST:`. |
| `ST:` | Both  | **Stop** — aborts all motion and resets toolchange state. |
| `CU:` | Both  | **Cut** — performs the cutter sequence on the active lane. |

### Status & Configuration
| Command | Response | Description |
|---------|----------|-------------|
| `?:` | Status | **Full Status** — returns all sensors, tasks, and rates. |
| `VR:` | Version| **Version** — returns firmware version. |
| `SG:n` | SG Value| **StallGuard** — returns current load for lane `n`. |
| `TS:<0\|1>`| OK | **Toolhead Sensor** — report toolhead filament status (sent by host). |
| `SM:<0\|1>`| OK | **Sync Mode** — manually toggle buffer sync. |
| `BI:<0\|1>`| OK | **Buffer Invert** — invert buffer endstop logic. |

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
| `JOIN_RATE` | `join_rate` | RELOAD: Fast approach speed | 2000 |
| `PRESS_RATE` | `press_rate` | RELOAD: Slow follow-sync speed | 1000 |
| `SYNC_MAX_RATE` | `sync_max_rate` | Max speed allowed during sync | 20000 |

### Safety & Timeouts
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `LOAD_MAX` | `load_max_mm` | Max distance for `FL:` or **Auto-Load** | 3000 |
| `UNLOAD_MAX` | `unload_max_mm` | Max distance for `UL:`, `UM:` | 3000 |
| `RELOAD_MODE`| `reload_mode` | Enable autonomous RELOAD behavior | 0 |
| `SYNC_AUTO_STOP` | `sync_auto_stop` | Disable sync if idle for X ms | 2000 |
| `RELOAD_Y_MS` | `reload_y_timeout_ms` | Max time for tail to clear Y during RELOAD | 10000 |
