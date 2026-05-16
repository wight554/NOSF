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
| `RL:` | Manual| **Reload Load** — manually triggers RELOAD sync. Pushes active lane to approach and follow a disconnected tail. |
| `UL:` | Both  | **Unload (Extruder)** — reverse until OUT sensor clears. Limit: `UNLOAD_MAX`. |
| `UM:` | Both  | **Unload (MMU)** — reverse until IN sensor clears. Limit: `UNLOAD_MAX`. |
| `TC:n` | Manual| **Toolchange** — Unload active lane and load lane `n`. |
| `MV:mm:F[:D]`| Both | **Exact Move** — move `abs(mm)` at `F` mm/min. Direction from sign of `mm` or optional `D` (`F`/`R`/`B`, `+`/`-`). Disables sync. |
| `FD:` | Both  | **Continuous Feed** — runs forward until `ST:`. |
| `BS:` | Both  | **Buffer Stabilize** — if the controller is idle, run the buffer neutralization move immediately to bring a dual-endstop buffer back toward `MID`. |
| `ST:` | Both  | **Stop** — aborts all motion and resets toolchange state. |
| `CU:` | Both  | **Cut** — performs the full cutter sequence (Open -> Feed -> Close -> Open -> Repeat -> Block) on the active lane. |
| `CX:` | Both  | **Bare Cut** — performs the cutter sequence without filament movement (Open -> Close -> Open -> Repeat -> Block). |
| `CP:us` | Both  | **Cutter Position** — moves the cutter servo to the specified pulse width (400-2700 us) and stays there. Useful for mechanical tuning. |

### Status & Configuration
| Command | Response | Description |
|---------|----------|-------------|
| `?:` | Status | **Full Status** — returns all sensors, tasks, and rates. |
| `VR:` | Version| **Version** — returns firmware version. |
| `TS:<0\|1>`| OK | **Toolhead Sensor** — report toolhead filament status (sent by host). |
| `SM:<0\|1>`| OK | **Sync Mode** — manually toggle buffer sync. |
| `BI:<0\|1>`| OK | **Buffer Invert** — invert buffer endstop logic. |
| `MARK:<tag>` | `OK:MARK` | **Telemetry Marker** — stores a short host marker in firmware. Subsequent status replies expose it as `MK:<seq>:<tag>`. |
| `SV:` | OK | **Save Settings** — persist current runtime parameters to flash. Rejected with `ER:PERSIST_BUSY` while motion, toolchange, cutter activity, or buffer stabilization is active. |
| `LD:` | OK | **Load Settings** — reload persisted settings from flash. Rejected with `ER:PERSIST_BUSY` while motion, toolchange, cutter activity, or buffer stabilization is active. |
| `RS:` | OK | **Reset Settings** — restore defaults and save them to flash. Rejected with `ER:PERSIST_BUSY` while motion, toolchange, cutter activity, or buffer stabilization is active. |
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
| `BUF_HALF_TRAVEL` | `buf_half_travel_mm` | Distance from MID to a dual-endstop switch trip point | 7.8 |
| `BUF_SIZE` | `buf_size_mm` | Travel distance of the buffer arm | 22 |

### Speeds & Rates (mm/min)
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `FEED_RATE` | `feed_rate` | Standard feeding speed | 3000 |
| `REV_RATE` | `rev_rate` | Standard retract speed | 3000 |
| `AUTO_RATE` | `auto_rate` | Preload speed (`LO:`) | 3000 |
| `BUF_STAB_RATE` | `buf_stab_rate` | Buffer stabilization speed for boot neutralization and UL advance-recovery move | 600 |
| `JOIN_RATE` | `join_rate` | RELOAD: Fast approach speed | 1600 |
| `PRESS_RATE` | `press_rate` | RELOAD: Slow follow-sync speed | 1200 |
| `GLOBAL_MAX_RATE` | `global_max_rate` | Absolute ceiling applied to every commanded motor rate; `SYNC_MAX_RATE` remains the sync-only soft cap under it | 4000 |
| `SYNC_MAX_RATE` | `sync_max_rate` | Max speed allowed during sync | 4000 |
| `BASELINE_RATE` | `baseline_rate` | Sync bootstrap and conservative baseline speed | 1600 |

### Smarter Sync (Estimator)
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `SYNC_TICK_MS` | `sync_tick_ms` | Period between sync-controller updates | 20 |
| `SYNC_UP_RATE` | `sync_ramp_up_rate` | Max sync-speed increase applied each control tick | 40 |
| `SYNC_DN_RATE` | `sync_ramp_dn_rate` | Max sync-speed decrease applied each control tick | 12 |
| `BASELINE_ALPHA` | `baseline_alpha` | Settled-MID baseline adaptation factor | 0.02 |
| `BUF_PREDICT_THR_MS` | `buf_predict_thr_ms` | MID-dwell threshold used by advance prediction | 250 |
| `SYNC_KP_RATE` | `sync_kp_rate` | Proportional reserve-correction window around the virtual buffer target | 900 |
| `EST_ALPHA_MIN`| `est_alpha_min` | Estimator responsiveness for slow drifts | 0.12 |
| `EST_ALPHA_MAX`| `est_alpha_max` | Estimator responsiveness for sharp jumps | 0.65 |
| `SYNC_RESERVE_PCT` | `sync_reserve_pct` | Normal-sync reserve target as % of `BUF_HALF_TRAVEL` toward trailing | 35 |
| `TRAIL_BIAS_FRAC` | `sync_trailing_bias_frac` | Additional trailing-side setpoint shift (0.0 to 0.7) | 0.0 |
| `MID_CREEP_TIMEOUT_MS` | `mid_creep_timeout_ms` | Mid-dwell wait before creep activates | 0 |
| `MID_CREEP_RATE` | `mid_creep_rate_sps_per_s` | Creep ramp slope (SPS/s) | 0 |
| `MID_CREEP_CAP` | `mid_creep_cap_frac` | Hard cap on creep as % of extruder_est_sps | 10 |
| `VAR_BLEND_FRAC` | `buf_variance_blend_frac` | Max variance-aware blend fraction (0.0=OFF) | 0.0 |
| `VAR_BLEND_REF_MM` | `buf_variance_blend_ref_mm` | Sigma value at which blend distrust saturates | 1.0 |
| `ZONE_BIAS_BASE`| `zone_bias_base_rate`| Base reserve-recovery correction around the virtual buffer target (mm/min) | 90 |
| `ZONE_BIAS_RAMP`| `zone_bias_ramp_rate`| Extra reserve-recovery ramp while buffer stays away from target (mm/min per second) | 30 |
| `ZONE_BIAS_MAX` | `zone_bias_max_rate` | Max reserve-recovery correction (mm/min) | 600 |
| `RELOAD_LEAN`  | `reload_lean_factor` | RELOAD follow over-feed factor (0.0 to 5.0) | 1.15 |
| `LIVE_TUNE_LOCK` | _(runtime only)_ | Debug-only host live-write guard. The default observe-only tuner does not use it. `SET:LIVE_TUNE_LOCK:1` blocks live writes to `BASELINE_RATE`/`BASELINE_SPS`, `TRAIL_BIAS_FRAC`, `MID_CREEP_*`, and `VAR_BLEND_*`/`BUF_VARIANCE_*`; `GET:LIVE_TUNE_LOCK` returns `0` or `1`. Not persisted; resets to `0` on boot. | 0 |

### Safety & Timeouts
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `RAMP_TICK_MS` | `ramp_tick_ms` | Period between lane acceleration ramp steps | 5 |
| `LOAD_MAX` | `load_max_mm` | Max distance for `FL:` or **Auto-Load** | 3000 |
| `UNLOAD_MAX` | `unload_max_mm` | Max distance for `UL:`, `UM:` | 3000 |
| `AUTO_MODE` | `auto_mode` | Enable autonomous Flow (Auto-Sync, Toolhead load) | 1 |
| `AUTO_PRELOAD`| `auto_preload` | Enable parking preload on insertion | 1 |
| `RELOAD_MODE`| `reload_mode` | Enable autonomous RELOAD behavior (Auto-Swap) | 1 |
| `RUNOUT_COOLDOWN_MS` | `runout_cooldown_ms` | Cooldown before another runout can be reported on the same lane | 12000 |
| `SYNC_OVERSHOOT_PCT` | `sync_overshoot_pct` | Extra trailing-side trim as percent of sync correction after reserve overshoots full (0..200) | 25 |
| `SYNC_OVERSHOOT_MID_EXT` | `sync_overshoot_mid_extend` | Feature flag: extend trailing overshoot trim into `BUF_MID` when virtual position is below the deadband. Default OFF; only enable after A/B evidence from long-run logs. | 0 |
| `SYNC_AUTO_STOP` | `sync_auto_stop_ms` | Auto-mode only: tail-assist stop after sustained `TRAILING`; in normal print sync, stops if continuous `TRAILING` dwell exceeds the timeout and recovery speed has collapsed to the minimum sync floor. | 5000 |
| `SYNC_ADV_STOP_MS` | `sync_advance_dwell_stop_ms` | Hard stop if continuously pinned at advance endstop for this many ms. 0 = disable. | 6000 |
| `SYNC_ADV_RAMP_MS` | `sync_advance_ramp_delay_ms` | Grace window before refill-assist overrides target to `SYNC_MAX_RATE`, bypassing the estimator ceiling. 0 = disable. | 400 |
| `SYNC_INT_GAIN` | `sync_reserve_integral_gain` | Integral reserve-centering gain (mm of target bias per mm·s of reserve error). **0.0 = disabled** (default; behavior identical to Phase 2). Enable with a small value (e.g. 0.005) after reviewing long-run soak logs. | 0.0 |
| `SYNC_INT_CLAMP` | `sync_reserve_integral_clamp_mm` | Maximum integral correction magnitude in mm. The integral cannot shift the effective reserve target by more than this amount. | 0.6 |
| `SYNC_INT_DECAY_MS` | `sync_reserve_integral_decay_ms` | Reserved for future integral decay rate. 0 = hold integral value when frozen. | 0 |
| `EST_SIGMA_CAP` | `est_sigma_hard_cap_mm` | Estimator sigma hard cap in mm. Confidence (`EC`) drops to 0 when the physics-based position uncertainty reaches this level. | 1.5 |
| `EST_LOW_CF_THR` | `est_low_cf_warn_threshold` | `EV:BUF,EST_LOW_CF` fires when estimator confidence falls below this threshold (runtime-only, not persisted). | 0.5 |
| `EST_FALLBACK_THR` | `est_fallback_cf_threshold` | Integral centering freezes when confidence falls below this threshold. Also the floor below which `EV:BUF,EST_FALLBACK` is eligible (runtime-only). | 0.2 |
| `BUF_DRIFT_TAU_MS` | `buf_drift_ewma_tau_ms` | EWMA time constant for per-transition residual drift estimate (ms). Longer = more stable; shorter = adapts faster. | 60000 |
| `BUF_DRIFT_MIN_SMP` | `buf_drift_min_samples` | Transition samples required for full-strength drift correction. When correction is explicitly enabled, it ramps in from the first sample to this count. | 3 |
| `BUF_DRIFT_THR_MM` | `buf_drift_apply_thr_mm` | Minimum `|BPD|` required to apply correction (mm). **0.0 = disabled**. Provisional print default applies correction only after meaningful observed drift. | 2.0 |
| `BUF_DRIFT_CLAMP` | `buf_drift_clamp_mm` | Hard clamp on applied drift correction magnitude in mm. Runtime range: 0.0–8.0. | 3.0 |
| `BUF_DRIFT_MIN_CF` | `buf_drift_apply_min_cf` | Minimum estimator confidence (`EC`/100) required to apply drift correction. Correction freezes (but EWMA continues accumulating) when below this. | 0.5 |
| `ADV_RISK_WINDOW` | `adv_risk_window_ms` | Rolling window for `APX` advance-pin density (ms). Runtime-only, not persisted. | 60000 |
| `ADV_RISK_THR` | `adv_risk_threshold` | `EV:SYNC,ADV_RISK_HIGH` fires when `APX >= this`. 0 = disable. Runtime-only, not persisted. | 4 |
| `POST_PRINT_STAB_MS` | `post_print_stab_delay_ms` | Delay before idle+`TRAILING` recovery starts; once triggered, the low-speed post-print stabilization move settles the buffer back to `MID` and only falls back to the advance-side handoff if it overshoots center. `0` starts immediately | 0 |
| `RELOAD_Y_MS` | `reload_y_timeout_ms` | Max time for tail to clear Y during RELOAD | 10000 |
| `RELOAD_JOIN_MS` | `reload_join_delay_ms` | Extra RELOAD-only settling delay after tail and Y clear before `RELOAD:JOINING` starts | 10000 |
| `STEALTHCHOP` | `stealthchop_threshold` | Velocity threshold (mm/min) for StealthChop. 0 = always SpreadCycle. | 500 |

`BASELINE_RATE` remains a persistent bootstrap target. AUTO sync no longer rewrites it during startup.

Runtime status `BL` is the learned control baseline. `GET:` / `SET:` / `SV:` / `LD:` for `BASELINE_RATE` operate on the configured bootstrap target.

`BUF_TRAVEL` remains accepted as a backward-compatible alias for `BUF_HALF_TRAVEL`.

### Cutter / Servo
| Parameter | `config.ini` Key | Description | Default |
|-----------|------------------|-------------|---------|
| `SERVO_BLOCK` | `servo_block_us` | Servo block position used between cutter phases | 950 |

### Diagnostic Status Fields (tail-appended)

These fields are appended after `SS:` in the `?:` response. They are additive and do not shift existing field positions.

| Field | Unit | Description |
|-------|------|-------------|
| `RT` | mm (signed) | Reserve target position. Negative = trailing side. Set by `SYNC_RESERVE_PCT`, `TRAIL_BIAS_FRAC`, and `BUF_HALF_TRAVEL`. |
| `TB` | % (int) | Trailing bias fraction × 100. |
| `MC` | SPS | Mid-zone creep component added to target rate |
| `VB` | % (int) | Variance blend distrust percentage |
| `BPV`| mm × 100 | Post-blend effective position used by control loops |
| `MK` | seq:tag | Telemetry marker tag and sequence number set by the most recent `MARK:` command. |
| `RD` | mm | Reserve deadband width around the target. |
| `AD` | ms | Time the buffer arm has been continuously pinned at the advance-side switch. Zero when not in `BUF_ADVANCE`. |
| `TD` | ms | Time the buffer arm has been continuously pinned at the trailing-side switch. Zero when not in `BUF_TRAILING`. |
| `TW` | ms | Estimated time to trailing wall (remaining physical margin ÷ current net push velocity). Capped at 99999 when not applicable or well out of range. |
| `EA` | ms | Age of the extruder velocity estimate — time since the estimator was last updated by a zone transition or bleed. |
| `SK` | enum | Active buffer sensor kind: `0` = virtual endstop, `1` = analog. |
| `CF` | 0.0–1.0 | Signal confidence from the active source. Below ~0.5 indicates saturation or stale data; the control loop treats values below 0.4 as unreliable. |
| `RI` | mm (signed) | Reserve integral term — slow centering correction added to the reserve target. |
| `RC` | 0–100 | Effective integral gain scalar (0 = frozen/disabled, 100 = active). |
| `ES` | mm | Estimator sigma — physics-based position uncertainty in mm. |
| `EC` | 0–100 | Estimator confidence based on sigma (independent of source `CF`). |
| `BPR` | mm (signed) | Last per-transition residual: `g_buf_pos − switch_pos_mm` measured just before the virtual position snaps to the switch threshold. Non-zero values indicate virtual/physical mismatch at that crossing. |
| `BPD` | mm (signed) | Drift EWMA — exponentially weighted average of `BPR` samples (time constant `BUF_DRIFT_TAU_MS`). A stable non-zero value indicates systematic virtual-position bias. |
| `BPN` | int | Number of zone transitions sampled into `BPD`. Drift correction ramps in until `BPN >= BUF_DRIFT_MIN_SMP`, then can apply at full configured strength away from the opposite wall. |
| `APX` | int | Count of `BUF_ADVANCE` pin entries within the last `ADV_RISK_WINDOW` ms. `EV:SYNC,ADV_RISK_HIGH` fires when this reaches `ADV_RISK_THR`. |
| `RDC` | 0–100 | Drift-correction activity scalar after confidence gating, sample ramp, clamp, and opposite-wall taper. `100` means correction is applying at the configured clamp; values can drop near a physical endstop so correction cannot hide the wall. |

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
| `SYNC` | `AUTO_START\|AUTO_STOP\|ADV_DWELL_STOP\|ADV_DWELL_WARN\|ADV_RISK_HIGH` | Automatic sync state transitions. `ADV_DWELL_STOP` fires when pinned at advance for `SYNC_ADV_STOP_MS`. `ADV_DWELL_WARN` fires when centering drift reaches a significant threshold. `ADV_RISK_HIGH` fires (rate-limited 1/30 s) when advance-pin density in the rolling window reaches `ADV_RISK_THR`. |
| `BUF` | `DRIFT_RESET` | Drift EWMA was reset. Fires when sync stops, `EST_FALLBACK` occurs, or sensor is hot-swapped. Subsequent `BPN` will restart from 0. |
| `BUF` | `EST_LOW_CF\|EST_FALLBACK` | Buffer estimator events. `EST_LOW_CF` fires when confidence drops; `EST_FALLBACK` fires when sigma exceeds the hard cap. |
| `BUF_STAB` | `START\|DONE\|TIMEOUT` | Buffer neutralization started, reached `MID`, or hit its safety timeout. |
| `BS` | Mode-specific snapshot | Periodic buffer/sync status event used during sync and RELOAD follow. |
| `TC:*` | Phase-specific | Toolchange progress events such as `TC:UNLOADING`, `TC:SWAPPING`, `TC:LOADING`, `TC:DONE`, `TC:ERROR`. |
| `RELOAD:*` | Phase-specific | RELOAD progress and fault events such as `RELOAD:SWITCHING`, `RELOAD:JOINING`, `RELOAD:LOADED`, `RELOAD:FAULT`. |
| `CUT:FEEDING` | — | Cutter feed phase started. |

### Fault Recovery
Most faults (`TIMEOUT`, sensor-related faults) are transient and reset on the next command.
**`FAULT:DRY_SPIN`** is sticky: it blocks automatic background tasks (Sync, RELOAD follow) to prevent motor wear. It clears automatically when a new spool is inserted (`IN` sensor triggers) or when a manual load command (`LO:`, `FL:`, etc.) is issued.

### Trailing-Bias Tuning Quickstart (Phase 2.7)
For slow-extrusion soak workflows, use this SET sequence to converge the baseline:
1. `STATUS` to confirm baseline `RT:`, `BPV:` (or `BP:`).
2. `SET:TRAIL_BIAS_FRAC:0.4`
3. `SAVE`
4. Soak 5+ minutes. Verify the post-blend position `BPV:` shifts ≥ 1.5 mm toward trailing without increasing `AD:` counts or causing extruder stall.

---

## Tools

### Calibration Workflow
Phase 2.10 tuning is a calibration-time workflow, not a normal-print service.
The goal is to run several sidecar-tracked calibration prints, analyze the
telemetry, review a patch, bake accepted values into `config.ini`, flash
firmware, and then disconnect the host so NOSF runs standalone.

Before running the first 2.9.9 build, please back up your state file:
```bash
cp ~/nosf-state/buckets-<id>.json ~/nosf-state/buckets-<id>.json.schema2.bak
```

1. Find the Klipper API socket path on the Pi:
   ```bash
   ps -ef | grep '[k]lippy.py'
   ```
   Use the `-a` path. Common modern installs use
   `/home/pi/printer_data/comms/klippy.sock`.
2. Postprocess calibration G-code with a sidecar:
   ```bash
   python3 scripts/gcode_marker.py input.gcode --output input.nosf.gcode \
       --emit sidecar
   ```
   By default, layer changes are recognized (both `;LAYER:<n>` and OrcaSlicer
   `;LAYER_CHANGE` comments). Use `--no-layer-markers` to disable.
3. Capture data using the observe-only tuner in daemon mode, emitting CSV:
   ```bash
   python3 scripts/nosf_live_tuner.py --port /dev/ttyACM0 \
       --machine-id myprinter \
       --klipper-uds /home/pi/printer_data/comms/klippy.sock \
       --sidecar /home/pi/printer_data/gcodes/input.nosf.json \
       --csv-out ~/nosf-runs/run1.csv \
       --observe-daemon &
   ```
   `--klipper-mode auto` is the default. It tries UDS first and falls back to
   marker input if the socket is unavailable. Use `--klipper-mode on` to require
   UDS, or `--klipper-mode off` for shell-marker fallback testing.
4. After at least three calibration runs, analyze the CSV corpus and tuner
   state:
   ```bash
   python3 scripts/nosf_analyze.py \
       --in ~/nosf-runs/run1.csv ~/nosf-runs/run2.csv ~/nosf-runs/run3.csv \
       --state ~/nosf-state/buckets-myprinter.json \
       --out config.patch.ini \
       --acceptance-gate
   ```
   `--mode safe` refuses to emit learned values when the state file has zero
   `LOCKED` buckets. `--mode aggressive` writes a loud pre-lock warning and
   low-confidence estimates. Use `--force` only for explicit bootstrap/debug
   work where you accept pre-lock estimates.
5. Review `config.patch.ini`. It is commented review text only; copy chosen
   values into `config.ini` by hand.
6. Regenerate and flash:
   ```bash
   python3 scripts/gen_config.py
   ninja -C build_local
   bash scripts/flash_nosf.sh
   ```
7. Update the watermark in your state file so drift tracking works:
   ```bash
   python3 scripts/nosf_analyze.py --commit-watermark --state ~/nosf-state/buckets-myprinter.json
   ```

The acceptance gate differentiates between hardware/math failures (**FAIL**) and
stale-configuration warnings (**WARN**). It compares the state-aware 
recommendation path once per "comparable" run. A run is comparable only if it 
contains at least 50 MID rows for at least three contributing buckets. 

- **FAIL (Recommendation Unreliable)**: Triggered by high scatter 
  (sigma_p95 >= 5.0 mm), inconsistent recommendations between runs, 
  very low contributor mass (< 40% after ignoring sparse buckets), or having
  fewer than 2 comparable runs.
- **WARN (Config Stale / Immature)**: Triggered by actual scatter exceeding 
  the current config reference, contributor mass below 65%, low run counts
  (< 3), short print durations (< 30 min total or any run < 10 min), or
  having fewer than 3 LOCKED buckets.

Raw MID-row coverage is reported as a diagnostic warning only. On FAIL, the 
analyzer still writes a patch with `Acceptance gate: FAIL` and prints explicit 
reasons to stderr.

### Live Tuner Modes
`scripts/nosf_live_tuner.py` now defaults to observe-only. It reads status and
marker tags from the Klipper API sidecar path or shell-marker fallback, updates
bucket Kalman state, persists JSON, and emits a review patch at
`--commit-on-idle` or `--commit-on-finish`. It does not send `SET:`,
`SET:LIVE_TUNE_LOCK`, or `SV:` in default mode. Use `--observe-daemon` to run
continuously across prints.

Mode flags:

- default observe mode: no firmware writes, no save.
- `--allow-bias-writes`: debug-only live `SET:TRAIL_BIAS_FRAC` writes.
- `--allow-baseline-writes`: debug-only live `SET:BASELINE_SPS` writes.
- `--klipper-uds PATH`: Klipper API Unix socket path.
- `--klipper-mode auto|on|off`: auto-fallback, require UDS, or force marker fallback.
- `--sidecar PATH`: sidecar JSON generated by `gcode_marker.py --emit sidecar`.
- `--keep-marker-file`: debug/attach-only; preserve existing `--marker-file`
  contents instead of truncating it on startup.
- `--recommend-recheck`: compares current buckets against watermark drift flags.
- `--prune-stale`: removes buckets not seen in >60 days.

Inspect state with:

```bash
python3 scripts/nosf_live_tuner.py --machine-id myprinter --state-info --include-stale
python3 scripts/nosf_live_tuner.py --machine-id myprinter --state-info --csv
python3 scripts/nosf_live_tuner.py --machine-id myprinter --state-info --verbose
```

Bucket lock is cumulative across calibration runs: samples, run count, layer
count, and time spent in `MID` must all pass before a bucket becomes `LOCKED`.
Very low `EST` samples below 100 steps/s and rail-clamped bias buckets are
tracked as diagnostics but excluded from lock/write eligibility.

Phase 2.11 adds residual-aware lock hysteresis. A locked bucket is no longer
unlocked by one moderate sample; the tuner waits for catastrophic mismatch,
sustained outlier streak, or sustained mean drift. Buckets with high residual
scatter remain `STABLE` with `wait=noise sigma/x=...` instead of locking and
chattering. Freshly locked buckets may briefly show `wait=dwell N/20` while the
new unlock detector gathers post-lock evidence.

`--state-info --verbose` appends residual diagnostics to the table:
`sigma2` is the residual-variance EWMA, `streak` is the current moderate
outlier streak, `dwell` is the number of samples observed since the last lock,
and `last_unlock` records `catastrophic`, `streak`, or `drift` when an unlock
actually occurs. With `--csv --verbose`, the same data is appended as
`resid_var_ewma,outlier_streak,locked_sample_count,last_unlock_reason`.

If a serial write fails in an explicit live-write mode, the tuner waits 1 s and
attempts to reopen the same port up to five times. If reconnect fails, it exits
non-zero and leaves the state file unchanged.
