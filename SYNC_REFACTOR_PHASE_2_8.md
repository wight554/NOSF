# Phase 2.8 — Live Tuning (Closed-Loop Online Bucket Learning)

> **Status:** PROPOSED. Companion to `SYNC_REFACTOR_PLAN.md`. Builds on
> Phase 2.7 telemetry pipeline (`MARK:`, `gcode_marker.py`, `nosf_logger.py`),
> Phase 2.5/2.6 estimator surfaces (`CF`, `ES`, `EC`, `BPD`), and the
> `APX` advance-risk channel. Host-only firmware delta (one new `SET` key);
> all learning state lives in Python.
>
> **Why a companion file.** `SYNC_REFACTOR_PLAN.md` is already 1.6k lines.
> Phase 2.8 is a self-contained host-side feature with a tiny firmware
> footprint, so it lives here. The main plan carries a one-paragraph
> pointer back to this file.

## 0. Decision Addendum

These are fixed by the maintainer. Where any section conflicts, this
list wins.

| # | Topic | Decision |
|---|---|---|
| L1 | No firmware PID | Phase 2.7.5 still gates Kp/Kd OFF by default. Phase 2.8 must work without ever enabling them. |
| L2 | Endstop parity | Live tuner must be valid for dual-endstop hardware. No analog-only assumption. |
| L3 | Lean implementation | Pure stdlib + `pyserial` only. No `numpy`, `scipy`, `sklearn`, `pandas`. |
| L4 | One firmware delta | `SET:LIVE_TUNE_LOCK:<0|1>` is the **only** new firmware command. No new persisted fields, no new control law, no settings-version bump. |
| L5 | No SAVE during print | `SAVE` writes flash and can stall sync. Tuner only emits `SAVE` after print idle. |
| L6 | Single-machine scope | State file scoped by `--machine-id`. Cross-machine sharing is out of scope. |
| L7 | Write-after-LOCK | LOCKED buckets are read-only except via operator `--unlock <feature>` or auto-unlock on regime-change variance growth. |

## 1. Findings (post-2.7)

1. **2.7 is open-loop wrt feature buckets.** `BASELINE_SPS` is a single
   global value; `nosf_analyze.py` runs offline only — no in-print
   correction.
2. **Marker stream is one-way today.** Firmware tags `MK:` into status;
   nothing reads it back to drive `SET:` writes.
3. **Operator tuning still hand-driven.** Soak → analyze → manual `SET` →
   reflash. Acceptable for one tune; impossible for per-feature drift
   across regimes.
4. **Confidence and risk surfaces already exist** (`CF`, `ES`, `EC`,
   `APX`, `BPN`, `RDC`). Live tuner can lean on them rather than invent
   a parallel quality model.
5. **Marker tag stream is reliable enough.** Phase 2.7.3a confirmed
   `MK:<seq>:<tag>` survives the round trip from `gcode_marker.py` →
   M118 → Klipper → `nosf_cmd.py MARK:` → firmware → STATUS, with
   bounded latency (<100 ms typical).

## 2. Goals

- Per-bucket `(feature, v_fil_bin)` Kalman estimate of optimal
  `BASELINE_SPS`.
- Per-bucket `TRAIL_BIAS_FRAC` derived from observed `BP` residency vs
  `RT`.
- Updates gated by `CF`, `APX`, `BPN`, and the `EV:` event channel.
- Auto-converge to LOCKED → COMMITTED transition rules with a
  three-print convergence criterion.
- Zero firmware control-law changes; one new SET key only.
- Stability first: no reaction to single-tick noise, retractions, or
  toolchange transitions.

## 3. Architecture

```
                ┌──────────────────────────────────────────┐
                │  scripts/nosf_live_tuner.py              │
                │  ┌────────────────────────────────────┐  │
                │  │ Reader thread:                     │  │
                │  │   STATUS rows  (10 Hz polled)      │  │
                │  │   EV:* lines   (async)             │  │
                │  │   M118 echoes  (async)             │  │
                │  │   →  events queue                  │  │
                │  └────────────────────────────────────┘  │
                │              │                            │
                │              ▼                            │
                │  ┌────────────────────────────────────┐  │
                │  │ Bucketizer:                        │  │
                │  │   key = (feature, v_fil // 5)      │  │
                │  │   feature/v_fil parsed from MK tag │  │
                │  └────────────────────────────────────┘  │
                │              │                            │
                │              ▼                            │
                │  ┌────────────────────────────────────┐  │
                │  │ Per-bucket Kalman filter:          │  │
                │  │   state x_b = optimal BASELINE_SPS │  │
                │  │   meas  z_b = EST observed in MID  │  │
                │  │   R     = f(CF, APX) (low CF or    │  │
                │  │            high APX → ignore)      │  │
                │  └────────────────────────────────────┘  │
                │              │                            │
                │              ▼                            │
                │  ┌────────────────────────────────────┐  │
                │  │ Decision gate:                     │  │
                │  │   bucket STABLE  → emit SET        │  │
                │  │   APX hot        → FREEZE 30 s     │  │
                │  │   ADV_RISK_HIGH  → ROLLBACK active │  │
                │  │   EST_FALLBACK   → FREEZE 30 s     │  │
                │  │   ADV_DWELL_STOP → HALT (manual    │  │
                │  │                   resume only)     │  │
                │  └────────────────────────────────────┘  │
                │              │                            │
                │              ▼                            │
                │  ┌────────────────────────────────────┐  │
                │  │ SET writer:                        │  │
                │  │   ≥ 2 s spacing                    │  │
                │  │   skip when zone ≠ MID             │  │
                │  │   never SAVE during print          │  │
                │  └────────────────────────────────────┘  │
                │              │                            │
                │              ▼                            │
                └──────────────│────────────────────────────┘
                               ▼
                        /dev/ttyACM0  ──►  NOSF firmware
                               ▲
                               │
                ┌──────────────│────────────────────────────┐
                │ Persistent state (~/nosf-state/...json)   │
                │   warm-start buckets across prints        │
                └───────────────────────────────────────────┘
```

## 4. Algorithm

### 4.1 Per-bucket Kalman filter on `BASELINE_SPS`

Scalar state, scalar measurement. Sample-efficient enough that ~200
in-bucket samples (20 s at 10 Hz) reach the LOCKED variance threshold.

```
Definitions (per bucket b):
  x_b           — estimated optimal baseline_sps
  P_b           — estimate variance (sps²)
  Q             — process noise (sps² / s; small — true optimum drifts slowly)
  R_b           — measurement noise (sps²; varies by confidence and risk)

Predict (each tick, dt seconds):
  x_b ← x_b
  P_b ← P_b + Q · dt

Measurement (only when zone == MID, CF ≥ CF_GATE, APX < APX_THR):
  z_b = est_sps_observed                       # EST field, after firmware estimator
  cf  = CF / 100
  R_b = (R_BASE / max(cf, 0.05)) · (1 + APX / APX_THR)

Update:
  K   = P_b / (P_b + R_b)
  x_b ← x_b + K · (z_b − x_b)
  P_b ← (1 − K) · P_b
```

Defaults: `Q = 25`, `R_BASE = 100`, `CF_GATE = 0.6`, `APX_THR = 4`.

The `R` formulation makes low-CF or high-APX measurements effectively
ignored: at `CF = 0.05`, `R = 20·R_BASE`; at `APX = APX_THR`, `R` doubles.
Combined with the hard `CF_GATE` cutoff, the filter never reacts to
single-tick noise or to advance-pin clusters.

### 4.2 `TRAIL_BIAS_FRAC` per bucket — EWMA on residency

```
bp_ewma_b ← 0.95 · bp_ewma_b + 0.05 · BP                  (per measurement)
bias_target_b ← 0.4 + (bp_ewma_b − RT) / threshold_mm
bias_b ← clamp(0.95 · bias_b + 0.05 · bias_target_b, 0.0, 0.7)
```

Smoothed: bias never jumps more than ~0.05 over 100 measurements
(~10 s at 10 Hz). The `0.4` baseline plus the `bp_ewma − RT` correction
push bias up when the arm sits advance-of-target, down when trailing-of-
target. Clamp range matches the firmware tunable range (Phase 2.7.0).

### 4.3 Retraction handling

Retractions cause brief `BUF_TRAILING` excursions. The `zone == MID`
precondition in §4.1 already drops these rows. M118 markers from the
slicer don't carry retraction-only `E` segments (the marker injects on
extruding moves only); v_fil < 1 rows are filtered by the bucketizer.

### 4.4 Toolchange and reload

`TC ≠ IDLE` rows are skipped entirely. The Kalman state holds (predict
continues to widen P, but no measurement update) so the bucket re-enters
TRACKING with conservative variance after the transition.

## 5. Bucket lifecycle

```
            new sample (CF ≥ gate, APX < THR, zone == MID)
TRACKING  ─────────────────────────────────────────►  STABLE  ────►  LOCKED
   ▲                                                    │              │
   │  variance grows ≥ 2× last LOCKED P                 │  60 s        │  print idle
   │  (regime change auto-unlock)                       │  no new SET  │  + FINISH marker
   │                                                    │  no events   │  or 30 s no E
   │                                                    │              ▼
   └────────────────────────────────────────────────────┘         COMMITTED
                                                                  (config patch
                                                                   emitted)
```

Bucket is **STABLE** when all hold:

- `P_b < P_STABLE_THR` (default 100 sps²)
- `n_b ≥ N_MIN_SAMPLES` (default 200)
- `|x_b − last_sent_x_b| < SET_DEADBAND_SPS` (default 30 sps)
- `|bias_b − last_sent_bias_b| < BIAS_DEADBAND` (default 0.03)
- No `EV:SYNC,ADV_RISK_HIGH` or `EV:BUF,EST_FALLBACK` in last 30 s

A STABLE bucket transitions to **LOCKED** after `BUCKET_LOCK_S = 60`
seconds with no further SET writes. LOCKED is read-only until either
operator `--unlock <feature>` or sample variance grows ≥ 2× last LOCKED
P (regime-change auto-unlock).

## 6. Safety interlocks

| Trigger | Action | Duration |
|---|---|---|
| `EV:SYNC,ADV_RISK_HIGH` | FREEZE all writes; ROLLBACK active bucket to `last_set_x`; widen P to `1e4` | 30 s |
| `EV:SYNC,ADV_DWELL_STOP` | HALT entire tuner; require operator `RESUME` | indefinite |
| `EV:BUF,EST_FALLBACK` | FREEZE all writes (state was already bad — rollback would loop) | 30 s |
| `APX > APX_THR` (read from STATUS) | FREEZE | 10 s sliding |
| `CF < 0.4` for > 2 s | Predict-only (no Kalman update) | until CF ≥ gate |
| `TC ≠ IDLE` | Pause entirely | until idle |
| `> 3` SETs in rolling 30 s | Auto rate-limit; warn | rolling 30 s |
| Firmware reset detected (status seq jumps backward, or `BP/RT/RE` reset to defaults) | Reload state file; clear in-memory locks | one-shot |

Rollback semantics: only **active** bucket rolls back. Other buckets are
fine and their last SET is still the firmware live value.

## 7. Communication protocol

Hard rules:

- One SET per ≥ 2 s.
- Never SET during `BUF_ADVANCE` (control law fighting empty buffer).
- Group order: `BASELINE_SPS` first; on next STATUS verify acknowledged;
  then `TRAIL_BIAS_FRAC` if delta exceeds deadband.
- **No `SAVE` mid-print.** SAVE writes flash → potential sync stall.
  SAVE only on idle.
- 10 s heartbeat: `STATUS` polled even with fresh data, to detect link
  stalls.
- All host writes prepended with `SET:LIVE_TUNE_LOCK:1` block (see §8)
  so manual operator typos can't corrupt the host model during a run.

Sequence per stable transition:

```
T+0     bucket goes STABLE
        if zone == MID and (now − last_set_t) > 2 s:
            send  SET:BASELINE_SPS:<int(x_b)>
T+200ms read STATUS; verify SC: reflects new baseline
T+1s    if |bias_b − last_sent_bias| > 0.03:
            send  SET:TRAIL_BIAS_FRAC:<bias_b>
T+30s+  bucket holds STABLE → LOCKED → persist JSON
T+...   on print idle: aggregate LOCKED buckets, decide global default,
        send  SAVE
```

## 8. Firmware delta (minimal)

**Option A (chosen)**: keep firmware globals; tuner re-aims them as the
active bucket changes. Per-bucket state is host-only. Trade-off: brief
settling on bucket transition (~200 ms) as the global tracks the new
bucket value. Acceptable; firmware estimator already smooths.

**Option B (deferred to 2.8.x)**: per-tag baseline lookup table inside
firmware. Heavier; needs persistent storage extension and lookup at
sync_tick(). Skip until Option A's transient is observed insufficient.

**New SET key (Option A v1)**: `SET:LIVE_TUNE_LOCK:<0|1>`.

- `1` = firmware ignores `SET` writes to `BASELINE_SPS`,
  `TRAIL_BIAS_FRAC`, `MID_CREEP_*`, `VAR_BLEND_*`. Defensive — prevents
  manual SET during tuner run from racing with host model.
- `0` = normal SET handling.
- Reset to `0` on every boot. **Not persisted.**
- Returns `ER:LIVE_TUNE_LOCKED` for blocked SET writes (operator can
  see they were ignored).

Implementation: file-static `bool g_live_tune_lock` in `protocol.c`;
checked in those specific SET handlers; ~30 LOC. **No `settings_t`
change. No `SETTINGS_VERSION` bump.**

## 9. Data persistence

State file: `~/nosf-state/buckets-<machine-id>.json`.

```json
{
  "_schema": 1,
  "<machine-id>": {
    "PERIMETER_v40": {
      "x": 1820.5,
      "P": 78.2,
      "n": 4321,
      "bias": 0.42,
      "bp_ewma": -3.55,
      "locked": true,
      "last_set_x": 1820,
      "last_set_bias": 0.42,
      "first_seen": "2026-05-09T14:02:11",
      "last_seen":  "2026-05-09T14:38:47"
    }
  }
}
```

Atomic write: `tmp + os.replace`. Schema versioning via top-level
`"_schema"` field; tuner refuses to load mismatched schema and starts
empty (with stderr warning).

### 9.1 Live-patch → permanent default flow

```
phase            firmware state                     persistence
─────────────────────────────────────────────────────────────────
TRACKING         globals at config.ini defaults     (none)
STABLE           globals shifting on SET writes     in-memory bucket
LOCKED           globals at LOCKED bucket value     JSON file
COMMITTED        SAVEd to flash; config.ini.patch   JSON file +
                 emitted to disk for review         /tmp/nosf-patch.ini
```

### 9.2 Three-print convergence rule

- Run 1: tuner emits N SETs, locks K buckets.
- Run 2: cold-start with JSON; locked buckets re-LOCK on first matching
  tag with no measurement updates needed.
- Run 3: same as run 2, no SETs emitted.
- → tune converged. Operator runs
  `nosf_live_tuner --emit-config-patch` once; reviews
  `/tmp/nosf-patch.ini`; merges into repo `config.ini`; commits.

## 10. Lean Python implementation sketch

stdlib + pyserial only. ~250 LOC target. Full file ships as
`scripts/nosf_live_tuner.py` in 2.8.1.

```python
#!/usr/bin/env python3
"""nosf_live_tuner.py — closed-loop live tuner for NOSF Phase 2.8.

Pure stdlib + pyserial. No numpy/scipy/sklearn.
"""
import argparse, json, os, queue, re, sys, threading, time
from collections import deque
from dataclasses import dataclass
from typing import Optional
import serial

CF_GATE          = 0.6
APX_THR          = 4
P_STABLE_THR     = 100.0
SET_DEADBAND_SPS = 30
BIAS_DEADBAND    = 0.03
MIN_SET_SPACING  = 2.0
N_MIN_SAMPLES    = 200
BUCKET_LOCK_S    = 60
Q_PROCESS        = 25.0
R_BASE           = 100.0
SCHEMA_VERSION   = 1

STATUS_FIELD_RE = re.compile(r'(?P<key>[A-Z]+):(?P<val>-?\d+(?:\.\d+)?|[A-Z]+)')
EVENT_RE        = re.compile(r'^EV:([A-Z_]+),([A-Z_]+)')
M118_RE         = re.compile(
    r'NOSF_TUNE:(?P<feature>[^:]+):V(?P<vfil>[^:]+):W(?P<w>[^:]+):H(?P<h>[^:]+)'
)


@dataclass
class Bucket:
    label: str
    x: float = 1600.0
    P: float = 1e6
    n: int = 0
    bias: float = 0.4
    bp_ewma: float = 0.0
    state: str = 'TRACKING'           # TRACKING | STABLE | LOCKED
    last_set_x: float = 0.0
    last_set_bias: float = 0.4
    last_set_ms: float = 0.0
    stable_since_ms: float = 0.0


def kf_predict_update(b: Bucket, z: float, cf: float, apx: int, dt_s: float) -> None:
    R = (R_BASE / max(cf, 0.05)) * (1.0 + apx / float(APX_THR))
    b.P += Q_PROCESS * dt_s
    K = b.P / (b.P + R)
    b.x += K * (z - b.x)
    b.P *= (1.0 - K)
    b.n += 1


class Tuner:
    def __init__(self, ser: serial.Serial, state_path: str, machine_id: str):
        self.ser = ser
        self.state_path = state_path
        self.machine_id = machine_id
        self.buckets: dict[str, Bucket] = {}
        self.active_label: Optional[str] = None
        self.last_set_t = 0.0
        self.frozen_until = 0.0
        self.halted = False
        self.last_status_t = 0.0
        self.last_feature_tag = ''
        self.last_v_fil = 0
        self.recent_sets: deque = deque(maxlen=4)
        self.lock_engaged = False
        self._load_state()

    # --- persistence ---------------------------------------------------------

    def _load_state(self):
        if not os.path.exists(self.state_path):
            return
        try:
            data = json.load(open(self.state_path))
        except Exception:
            return
        if data.get('_schema') != SCHEMA_VERSION:
            sys.stderr.write('[tuner] state file schema mismatch — ignoring\n')
            return
        for k, v in data.get(self.machine_id, {}).items():
            self.buckets[k] = Bucket(
                label=k, x=v['x'], P=v['P'], n=v['n'],
                bias=v['bias'], bp_ewma=v.get('bp_ewma', 0.0),
                state='LOCKED' if v.get('locked') else 'TRACKING',
                last_set_x=v.get('last_set_x', v['x']),
                last_set_bias=v.get('last_set_bias', v['bias']),
            )

    def _persist(self):
        try:
            data = json.load(open(self.state_path)) if os.path.exists(self.state_path) else {}
        except Exception:
            data = {}
        data['_schema'] = SCHEMA_VERSION
        data.setdefault(self.machine_id, {})
        for k, b in self.buckets.items():
            data[self.machine_id][k] = {
                'x': b.x, 'P': b.P, 'n': b.n, 'bias': b.bias, 'bp_ewma': b.bp_ewma,
                'locked': b.state == 'LOCKED',
                'last_set_x': b.last_set_x, 'last_set_bias': b.last_set_bias,
            }
        tmp = self.state_path + '.tmp'
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.state_path)

    # --- protocol ------------------------------------------------------------

    def _send(self, line: str):
        if self.halted:
            return
        self.ser.write((line + '\n').encode())
        self.recent_sets.append(time.monotonic())

    def _engage_lock(self):
        if not self.lock_engaged:
            self._send('SET:LIVE_TUNE_LOCK:1')
            self.lock_engaged = True

    # --- input handlers ------------------------------------------------------

    def on_m118(self, raw: str):
        m = M118_RE.search(raw)
        if not m:
            return
        self.last_feature_tag = m.group('feature').strip()
        try:
            self.last_v_fil = int(round(float(m.group('vfil'))))
        except ValueError:
            self.last_v_fil = 0

    def on_event(self, ev: str):
        now = time.monotonic()
        if ev == 'EV:SYNC,ADV_RISK_HIGH':
            self.frozen_until = now + 30
            self._rollback_active()
        elif ev == 'EV:SYNC,ADV_DWELL_STOP':
            self.halted = True
            sys.stderr.write('[tuner] HALT: ADV_DWELL_STOP — operator resume required\n')
        elif ev == 'EV:BUF,EST_FALLBACK':
            self.frozen_until = now + 30

    def on_status(self, line: str):
        if self.halted:
            return
        now = time.monotonic()
        fields = dict(STATUS_FIELD_RE.findall(line))
        if not fields:
            return

        # rate-limit guard
        if len(self.recent_sets) == self.recent_sets.maxlen:
            if now - self.recent_sets[0] < 30:
                return  # too many sets in last 30 s — skip

        zone = fields.get('BUF')
        cf   = float(fields.get('CF', 0)) / 100.0
        apx  = int(fields.get('APX', 0))
        est  = float(fields.get('EST', 0))
        bp   = float(fields.get('BP', 0)) / 100.0
        rt   = float(fields.get('RT', 0)) / 100.0
        tc   = fields.get('TC', '')

        if tc and tc != 'IDLE':
            return
        if now < self.frozen_until:
            return
        if apx >= APX_THR:
            self.frozen_until = now + 10
            return
        if zone != 'MID':
            return
        if cf < CF_GATE:
            return
        if not self.last_feature_tag or self.last_v_fil <= 0:
            return

        label = f'{self.last_feature_tag}_v{(self.last_v_fil // 5) * 5}'
        b = self.buckets.setdefault(label, Bucket(label=label, x=est, P=1e6))
        self.active_label = label

        if self.last_status_t == 0:
            self.last_status_t = now
            return
        dt = now - self.last_status_t
        self.last_status_t = now

        if b.state == 'LOCKED':
            innov = abs(est - b.x)
            if innov * innov > 4 * (b.P + R_BASE):
                b.state = 'TRACKING'
                b.P = max(b.P, 1e4)
            return

        kf_predict_update(b, est, cf, apx, dt)
        b.bp_ewma = 0.95 * b.bp_ewma + 0.05 * bp if b.n > 1 else bp
        threshold_mm = 7.8
        bias_target = 0.4 + (b.bp_ewma - rt) / threshold_mm
        b.bias = max(0.0, min(0.7, 0.95 * b.bias + 0.05 * bias_target))

        self._maybe_emit_set(b, now)
        self._maybe_lock(b, now)

    # --- decisions -----------------------------------------------------------

    def _maybe_emit_set(self, b: Bucket, now: float):
        if (now - self.last_set_t) < MIN_SET_SPACING:
            return
        if b.n < N_MIN_SAMPLES:
            return
        if b.P > 4 * P_STABLE_THR:
            return
        if abs(b.x - b.last_set_x) >= SET_DEADBAND_SPS:
            self._engage_lock()
            self._send(f'SET:BASELINE_SPS:{int(round(b.x))}')
            b.last_set_x = b.x
            self.last_set_t = now
            return
        if abs(b.bias - b.last_set_bias) >= BIAS_DEADBAND:
            self._engage_lock()
            self._send(f'SET:TRAIL_BIAS_FRAC:{b.bias:.3f}')
            b.last_set_bias = b.bias
            self.last_set_t = now

    def _maybe_lock(self, b: Bucket, now: float):
        if b.state == 'LOCKED':
            return
        stable = (b.P < P_STABLE_THR
                  and b.n >= N_MIN_SAMPLES
                  and abs(b.x - b.last_set_x) < SET_DEADBAND_SPS
                  and abs(b.bias - b.last_set_bias) < BIAS_DEADBAND)
        if stable:
            if b.state != 'STABLE':
                b.state = 'STABLE'
                b.stable_since_ms = now * 1000
            elif (now * 1000 - b.stable_since_ms) > BUCKET_LOCK_S * 1000:
                b.state = 'LOCKED'
                self._persist()
        else:
            b.state = 'TRACKING'
            b.stable_since_ms = 0.0

    def _rollback_active(self):
        if not self.active_label:
            return
        b = self.buckets.get(self.active_label)
        if not b or b.state == 'LOCKED':
            return
        b.P = max(b.P, 1e4)
        if b.last_set_x:
            self._send(f'SET:BASELINE_SPS:{int(b.last_set_x)}')


# --- reader / dispatcher loop -----------------------------------------------

def reader(ser: serial.Serial, q: queue.Queue):
    buf = b''
    while True:
        chunk = ser.read(256)
        if not chunk:
            continue
        buf += chunk
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            q.put(line.decode('utf-8', errors='replace').strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', required=True)
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--state', default=os.path.expanduser('~/nosf-state/buckets.json'))
    ap.add_argument('--machine-id', default='default')
    ap.add_argument('--poll-hz', type=float, default=10.0)
    ap.add_argument('--emit-config-patch', metavar='PATH',
                    help='Read state file, emit /tmp/nosf-patch.ini-style file, exit')
    ap.add_argument('--unlock', metavar='FEATURE',
                    help='Unlock a LOCKED bucket and exit')
    ap.add_argument('--reset-runtime', action='store_true',
                    help='Send LIVE_TUNE_LOCK:0 and LOAD; clear in-memory state; exit')
    args = ap.parse_args()

    if args.emit_config_patch:
        emit_patch(args.state, args.machine_id, args.emit_config_patch)
        return

    ser = serial.Serial(args.port, args.baud, timeout=0.05)

    if args.reset_runtime:
        ser.write(b'SET:LIVE_TUNE_LOCK:0\n')
        time.sleep(0.1)
        ser.write(b'LOAD\n')
        return

    if args.unlock:
        unlock_bucket(args.state, args.machine_id, args.unlock)
        return

    q = queue.Queue(maxsize=1024)
    threading.Thread(target=reader, args=(ser, q), daemon=True).start()

    tuner = Tuner(ser, args.state, args.machine_id)
    poll_interval = 1.0 / args.poll_hz
    next_poll = time.monotonic()

    try:
        while True:
            now = time.monotonic()
            if now >= next_poll:
                ser.write(b'STATUS\n')
                next_poll = now + poll_interval

            try:
                line = q.get(timeout=0.05)
            except queue.Empty:
                continue

            if 'NOSF_TUNE:' in line:
                tuner.on_m118(line)
            elif EVENT_RE.match(line):
                tuner.on_event(line.split()[0])
            elif 'BUF:' in line and 'BP:' in line:
                tuner.on_status(line)
    except KeyboardInterrupt:
        tuner._persist()
        sys.stderr.write('[tuner] persisted state on exit\n')


def emit_patch(state_path: str, machine_id: str, out_path: str):
    """Produce a config.ini-style patch from LOCKED buckets."""
    if not os.path.exists(state_path):
        sys.stderr.write('[tuner] no state file\n')
        sys.exit(1)
    data = json.load(open(state_path))
    buckets = data.get(machine_id, {})
    locked = {k: v for k, v in buckets.items() if v.get('locked')}
    if not locked:
        sys.stderr.write('[tuner] no locked buckets to commit\n')
        sys.exit(1)
    # Global recommendation: weighted mean of LOCKED bucket x by sample count.
    total_n = sum(v['n'] for v in locked.values()) or 1
    baseline = sum(v['x'] * v['n'] for v in locked.values()) / total_n
    bias     = sum(v['bias'] * v['n'] for v in locked.values()) / total_n
    with open(out_path, 'w') as f:
        f.write('# nosf_live_tuner.py emitted patch\n')
        f.write('# Apply to config.ini, then re-run gen_config.py + ninja.\n')
        f.write(f'# locked buckets: {len(locked)}\n')
        for k, v in sorted(locked.items()):
            f.write(f'#   {k}: x={v["x"]:.0f} bias={v["bias"]:.3f} n={v["n"]}\n')
        f.write(f'baseline_rate_sps: {int(round(baseline))}\n')
        f.write(f'sync_trailing_bias_frac: {bias:.3f}\n')


def unlock_bucket(state_path: str, machine_id: str, feature: str):
    if not os.path.exists(state_path):
        return
    data = json.load(open(state_path))
    if machine_id in data and feature in data[machine_id]:
        data[machine_id][feature]['locked'] = False
        tmp = state_path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, state_path)
        sys.stderr.write(f'[tuner] unlocked {feature}\n')


if __name__ == '__main__':
    main()
```

## 11. Acceptance criteria

- Tuner attached to running print emits zero SETs in first 30 s
  (warm-up; `n_b < N_MIN_SAMPLES`).
- Per-bucket JSON persists across host restarts; cold-start cycle hits
  LOCKED on first matching tag without further SETs.
- One full slow-extrusion soak (≥ 30 min): all observed buckets reach
  LOCKED; total SETs sent < 20.
- Forced advance-pin cluster (synthetic extruder accel test): tuner
  FREEZES within 200 ms of `EV:SYNC,ADV_RISK_HIGH`; ROLLS BACK active
  bucket within 1 s.
- `EV:BUF,EST_FALLBACK` injection: tuner pauses, no SETs in next 30 s,
  no false rollback.
- Three converged prints with identical slicer profile: zero new SETs
  across runs 2–3.
- `nosf_live_tuner --emit-config-patch <path>` produces a
  `config.ini`-merge-ready file with `baseline_rate_sps` and
  `sync_trailing_bias_frac` keys; values match weighted-mean of LOCKED
  buckets.
- Tuner survives USB disconnect: reconnects within 5 s, state file
  unchanged, in-memory buckets preserved.
- Building firmware with the 2.8.0 `LIVE_TUNE_LOCK` change does not
  require a `SETTINGS_VERSION` bump (verify with `git diff
  firmware/src/settings_store.c` showing no version constant change).

## 12. Out of scope (Phase 2.8.x follow-ups)

- Per-bucket firmware-side baseline (Option B).
- Online learning of `MID_CREEP_RATE`, `VAR_BLEND_FRAC` — those are
  config-time choices, not feedback-control.
- Cross-machine bucket sharing — single-machine only via
  `--machine-id`.
- ML model fitting (lightgbm, MLP) — Phase 2.9 if data justifies; OLS
  already in `nosf_analyze.py`.
- Autonomous operator unlock — operator must explicitly run
  `--unlock <feature>`; no auto-unlock except regime-change variance
  growth.

## 13. Milestones

```
[ ] 2.8.0  add SET:LIVE_TUNE_LOCK:<0|1>           protocol.c, MANUAL.md
[ ] 2.8.1  scripts/nosf_live_tuner.py             reader + KF + SET writer
[ ] 2.8.2  state-file persistence + warm start    JSON schema, atomic writes
[ ] 2.8.3  safety interlocks + rate limit         APX/EV handling, rollback
[ ] 2.8.4  end-of-print SAVE + config patch       --emit-config-patch flag
[ ] 2.8.5  docs sync                              MANUAL.md, KLIPPER.md, README.md
```

Each milestone = one commit + push per repo rule #3. No firmware build
required after 2.8.0.

## 14. Rollback path

- Single-shot disable: `Ctrl-C` the tuner. Firmware retains last live
  values; operator runs `SET:LIVE_TUNE_LOCK:0` manually (or restarts
  the firmware — lock is non-persistent).
- Wipe live patch: `nosf_live_tuner --reset-runtime` sends
  `SET:LIVE_TUNE_LOCK:0`, then `LOAD` (re-applies persisted defaults),
  and clears in-memory buckets.
- Wipe persistent state: `rm ~/nosf-state/buckets-<machine-id>.json`.
  Next run cold-starts.
- Revert firmware: `git revert` the 2.8.0 commit. Removes the new SET
  key; tuner falls back to "best effort" mode (no host-side lock; manual
  SETs from operator can race the model, mitigated by the 2 s spacing
  rule).

## 15. Open questions (deferred)

- **Q-2.8-A.** Should the bucketizer take `layer_height` and `width` as
  additional dimensions (`gcode_marker.py` already injects them in the
  M118 payload)? More dimensions → smaller bucket fill rate. Defer
  until first soak shows whether per-(feature, v_fil) buckets are
  sufficient.
- **Q-2.8-B.** Should `nosf_logger.py` and `nosf_live_tuner.py` share a
  reader process? Two opens of the same TTY conflict. Either compose
  (logger embeds tuner) or pick one. Defer to first ship — operators
  can choose.
- **Q-2.8-C.** Klipper webhook subscription vs TTY echo for M118. Same
  question raised in Phase 2.7.3; resolution applies symmetrically.
- **Q-2.8-D.** Should the Kalman state include extruder-velocity
  derivative (so the filter anticipates accel/decel)? Adds a second
  state. Defer; Phase 2.7's existing estimator already smooths.

---

**Cross-references:**
- `SYNC_REFACTOR_PLAN.md` — main plan; Phase 2.7 telemetry pipeline
  documented there.
- `scripts/gcode_marker.py` (re-added in 2.7.3b) — injects M118 markers.
- `scripts/nosf_logger.py` (added in 2.7.3c) — passive CSV capture; can
  run in parallel with the live tuner if they share the TTY (see
  Q-2.8-B).
- `scripts/nosf_analyze.py` (added in 2.7.4) — offline regression;
  `nosf_live_tuner.py --emit-config-patch` produces output of the same
  shape so operators can pipe either tool into the same merge step.
