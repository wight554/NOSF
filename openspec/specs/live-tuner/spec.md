# Live Tuner Specification

## Purpose
Tuner contract for Phase 2.8 and Phase 2.11 (chatter resistance).

## Requirements

### REQ: Per-Feature Velocity Buckets
Tuner aggregate telemetry into feature + velocity buckets (rate + bias).
- **SCEN: Marker Active**: tune samples + marker -> update rounded velocity bucket.
- **AND** update rate, uncertainty, bias, N, layers, runs, motion time.

### REQ: Machine-Scoped Persistence
Persist bucket state in machine-scoped JSON.
- **SCEN: Tuner Restart**: load machine state -> extend evidence (no zero-start).

### REQ: Observe-Only Default
NO firmware writes without explicit flags.
- **SCEN: No Write Flags**: record + report buckets. NO `SET`, NO `SV`.

### REQ: Review-Only Workflow
Prefer analyzer review patches over blind tuning.
- **SCEN: Cal Data Ready**: analyzer emits patch for review -> operator flash.

### REQ: Diagnostics
Tuner MUST explain bucket states (TRACKING, STABLE, LOCKED).
- **SCEN: state-info**: output state + counts + wait reason (e.g. noise).

## Historical Rationale and Constants

### Live-Tune Lock
`LIVE_TUNE_LOCK:1` prevents host-firmware races. LOCKED = accepts writes; UNLOCKED = defaults.

### Chatter Resistance (Phase 2.11)
- **Noise Gate (`sigma/x`)**: threshold pass required to lock (default 0.25).
- **3-Channel Unlock**:
    - **Catastrophic**: residual > 10.0 * sigma.
    - **Streak**: 5 residuals > 3.0 * sigma.
    - **Drift**: EWMA drift > 4.0 * sigma.
- **Lock Dwell**: 100 samples min after warmup.

### Rollback
- **`--reset-runtime`**: `LOCK:0` + `LOAD`. Clear memory, re-apply defaults.
- **Schema 4 Migration**: One-way. Adds residual stats for 3-channel unlock.
