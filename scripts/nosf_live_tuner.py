#!/usr/bin/env python3
"""nosf_live_tuner.py - observe-only calibration tuner for NOSF Phase 2.9.

Pure stdlib plus pyserial. No numpy, scipy, sklearn, or pandas.

Usage examples:
    python3 scripts/nosf_live_tuner.py --port /dev/ttyACM0 --machine-id myprinter
    python3 scripts/nosf_live_tuner.py --state ~/nosf-state/buckets-myprinter.json \
        --machine-id myprinter --emit-config-patch /tmp/nosf-patch.ini
    python3 scripts/nosf_live_tuner.py --port /dev/ttyACM0 --reset-runtime

The live loop reads NOSF status and marker lines, learns per
(feature, v_fil_bin) buckets, and persists calibration state. Default
mode is observe-only: no SET writes and no SAVE. Research/debug modes:
    --allow-bias-writes      allow guarded SET:TRAIL_BIAS_FRAC writes
    --allow-baseline-writes  allow guarded SET:BASELINE_SPS writes
    --commit-flash           enable both write flags and send SV: at
                             end-of-print commit time

Runtime BASELINE_SPS writes are disabled by default because the status
EST field is a live flow estimate, not a safe global baseline target;
use --allow-baseline-writes only for controlled experiments. If a
serial write fails, the tuner waits 1 s and attempts to reopen the
configured port up to five times. If reconnect fails, it exits non-zero
without modifying the state file.

Config patch emission uses recency-weighted bucket means:
    weight = n / (1 + age_seconds / 86400)
where age_seconds is computed from each bucket's last_seen wall-clock
timestamp. Recent locked buckets therefore carry full sample weight while
older buckets decay gently over multi-day tuning cycles.
"""

import argparse
import json
import math
import os
import platform
import queue
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, Optional

try:
    import serial
except ImportError:
    class _MissingSerial:
        class SerialException(Exception):
            pass

        @staticmethod
        def Serial(*_args, **_kwargs):
            raise _MissingSerial.SerialException("pyserial is not installed")

    serial = _MissingSerial()
    PY_SERIAL_AVAILABLE = False
else:
    PY_SERIAL_AVAILABLE = True

try:
    from klipper_motion_tracker import (
        DEFAULT_UDS_PATH,
        SUBSCRIBE_OBJECTS,
        KlipperApiClient,
        SegmentMatcher,
        matcher_state_from_status,
        merge_status_update,
    )
except ImportError:
    DEFAULT_UDS_PATH = "/tmp/klippy_uds"
    SUBSCRIBE_OBJECTS = {}
    KlipperApiClient = None
    SegmentMatcher = None
    matcher_state_from_status = None
    merge_status_update = None


CF_GATE = 0.6
APX_THR = 4
P_STABLE_THR = 100.0
SET_DEADBAND_SPS = 30.0
BIAS_DEADBAND = 0.03
BIAS_SAFE_MIN = 0.05
BIAS_SAFE_MAX = 0.65
MIN_LEARN_EST_SPS = 100.0
MIN_SET_SPACING = 2.0
N_MIN_SAMPLES_CUMULATIVE = 200
N_MIN_RUNS = 2
N_MIN_LAYERS = 3
MIN_MID_TIME_S = 60.0
N_SINGLE_PRINT_SAMPLES = 500
N_SINGLE_PRINT_LAYERS = 5
MIN_PRINT_MID_S = 300.0
RECHECK_NEW_LOCKED = 3
RECHECK_SAMPLE_PCT = 25.0
RECHECK_AGE_DAYS = 30
STALE_AGE_DAYS = 60
Q_PROCESS = 25.0
R_BASE = 100.0
A_R = 0.05  # residual EWMA alpha
A_V = 0.05  # residual variance EWMA alpha
N_WARMUP_FOR_NOISE = 50  # samples before noise gate can affect locking
NOISE_RATIO_THR = 0.25  # lock only when residual sigma is <= 25% of learned x
V_NOISE_FLOOR = 100.0  # sps^2 floor; sigma never evaluates below 10 sps
NOISE_GATE_MIN_X = MIN_LEARN_EST_SPS  # avoid ratio blow-up near zero-flow buckets
K_CATA = 8.0  # catastrophic residual threshold in sigma
K_STREAK_SIGMA = 3.0  # moderate outlier threshold in sigma
N_STREAK = 5  # moderate outliers before streak unlock
K_DRIFT = 4.0  # drift threshold in EWMA standard errors
EWMA_EFFECTIVE_N = 19.0  # ~= 1 / A_R - 1
MIN_LOCK_DWELL = 20  # locked samples before moderate unlock channels
M_DRIFT_DWELL = 30  # locked samples before drift channel
P_UNLOCK_RESET = 400.0  # reset P after unlock without cold-starting bucket
SCHEMA_VERSION = 4
DEFAULT_STATE_DIR = os.path.expanduser("~/nosf-state")
PATCH_KEYS = [
    "baseline_rate",
    "sync_trailing_bias_frac",
    "mid_creep_timeout_ms",
    "mid_creep_rate_sps_per_s",
    "mid_creep_cap_frac",
    "buf_variance_blend_frac",
    "buf_variance_blend_ref_mm",
]

STATUS_FIELD_RE = re.compile(r"(?P<key>[A-Z0-9]+):(?P<val>-?\d+(?:\.\d+)?|[A-Z_]+|[^,]*)")
EVENT_RE = re.compile(r"^EV:([A-Z_]+),([A-Z_]+)")
MARK_RE = re.compile(r"MK:(?P<seq>\d+):(?P<tag>[^,]*)")
M118_RE = re.compile(
    r"NOSF_TUNE:(?P<feature>[^:]+):V(?P<vfil>[^:]+):W(?P<w>[^:]+):H(?P<h>[^:\s]+)"
)
COMPACT_MARK_RE = re.compile(r"NT:(?P<feature>[^:]+):V(?P<vfil>[^:\s]+)")
LAYER_MARK_RE = re.compile(r"(?:NT:LAYER:|NOSF_TUNE:LAYER:)(?P<layer>\d+)")


@dataclass
class Bucket:
    label: str
    x: float = 1600.0
    P: float = 1e6
    n: int = 0
    bias: float = 0.4
    bp_ewma: float = 0.0
    state: str = "TRACKING"
    last_set_x: float = 0.0
    last_set_bias: float = 0.4
    first_seen: float = 0.0
    last_seen: float = 0.0
    stable_since: float = 0.0
    locked: bool = False
    last_debug_t: float = 0.0
    last_debug_state: str = ""
    runs_seen: int = 0
    layers_seen: int = 0
    cumulative_mid_s: float = 0.0
    low_flow_skip_count: int = 0
    rail_skip_count: int = 0
    rollback_count: int = 0
    first_seen_run: str = ""
    resid_ewma: float = 0.0
    resid_abs_ewma: float = 0.0
    resid_var_ewma: float = R_BASE
    outlier_streak: int = 0
    locked_sample_count: int = 0
    locked_since_run_seq: int = 0
    last_unlock_reason: str = ""
    last_unlock_at: float = 0.0


def default_state_path(machine_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", machine_id.strip() or "default")
    return os.path.join(DEFAULT_STATE_DIR, f"buckets-{safe}.json")


def parse_status(line: str) -> Dict[str, str]:
    if line.startswith("OK:"):
        line = line[3:]
    return dict(STATUS_FIELD_RE.findall(line))


def cf_value(raw: str) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return v / 100.0 if v > 1.0 else v


def bucket_label(feature: str, v_fil: float) -> str:
    return f"{feature}_v{int(round(v_fil / 25.0)) * 25}"


def kf_predict_update(bucket: Bucket, z: float, cf: float, apx: int, dt_s: float) -> None:
    bucket.P += Q_PROCESS * max(dt_s, 0.0)
    R = (R_BASE / max(cf, 0.05)) * (1.0 + apx / float(APX_THR))
    K = bucket.P / (bucket.P + R)
    bucket.x += K * (z - bucket.x)
    bucket.P *= 1.0 - K
    bucket.n += 1
    update_residual_stats(bucket, z)


def update_residual_stats(bucket: Bucket, z: float) -> None:
    resid = z - bucket.x
    bucket.resid_ewma = (1.0 - A_R) * bucket.resid_ewma + A_R * resid
    bucket.resid_abs_ewma = (1.0 - A_R) * bucket.resid_abs_ewma + A_R * abs(resid)
    bucket.resid_var_ewma = (1.0 - A_V) * bucket.resid_var_ewma + A_V * (resid * resid)


def resid_sigma(bucket: Bucket) -> float:
    return math.sqrt(max(bucket.resid_var_ewma, R_BASE))


def noise_ratio(bucket: Bucket) -> float:
    sigma = math.sqrt(max(bucket.resid_var_ewma, V_NOISE_FLOOR))
    x = max(bucket.x, NOISE_GATE_MIN_X)
    return sigma / x


def noise_ok(bucket: Bucket) -> bool:
    if bucket.n < N_WARMUP_FOR_NOISE:
        return True
    return noise_ratio(bucket) <= NOISE_RATIO_THR


class CsvEmitter:
    def __init__(self, path: str):
        self.path = path
        self.fields = [
            "wall_ts", "run_seq", "layer", "feature", "v_fil",
            "BL", "BP", "BPV", "EST", "RT", "AD", "APX", "CF", "TC", "BUF", "MK_seq"
        ]
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w") as fh:
                fh.write(",".join(self.fields) + "\n")
        self.fh = open(path, "a")

    def emit(self, row: dict) -> None:
        line = ",".join(str(row.get(f, "")) for f in self.fields)
        self.fh.write(line + "\n")
        self.fh.flush()

    def close(self) -> None:
        self.fh.close()


def _migrate_1_to_2(data: dict) -> dict:
    data["_schema"] = 2
    return data

def _migrate_2_to_3(data: dict) -> dict:
    for machine_id, machine_data in data.items():
        if machine_id.startswith("_") or not isinstance(machine_data, dict):
            continue
        meta = machine_data.setdefault("_meta", {})
        meta.setdefault("last_commit_values", {
            key: {"value": None, "applied_at": None, "source": "default"}
            for key in PATCH_KEYS
        })
        meta.setdefault("last_commit_run_seq", 0)
        meta.setdefault("last_commit_sample_total", 0)
    data["_schema"] = 3
    return data

def _migrate_3_to_4(data: dict) -> dict:
    for machine_id, machine_data in data.items():
        if machine_id.startswith("_") or not isinstance(machine_data, dict):
            continue
        for label, raw in machine_data.items():
            if label.startswith("_") or not isinstance(raw, dict):
                continue
            raw.setdefault("resid_ewma", 0.0)
            raw.setdefault("resid_abs_ewma", 0.0)
            raw.setdefault("resid_var_ewma", R_BASE)
            raw.setdefault("outlier_streak", 0)
            raw.setdefault("locked_sample_count", 0)
            raw.setdefault("locked_since_run_seq", 0)
            raw.setdefault("last_unlock_reason", "")
            raw.setdefault("last_unlock_at", 0.0)
    data["_schema"] = 4
    return data

_MIGRATIONS = {
    1: _migrate_1_to_2,
    2: _migrate_2_to_3,
    3: _migrate_3_to_4,
}

def migrate_state_data(data: dict) -> dict:
    schema = data.get("_schema")
    if not isinstance(schema, int):
        raise ValueError(f"state file schema mismatch: got {schema!r}, expected int")
    if schema > SCHEMA_VERSION:
        raise ValueError(f"state file schema {schema} is newer than supported {SCHEMA_VERSION}")
    while schema < SCHEMA_VERSION:
        if schema not in _MIGRATIONS:
            raise ValueError(f"missing migration step from schema {schema}")
        data = _MIGRATIONS[schema](data)
        schema = data["_schema"]
    return data


class Tuner:
    def __init__(
        self,
        ser,
        state_path: str,
        machine_id: str,
        port: Optional[str] = None,
        baud: int = 115200,
        now_fn=time.monotonic,
        wall_fn=time.time,
    ):
        self.ser = ser
        self.state_path = state_path
        self.machine_id = machine_id
        self.port = port
        self.baud = baud
        self.now_fn = now_fn
        self.wall_fn = wall_fn
        self.buckets: Dict[str, Bucket] = {}
        self.active_label: Optional[str] = None
        self.last_status_t = 0.0
        self.last_set_t = 0.0
        self.frozen_until = 0.0
        self.halted = False
        self.lock_engaged = False
        self.last_feature = ""
        self.last_v_fil = 0.0
        self.last_marker_t = 0.0
        self.last_marker_seq = -1
        self.idle_since = 0.0
        self.seen_print_activity = False
        self.finish_seen = False
        self.start_seen = False
        self.debug = False
        self.run_seq = 0
        self.current_run_id = ""
        self.current_layer = ""
        self.total_print_mid_s = 0.0
        self._run_seen_labels = set()
        self._seen_layer_keys = set()
        self.recent_sets = deque()
        self.last_rate_limit_warn_t = -999.0
        self.allow_bias_writes = False
        self.allow_baseline_writes = False
        self.last_baseline_skip_warn_t = -999.0
        self.last_bias_skip_warn_t = -999.0
        self.last_low_flow_warn_t = -999.0
        self.last_bias_rail_warn_t = -999.0
        self.progress_interval = 10.0
        self.csv_emitter = None
        self._load_state()

    def _load_state(self) -> None:
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[tuner] warning: ignoring unreadable state file: {exc}", file=sys.stderr)
            return
        try:
            data = migrate_state_data(data)
        except ValueError as exc:
            print(f"[tuner] {exc}", file=sys.stderr)
            sys.exit(1)
        for label, raw in data.get(self.machine_id, {}).items():
            b = Bucket(
                label=label,
                x=float(raw.get("x", 1600.0)),
                P=float(raw.get("P", 1e6)),
                n=int(raw.get("n", 0)),
                bias=float(raw.get("bias", 0.4)),
                bp_ewma=float(raw.get("bp_ewma", 0.0)),
                state="LOCKED" if raw.get("locked") else raw.get("state", "TRACKING"),
                last_set_x=float(raw.get("last_set_x", raw.get("x", 0.0))),
                last_set_bias=float(raw.get("last_set_bias", raw.get("bias", 0.4))),
                first_seen=float(raw.get("first_seen", 0.0)),
                last_seen=float(raw.get("last_seen", 0.0)),
                locked=bool(raw.get("locked", False)),
                runs_seen=int(raw.get("runs_seen", 0)),
                layers_seen=int(raw.get("layers_seen", 0)),
                cumulative_mid_s=float(raw.get("cumulative_mid_s", 0.0)),
                low_flow_skip_count=int(raw.get("low_flow_skip_count", 0)),
                rail_skip_count=int(raw.get("rail_skip_count", 0)),
                rollback_count=int(raw.get("rollback_count", 0)),
                first_seen_run=str(raw.get("first_seen_run", "")),
                resid_ewma=float(raw.get("resid_ewma", 0.0)),
                resid_abs_ewma=float(raw.get("resid_abs_ewma", 0.0)),
                resid_var_ewma=float(raw.get("resid_var_ewma", R_BASE)),
                outlier_streak=int(raw.get("outlier_streak", 0)),
                locked_sample_count=int(raw.get("locked_sample_count", 0)),
                locked_since_run_seq=int(raw.get("locked_since_run_seq", 0)),
                last_unlock_reason=str(raw.get("last_unlock_reason", "")),
                last_unlock_at=float(raw.get("last_unlock_at", 0.0)),
            )
            self.buckets[label] = b

    def _persist(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.state_path))
        os.makedirs(parent, exist_ok=True)
        try:
            with open(self.state_path) as fh:
                data = json.load(fh)
            data = migrate_state_data(data)
        except (OSError, json.JSONDecodeError, ValueError):
            data = {"_schema": SCHEMA_VERSION}
            
        machine_meta = data.get(self.machine_id, {}).get("_meta")
        data["_schema"] = SCHEMA_VERSION
        machine_data = {
            label: {
                "x": b.x,
                "P": b.P,
                "n": b.n,
                "bias": b.bias,
                "bp_ewma": b.bp_ewma,
                "state": b.state,
                "locked": b.state == "LOCKED" or b.locked,
                "last_set_x": b.last_set_x,
                "last_set_bias": b.last_set_bias,
                "first_seen": b.first_seen,
                "last_seen": b.last_seen,
                "runs_seen": b.runs_seen,
                "layers_seen": b.layers_seen,
                "cumulative_mid_s": b.cumulative_mid_s,
                "low_flow_skip_count": b.low_flow_skip_count,
                "rail_skip_count": b.rail_skip_count,
                "rollback_count": b.rollback_count,
                "first_seen_run": b.first_seen_run,
                "resid_ewma": b.resid_ewma,
                "resid_abs_ewma": b.resid_abs_ewma,
                "resid_var_ewma": b.resid_var_ewma,
                "outlier_streak": b.outlier_streak,
                "locked_sample_count": b.locked_sample_count,
                "locked_since_run_seq": b.locked_since_run_seq,
                "last_unlock_reason": b.last_unlock_reason,
                "last_unlock_at": b.last_unlock_at,
            }
            for label, b in sorted(self.buckets.items())
        }
        if machine_meta is not None:
            machine_data["_meta"] = machine_meta
        data[self.machine_id] = machine_data
        
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.state_path)

    def _reopen_serial(self) -> bool:
        if not self.port:
            return False
        for attempt in range(1, 6):
            time.sleep(1.0)
            try:
                self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
                print(f"[tuner] serial reconnected on attempt {attempt}", file=sys.stderr)
                return True
            except serial.SerialException as exc:
                print(f"[tuner] reconnect attempt {attempt} failed: {exc}", file=sys.stderr)
        return False

    def _send(self, line: str, count_rate: bool = True) -> None:
        if self.halted:
            return
        try:
            self.ser.write((line + "\n").encode())
        except serial.SerialException:
            if not self._reopen_serial():
                raise SystemExit(2)
            self.ser.write((line + "\n").encode())
        if count_rate and line.startswith("SET:"):
            self.recent_sets.append(self.now_fn())

    def _engage_lock(self) -> None:
        if not self.lock_engaged:
            self._send("SET:LIVE_TUNE_LOCK:1", count_rate=False)
            self.lock_engaged = True

    def _rate_limited(self, now: float) -> bool:
        while self.recent_sets and now - self.recent_sets[0] > 30.0:
            self.recent_sets.popleft()
        if len(self.recent_sets) >= 3:
            if now - self.last_rate_limit_warn_t >= 10.0:
                print("[tuner] write rate limited: >3 SETs in 30 s", file=sys.stderr)
                self.last_rate_limit_warn_t = now
            return True
        return False

    def on_m118(self, raw: str) -> None:
        if "NT:START" in raw or raw.strip() == "NT:START":
            self.finish_seen = False
            self.seen_print_activity = False
            self.idle_since = 0.0
            self.start_seen = True
            self.run_seq += 1
            self.current_run_id = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.wall_fn()))
            self.current_layer = ""
            self.total_print_mid_s = 0.0
            self._run_seen_labels.clear()
            self._seen_layer_keys.clear()
            if self.debug:
                print("[tuner] marker START — resetting print state", file=sys.stderr)
            return
        layer = LAYER_MARK_RE.search(raw)
        if layer:
            self.seen_print_activity = True
            self.last_marker_t = self.now_fn()
            self.idle_since = 0.0
            self.current_layer = layer.group("layer")
            if self.active_label:
                b = self.buckets.get(self.active_label)
                if b:
                    self._credit_layer(self.active_label, b)
            if self.debug:
                print(f"[tuner] marker LAYER {layer.group('layer')}", file=sys.stderr)
            return
        if raw.strip() == "FINISH" or "NOSF_TUNE:FINISH" in raw:
            self.seen_print_activity = True
            self.finish_seen = True
            self.last_marker_t = self.now_fn()
            self.idle_since = 0.0
            if self.debug:
                print("[tuner] marker FINISH", file=sys.stderr)
            return
        m = M118_RE.search(raw)
        if not m:
            m = COMPACT_MARK_RE.search(raw)
        if not m:
            return
        self.seen_print_activity = True
        self.last_marker_t = self.now_fn()
        self.idle_since = 0.0
        self.last_feature = m.group("feature").strip()
        if self.last_feature == "FINISH":
            self.finish_seen = True
            if self.debug:
                print("[tuner] marker FINISH", file=sys.stderr)
            return
        try:
            self.last_v_fil = float(m.group("vfil"))
        except ValueError:
            self.last_v_fil = 0.0
        if self.debug:
            print(f"[tuner] marker {self.last_feature}_v{int(round(self.last_v_fil / 5.0)) * 5}", file=sys.stderr)

    def _credit_layer(self, label: str, b: Bucket) -> None:
        if not self.current_layer:
            return
        key = (self.run_seq, label, self.current_layer)
        if key in self._seen_layer_keys:
            return
        b.layers_seen += 1
        self._seen_layer_keys.add(key)

    def on_status_marker(self, raw: str) -> None:
        m = MARK_RE.search(raw)
        if not m:
            return
        seq = int(m.group("seq"))
        if seq == self.last_marker_seq:
            return
        self.last_marker_seq = seq
        self.on_m118(m.group("tag"))

    def on_event(self, line: str) -> None:
        now = self.now_fn()
        if line.startswith("EV:SYNC,ADV_RISK_HIGH"):
            self.frozen_until = now + 30.0
            self._rollback_active()
        elif line.startswith("EV:SYNC,ADV_DWELL_STOP"):
            self.halted = True
            print("[tuner] HALT: ADV_DWELL_STOP; operator resume required", file=sys.stderr)
        elif line.startswith("EV:BUF,EST_FALLBACK"):
            self.frozen_until = now + 30.0

    def on_status(self, line: str) -> None:
        if self.halted:
            return
        self.on_status_marker(line)
        now = self.now_fn()
        fields = parse_status(line)
        if not fields:
            return

        if self.csv_emitter:
            row = fields.copy()
            row["wall_ts"] = self.wall_fn()
            row["run_seq"] = self.run_seq
            row["layer"] = self.current_layer
            row["feature"] = self.last_feature
            row["v_fil"] = self.last_v_fil
            row["MK_seq"] = self.last_marker_seq if self.last_marker_seq >= 0 else ""
            if row.get("BPV"):
                try:
                    row["BPV"] = float(row["BPV"]) / 100.0
                except ValueError:
                    pass
            self.csv_emitter.emit(row)

        tc = fields.get("TC", "")
        if tc and tc != "IDLE":
            self.idle_since = 0.0
            return
        if tc == "IDLE" and self.idle_since == 0.0:
            self.idle_since = now
        apx = int(float(fields.get("APX", "0") or 0))
        if apx >= APX_THR:
            self.frozen_until = now + 10.0
            return
        if now < self.frozen_until:
            return
        if fields.get("BUF") != "MID":
            return
        cf = cf_value(fields.get("CF", "0"))
        if cf < CF_GATE:
            return
        if not self.last_feature or self.last_v_fil < 1.0:
            return
        try:
            est = float(fields.get("EST", "0"))
            bp = float(fields.get("BP", "0"))
            rt = float(fields.get("RT", "0"))
        except ValueError:
            return

        label = bucket_label(self.last_feature, self.last_v_fil)
        if est < MIN_LEARN_EST_SPS:
            existing = self.buckets.get(label)
            if existing:
                existing.low_flow_skip_count += 1
            if self.debug and now - self.last_low_flow_warn_t >= max(1.0, self.progress_interval):
                print(
                    f"[tuner] skip low-flow {label} est={est:.1f} < {MIN_LEARN_EST_SPS:.0f}",
                    file=sys.stderr,
                )
                self.last_low_flow_warn_t = now
            return

        wall_now = self.wall_fn()
        b = self.buckets.setdefault(label, Bucket(label=label, x=est, first_seen=wall_now))
        self.active_label = label
        if self.current_run_id and label not in self._run_seen_labels:
            b.runs_seen += 1
            if not b.first_seen_run:
                b.first_seen_run = self.current_run_id
            self._run_seen_labels.add(label)
        self._credit_layer(label, b)
        b.last_seen = wall_now
        if b.first_seen == 0.0:
            b.first_seen = wall_now

        if self.last_status_t == 0.0:
            self.last_status_t = now
            return
        dt_s = now - self.last_status_t
        self.last_status_t = now
        if dt_s > 0.0:
            b.cumulative_mid_s += dt_s
            self.total_print_mid_s += dt_s

        if b.state == "LOCKED" or b.locked:
            resid = est - b.x
            update_residual_stats(b, est)
            self._credit_locked_sample(b)
            reason = self._evaluate_unlock(b, resid)
            if reason:
                self._apply_unlock(b, reason, est)
            self._debug_bucket_progress(b, now, est, bp, cf, apx)
            return

        kf_predict_update(b, est, cf, apx, dt_s)
        b.bp_ewma = 0.95 * b.bp_ewma + 0.05 * bp if b.n > 1 else bp
        bias_target = 0.4 + (b.bp_ewma - rt) / 7.8
        b.bias = max(BIAS_SAFE_MIN, min(BIAS_SAFE_MAX, 0.95 * b.bias + 0.05 * bias_target))
        self._maybe_emit_set(b, now)
        self._maybe_lock(b, now)
        self._debug_bucket_progress(b, now, est, bp, cf, apx)

    def _bucket_wait_reason(self, b: Bucket, now: float) -> str:
        return bucket_wait_reason(b, self.total_print_mid_s)

    def _debug_bucket_progress(self, b: Bucket, now: float, est: float, bp: float, cf: float, apx: int) -> None:
        if not self.debug or self.progress_interval <= 0.0:
            return
        state_changed = b.state != b.last_debug_state
        if not state_changed and now - b.last_debug_t < self.progress_interval:
            return
        b.last_debug_t = now
        b.last_debug_state = b.state
        wait = self._bucket_wait_reason(b, now)
        print(
            f"[tuner] bucket {b.label} n={b.n} P={b.P:.1f} "
            f"x={b.x:.0f} est={est:.0f} bias={b.bias:.3f} "
            f"bp={bp:.2f} cf={cf:.2f} apx={apx} state={b.state} wait={wait}",
            file=sys.stderr,
        )

    def _maybe_emit_set(self, b: Bucket, now: float) -> None:
        if now - self.last_set_t < MIN_SET_SPACING:
            return
        if b.n < N_MIN_SAMPLES_CUMULATIVE:
            return
        if b.P > 4.0 * P_STABLE_THR:
            return
        if abs(b.x - b.last_set_x) >= SET_DEADBAND_SPS:
            if not self.allow_baseline_writes:
                if self.debug and now - self.last_baseline_skip_warn_t >= 30.0:
                    print(
                        f"[tuner] baseline write skipped for {b.label}; pass --allow-baseline-writes to enable",
                        file=sys.stderr,
                    )
                    self.last_baseline_skip_warn_t = now
            else:
                if self._rate_limited(now):
                    return
                self._engage_lock()
                self._send(f"SET:BASELINE_SPS:{int(round(b.x))}")
                b.last_set_x = b.x
                self.last_set_t = now
                return
        if abs(b.bias - b.last_set_bias) >= BIAS_DEADBAND:
            if not self.allow_bias_writes:
                if self.debug and now - self.last_bias_skip_warn_t >= 30.0:
                    print(
                        f"[tuner] bias write skipped for {b.label}; pass --allow-bias-writes to enable",
                        file=sys.stderr,
                    )
                    self.last_bias_skip_warn_t = now
                return
            if not self._bias_in_safe_range(b.bias):
                b.rail_skip_count += 1
                if self.debug and now - self.last_bias_rail_warn_t >= max(1.0, self.progress_interval):
                    print(
                        f"[tuner] bias write skipped for {b.label}; rail guard bias={b.bias:.3f}",
                        file=sys.stderr,
                    )
                    self.last_bias_rail_warn_t = now
                return
            if self._rate_limited(now):
                return
            self._engage_lock()
            self._send(f"SET:TRAIL_BIAS_FRAC:{b.bias:.3f}")
            b.last_set_bias = b.bias
            self.last_set_t = now

    def _maybe_lock(self, b: Bucket, now: float) -> None:
        if b.state == "LOCKED" or b.locked:
            return
        stable = (
            b.P < P_STABLE_THR
            and self._bias_in_safe_range(b.bias)
        )
        if not stable:
            b.state = "TRACKING"
            b.stable_since = 0.0
            return
        ready_A = (
            b.n >= N_MIN_SAMPLES_CUMULATIVE
            and b.runs_seen >= N_MIN_RUNS
            and b.layers_seen >= N_MIN_LAYERS
            and b.cumulative_mid_s >= MIN_MID_TIME_S
        )
        ready_B = (
            b.n >= N_SINGLE_PRINT_SAMPLES
            and b.layers_seen >= N_SINGLE_PRINT_LAYERS
            and b.cumulative_mid_s >= MIN_MID_TIME_S
            and self.total_print_mid_s >= MIN_PRINT_MID_S
        )
        if b.state == "STABLE" and (ready_A or ready_B):
            if not self._noise_ok_for_lock(b):
                b.state = "STABLE"
                if b.stable_since == 0.0:
                    b.stable_since = now
                return
            b.state = "LOCKED"
            b.locked = True
            b.locked_sample_count = 0
            b.locked_since_run_seq = self.run_seq
            b.outlier_streak = 0
            self._persist()
            return
        b.state = "STABLE"
        if b.stable_since == 0.0:
            b.stable_since = now

    def _noise_ok_for_lock(self, b: Bucket) -> bool:
        return noise_ok(b)

    def _resid_sigma(self, b: Bucket) -> float:
        return resid_sigma(b)

    def _evaluate_unlock(self, b: Bucket, resid: float) -> str:
        sigma = self._resid_sigma(b)
        if abs(resid) > K_CATA * sigma:
            return "catastrophic"
        if abs(resid) > K_STREAK_SIGMA * sigma:
            b.outlier_streak += 1
        else:
            b.outlier_streak = max(0, b.outlier_streak - 1)
        if b.locked_sample_count < MIN_LOCK_DWELL:
            return ""
        if b.outlier_streak >= N_STREAK:
            return "streak"
        if b.locked_sample_count >= M_DRIFT_DWELL:
            drift_sigma = sigma / math.sqrt(EWMA_EFFECTIVE_N)
            if drift_sigma > 0.0 and abs(b.resid_ewma) > K_DRIFT * drift_sigma:
                return "drift"
        return ""

    def _apply_unlock(self, b: Bucket, reason: str, est: float) -> None:
        if self.debug:
            print(
                f"[tuner] bucket {b.label} unlock {reason} "
                f"est={est:.0f} x={b.x:.0f} P={b.P:.1f}",
                file=sys.stderr,
            )
        b.state = "TRACKING"
        b.locked = False
        b.P = max(b.P, P_UNLOCK_RESET)
        b.outlier_streak = 0
        b.resid_var_ewma = max(b.resid_var_ewma, R_BASE * 2.0)
        b.last_unlock_reason = reason
        b.last_unlock_at = self.wall_fn()
        b.locked_sample_count = 0

    def _credit_locked_sample(self, b: Bucket) -> None:
        b.locked_sample_count += 1

    def _rollback_active(self) -> None:
        if not self.active_label:
            return
        b = self.buckets.get(self.active_label)
        if not b or b.locked or b.state == "LOCKED":
            return
        b.P = max(b.P, 1e4)
        b.rollback_count += 1
        if self.allow_baseline_writes and b.last_set_x:
            self._send(f"SET:BASELINE_SPS:{int(round(b.last_set_x))}")

    @staticmethod
    def _bias_in_safe_range(bias: float) -> bool:
        return BIAS_SAFE_MIN <= bias <= BIAS_SAFE_MAX

    def print_idle_ready(self, idle_s: float = 30.0) -> bool:
        if not self.seen_print_activity:
            return False
        if not self.finish_seen:
            return False
        now = self.now_fn()
        if self.idle_since == 0.0 or now - self.idle_since < idle_s:
            return False
        if self.last_marker_t and now - self.last_marker_t < idle_s:
            return False
        return True

    def locked_bucket_count(self) -> int:
        return sum(1 for b in self.buckets.values() if b.locked or b.state == "LOCKED")


def reader(ser, lines: queue.Queue) -> None:
    buf = b""
    while True:
        try:
            chunk = ser.read(256)
        except serial.SerialException as exc:
            lines.put(f"__SERIAL_ERROR__:{exc}")
            return
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            lines.put(line.decode("utf-8", errors="replace").strip())


def lock_path_for_state(state_path: str) -> str:
    parent = os.path.dirname(os.path.abspath(state_path))
    stem = os.path.basename(state_path)
    return os.path.join(parent, f".{stem}.lock")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system().lower() == "linux" and os.path.exists("/proc"):
        return os.path.exists(f"/proc/{pid}")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_state_lock(state_path: str) -> str:
    lock_path = lock_path_for_state(state_path)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    if os.path.exists(lock_path):
        try:
            with open(lock_path) as fh:
                pid = int((fh.read().strip() or "0").split()[0])
        except (OSError, ValueError, IndexError):
            pid = 0
        if pid_alive(pid):
            print(f"[tuner] refusing to start: state lock held by PID {pid} ({lock_path})", file=sys.stderr)
            sys.exit(1)
        print(f"[tuner] removing stale state lock: {lock_path}", file=sys.stderr)
    tmp = lock_path + f".{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        fh.write(f"{os.getpid()}\n")
    os.replace(tmp, lock_path)
    return lock_path


def release_state_lock(lock_path: Optional[str]) -> None:
    if not lock_path:
        return
    try:
        with open(lock_path) as fh:
            pid = int((fh.read().strip() or "0").split()[0])
    except (OSError, ValueError, IndexError):
        pid = 0
    if pid == os.getpid():
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def state_label(raw: dict) -> str:
    if raw.get("locked") or raw.get("state") == "LOCKED":
        return "LOCKED"
    if raw.get("state"):
        return str(raw.get("state"))
    try:
        if float(raw.get("P", 1e6)) < P_STABLE_THR and int(raw.get("n", 0)) >= N_MIN_SAMPLES_CUMULATIVE:
            return "STABLE"
    except (TypeError, ValueError):
        pass
    return "TRACKING"


def bucket_from_raw(label: str, raw: dict) -> Bucket:
    return Bucket(
        label=label,
        x=float(raw.get("x", 1600.0)),
        P=float(raw.get("P", 1e6)),
        n=int(raw.get("n", 0)),
        bias=float(raw.get("bias", 0.4)),
        bp_ewma=float(raw.get("bp_ewma", 0.0)),
        state="LOCKED" if raw.get("locked") else raw.get("state", "TRACKING"),
        last_set_x=float(raw.get("last_set_x", raw.get("x", 0.0))),
        last_set_bias=float(raw.get("last_set_bias", raw.get("bias", 0.4))),
        first_seen=float(raw.get("first_seen", 0.0)),
        last_seen=float(raw.get("last_seen", 0.0)),
        locked=bool(raw.get("locked", False)),
        runs_seen=int(raw.get("runs_seen", 0)),
        layers_seen=int(raw.get("layers_seen", 0)),
        cumulative_mid_s=float(raw.get("cumulative_mid_s", 0.0)),
        low_flow_skip_count=int(raw.get("low_flow_skip_count", 0)),
        rail_skip_count=int(raw.get("rail_skip_count", 0)),
        rollback_count=int(raw.get("rollback_count", 0)),
        first_seen_run=str(raw.get("first_seen_run", "")),
        resid_ewma=float(raw.get("resid_ewma", 0.0)),
        resid_abs_ewma=float(raw.get("resid_abs_ewma", 0.0)),
        resid_var_ewma=float(raw.get("resid_var_ewma", R_BASE)),
        outlier_streak=int(raw.get("outlier_streak", 0)),
        locked_sample_count=int(raw.get("locked_sample_count", 0)),
        locked_since_run_seq=int(raw.get("locked_since_run_seq", 0)),
        last_unlock_reason=str(raw.get("last_unlock_reason", "")),
        last_unlock_at=float(raw.get("last_unlock_at", 0.0)),
    )


def bucket_wait_reason(b: Bucket, total_print_mid_s: float = 0.0) -> str:
    if b.state == "LOCKED" or b.locked:
        if b.locked_sample_count < MIN_LOCK_DWELL:
            return f"dwell {b.locked_sample_count}/{MIN_LOCK_DWELL}"
        return "locked"
    if b.P >= P_STABLE_THR:
        return f"variance P>={P_STABLE_THR:.0f}"
    if not (BIAS_SAFE_MIN <= b.bias <= BIAS_SAFE_MAX):
        return "bias rail guard"
    if b.n >= N_WARMUP_FOR_NOISE:
        ratio = noise_ratio(b)
        if ratio > NOISE_RATIO_THR:
            return f"noise sigma/x={ratio:.2f}"
    
    reasons_A = []
    if b.n < N_MIN_SAMPLES_CUMULATIVE: reasons_A.append(f"samples {b.n}/{N_MIN_SAMPLES_CUMULATIVE}")
    if b.runs_seen < N_MIN_RUNS: reasons_A.append(f"runs {b.runs_seen}/{N_MIN_RUNS}")
    if b.layers_seen < N_MIN_LAYERS: reasons_A.append(f"layers {b.layers_seen}/{N_MIN_LAYERS}")
    if b.cumulative_mid_s < MIN_MID_TIME_S: reasons_A.append(f"mid_time {b.cumulative_mid_s:.0f}/{MIN_MID_TIME_S:.0f}s")
    
    reasons_B = []
    if b.n < N_SINGLE_PRINT_SAMPLES: reasons_B.append(f"samples {b.n}/{N_SINGLE_PRINT_SAMPLES}")
    if b.layers_seen < N_SINGLE_PRINT_LAYERS: reasons_B.append(f"layers {b.layers_seen}/{N_SINGLE_PRINT_LAYERS}")
    if b.cumulative_mid_s < MIN_MID_TIME_S: reasons_B.append(f"mid_time {b.cumulative_mid_s:.0f}/{MIN_MID_TIME_S:.0f}s")
    if total_print_mid_s < MIN_PRINT_MID_S: reasons_B.append(f"total_mid {total_print_mid_s:.0f}/{MIN_PRINT_MID_S:.0f}s")
    
    if not reasons_A or not reasons_B:
        return "stable"
    
    return reasons_A[0] if len(reasons_A) <= len(reasons_B) else reasons_B[0]


def print_state_info(
    state_path: str,
    machine_id: str,
    csv_mode: bool = False,
    include_stale: bool = False,
    verbose: bool = False,
) -> None:
    if not os.path.exists(state_path):
        print(f"[tuner] no state file: {state_path}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(state_path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[tuner] state file unreadable: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        data = migrate_state_data(data)
    except ValueError:
        print(
            f"[tuner] state file schema mismatch: got {data.get('_schema')!r}, expected {SCHEMA_VERSION}",
            file=sys.stderr,
        )
        sys.exit(1)
    buckets = data.get(machine_id, {})
    now = time.time()
    cutoff = now - STALE_AGE_DAYS * 86400
    if csv_mode:
        header = "feature_v_fil,x,P,n,bias,state,runs,layers,mid_s,last_seen_age_s,wait"
        if verbose:
            header += ",resid_var_ewma,outlier_streak,locked_sample_count,last_unlock_reason"
        print(header)
    else:
        header = (
            f"{'feature_v_fil':<24} {'x':>7} {'P':>8} {'n':>6} {'bias':>7} "
            f"{'state':>11} {'runs':>5} {'layers':>6} {'mid_s':>7} {'age_s':>7} wait"
        )
        if verbose:
            header += f" {'sigma2':>8} {'streak':>6} {'dwell':>6} last_unlock"
        print(header)
    locked = 0
    total_n = 0
    total_mid_s = 0.0
    for label, raw in sorted(buckets.items()):
        if label.startswith("_"): continue
        b = bucket_from_raw(label, raw)
        age_s = max(0.0, now - b.last_seen) if b.last_seen else 0.0
        if b.last_seen < cutoff and not include_stale:
            continue
        
        st = state_label(raw)
        if b.last_seen < cutoff:
            st += " STALE"
            
        if "LOCKED" in st:
            locked += 1
        n = int(raw.get("n", 0))
        total_n += n
        total_mid_s += b.cumulative_mid_s
        wait = bucket_wait_reason(b)
        if csv_mode:
            row = (
                f"{label},{b.x:.0f},{b.P:.1f},{b.n},{b.bias:.3f},{st},"
                f"{b.runs_seen},{b.layers_seen},{b.cumulative_mid_s:.1f},{age_s:.1f},{wait}"
            )
            if verbose:
                row += (
                    f",{b.resid_var_ewma:.1f},{b.outlier_streak},"
                    f"{b.locked_sample_count},{b.last_unlock_reason}"
                )
            print(row)
        else:
            row = (
                f"{label:<24} {b.x:>7.0f} {b.P:>8.1f} {n:>6d} "
                f"{b.bias:>7.3f} {st:>11} {b.runs_seen:>5d} "
                f"{b.layers_seen:>6d} {b.cumulative_mid_s:>7.1f} "
                f"{age_s:>7.0f} {wait}"
            )
            if verbose:
                row += (
                    f" {b.resid_var_ewma:>8.1f} {b.outlier_streak:>6d} "
                    f"{b.locked_sample_count:>6d} {b.last_unlock_reason}"
                )
            print(row)
    if not csv_mode:
        print(f"TOTAL: {len(buckets)} buckets, {locked} locked, {total_n} samples, {total_mid_s:.1f}s MID")


def emit_patch(state_path: str, machine_id: str, out_path: str) -> None:
    if not os.path.exists(state_path):
        print("[tuner] no state file", file=sys.stderr)
        sys.exit(1)
    with open(state_path) as fh:
        data = json.load(fh)
    try:
        data = migrate_state_data(data)
    except ValueError as exc:
        print(f"[tuner] {exc}", file=sys.stderr)
        sys.exit(1)
    locked = {
        k: v for k, v in data.get(machine_id, {}).items()
        if v.get("locked") or v.get("state") == "LOCKED"
    }
    if not locked:
        print("[tuner] no locked buckets to commit", file=sys.stderr)
        sys.exit(1)
    now = time.time()
    weights = {}
    for label, raw in locked.items():
        n = max(1, int(raw.get("n", 0)))
        try:
            last_seen = float(raw.get("last_seen", now))
        except (TypeError, ValueError):
            last_seen = now
        age_s = max(0.0, now - last_seen)
        weights[label] = n / (1.0 + age_s / 86400.0)
    total_w = sum(weights.values()) or 1.0
    baseline = sum(float(v.get("x", 0.0)) * weights[k] for k, v in locked.items()) / total_w
    bias = sum(float(v.get("bias", 0.4)) * weights[k] for k, v in locked.items()) / total_w
    suggestions = {
        "baseline_rate": (
            f"{int(round(baseline))}",
            "LOW",
            "single-source tuner estimate; verify with nosf_analyze.py",
        ),
        "sync_trailing_bias_frac": (
            f"{bias:.3f}",
            "LOW",
            f"{len(locked)} locked buckets, recency weighted",
        ),
    }
    with open(out_path, "w") as fh:
        fh.write("# nosf_live_tuner.py emitted patch\n")
        fh.write(f"# Source: tuner state, {sum(int(v.get('n', 0)) for v in locked.values())} samples, {len(locked)} LOCKED buckets\n")
        fh.write("# Acceptance gate: NOT RUN\n")
        fh.write("# WARNING: do not blindly apply; review against config.ini first.\n")
        fh.write("# recency weight: n / (1 + age_seconds / 86400)\n")
        for label, raw in sorted(locked.items()):
            fh.write(
                f"#   {label}: x={float(raw.get('x', 0.0)):.0f} "
                f"bias={float(raw.get('bias', 0.4)):.3f} "
                f"n={int(raw.get('n', 0))} weight={weights[label]:.1f}\n"
            )
        fh.write("\n[nosf_review]\n")
        fh.write("# Each line: current_value -> suggested_value (confidence)\n")
        for key in PATCH_KEYS:
            suggested, conf, detail = suggestions.get(
                key,
                ("no-suggestion", "DEFAULT", "requires multi-run nosf_analyze.py"),
            )
            fh.write(f"# {key:<28} {'review':>7} -> {suggested:<13} ({conf}, {detail})\n")
        fh.write("\n")
        fh.write("# To apply, copy reviewed values into config.ini, then run:\n")
        fh.write("#   python3 scripts/gen_config.py\n")
        fh.write("#   ninja -C build_local\n")
        fh.write("#   bash scripts/flash_nosf.sh\n")
    print(f"[tuner] wrote patch: {out_path}", file=sys.stderr)


def finish_commit(tuner: Tuner, args, out_path: str = "/tmp/nosf-patch.ini") -> None:
    tuner._persist()
    if tuner.locked_bucket_count() == 0:
        print("[tuner] FINISH seen, but no LOCKED buckets yet; persisted tracking state without SV", file=sys.stderr)
        raise SystemExit(1)
    emit_patch(args.state, args.machine_id, out_path)
    print(f"[tuner] commit patch: {out_path}", file=sys.stderr)


def unlock_bucket(state_path: str, machine_id: str, feature: str) -> None:
    if not os.path.exists(state_path):
        print("[tuner] no state file", file=sys.stderr)
        return
    with open(state_path) as fh:
        data = json.load(fh)
    buckets = data.get(machine_id, {})
    changed = False
    for label, raw in buckets.items():
        if label == feature or label.startswith(feature + "_"):
            raw["locked"] = False
            raw["state"] = "TRACKING"
            changed = True
    if changed:
        tmp = state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, state_path)
        print(f"[tuner] unlocked {feature}", file=sys.stderr)


def open_serial(port: str, baud: int):
    if not PY_SERIAL_AVAILABLE:
        print("nosf_live_tuner: 'pyserial' not installed. Run: pip install pyserial", file=sys.stderr)
        sys.exit(1)
    try:
        return serial.Serial(port, baud, timeout=0.05)
    except serial.SerialException as exc:
        print(f"nosf_live_tuner: could not open {port}: {exc}", file=sys.stderr)
        sys.exit(1)


def setup_klipper_motion(args):
    if getattr(args, "klipper_mode", "auto") == "off":
        return None, None, {}, False
    if KlipperApiClient is None or SegmentMatcher is None:
        msg = "Klipper motion tracker module is unavailable"
        if args.klipper_mode == "on":
            print(f"nosf_live_tuner: {msg}", file=sys.stderr)
            sys.exit(1)
        print(f"[tuner] warning: {msg}; falling back to marker input", file=sys.stderr)
        return None, None, {}, False

    try:
        matcher = SegmentMatcher(getattr(args, "sidecar", None))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if args.klipper_mode == "on":
            print(f"nosf_live_tuner: sidecar refused: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"[tuner] warning: sidecar refused: {exc}; falling back to marker input", file=sys.stderr)
        return None, None, {}, False
    client = KlipperApiClient(args.klipper_uds)
    try:
        client.connect()
        client.subscribe(SUBSCRIBE_OBJECTS)
    except Exception as exc:
        client.close()
        if args.klipper_mode == "on":
            print(f"nosf_live_tuner: Klipper API required but unavailable at {args.klipper_uds}: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"[tuner] warning: Klipper API unavailable at {args.klipper_uds}: {exc}; falling back to marker input", file=sys.stderr)
        return None, None, {}, False

    print(f"[tuner] Klipper API connected: {args.klipper_uds}", file=sys.stderr)
    return client, matcher, {}, matcher.attached


def process_klipper_motion_message(tuner: Tuner, matcher: SegmentMatcher, status_cache: dict, message: dict) -> int:
    if merge_status_update(status_cache, message) is None:
        return 0
    state = matcher_state_from_status(status_cache)
    count = 0
    for raw in matcher.update(state):
        tuner.on_m118(raw)
        count += 1
    return count


def pump_klipper_motion(tuner: Tuner, client: KlipperApiClient, matcher: SegmentMatcher, status_cache: dict) -> int:
    count = 0
    first = True
    while True:
        msg = client.poll(0.05 if first else 0.0)
        first = False
        if msg is None:
            return count
        count += process_klipper_motion_message(tuner, matcher, status_cache, msg)


def run_loop(args) -> None:
    lock_path = acquire_state_lock(args.state)
    klipper_log = None
    klipper_client = None
    klipper_matcher = None
    klipper_status = {}
    marker_file_suppressed_by_uds = False
    marker_file = None
    tuner = None
    try:
        if args.klipper_log:
            try:
                klipper_log = open(args.klipper_log, "r")
                klipper_log.seek(0, os.SEEK_END)
                print(f"[tuner] tailing Klipper log: {args.klipper_log}", file=sys.stderr)
            except OSError as exc:
                print(f"[tuner] could not open Klipper log: {exc}", file=sys.stderr)
                sys.exit(1)
        if args.marker_file:
            parent = os.path.dirname(os.path.abspath(args.marker_file))
            os.makedirs(parent, exist_ok=True)
            if not args.keep_marker_file:
                with open(args.marker_file, "w"):
                    pass
            marker_file = open(args.marker_file, "r")
            marker_file.seek(0, os.SEEK_END)
            action = "tailing existing marker file" if args.keep_marker_file else "reset and tailing marker file"
            print(f"[tuner] {action}: {args.marker_file}", file=sys.stderr)
        ser = open_serial(args.port, args.baud)
        lines: queue.Queue = queue.Queue(maxsize=1024)
        threading.Thread(target=reader, args=(ser, lines), daemon=True).start()
        tuner = Tuner(ser, args.state, args.machine_id, port=args.port, baud=args.baud)
        tuner.debug = args.debug
        tuner.allow_bias_writes = args.allow_bias_writes
        tuner.allow_baseline_writes = args.allow_baseline_writes
        tuner.progress_interval = max(0.0, args.progress_interval)
        if hasattr(args, "csv_out") and args.csv_out:
            tuner.csv_emitter = CsvEmitter(args.csv_out)
        klipper_client, klipper_matcher, klipper_status, marker_file_suppressed_by_uds = setup_klipper_motion(args)
        poll_interval = 1.0 / args.poll_hz
        next_poll = time.monotonic()
        while True:
            now = time.monotonic()
            if now >= next_poll:
                tuner._send("?:", count_rate=False)
                next_poll = now + poll_interval
            if klipper_client and klipper_matcher:
                try:
                    pump_klipper_motion(tuner, klipper_client, klipper_matcher, klipper_status)
                    marker_file_suppressed_by_uds = marker_file_suppressed_by_uds or klipper_matcher.attached
                except (BrokenPipeError, ConnectionResetError, ConnectionError, OSError, ValueError) as exc:
                    klipper_client.close()
                    klipper_client = None
                    if args.klipper_mode == "on":
                        print(f"nosf_live_tuner: Klipper API lost: {exc}", file=sys.stderr)
                        sys.exit(2)
                    print(f"[tuner] warning: Klipper API lost: {exc}; continuing with marker fallback", file=sys.stderr)
            if klipper_log:
                for log_line in klipper_log.readlines():
                    if "NOSF_TUNE:" in log_line:
                        tuner.on_m118(log_line)
            if marker_file and not marker_file_suppressed_by_uds:
                for marker_line in marker_file.readlines():
                    parts = marker_line.strip().split(" ", 1)
                    if len(parts) == 2:
                        tuner.on_m118(parts[1])
                    elif parts:
                        tuner.on_m118(parts[0])
                if tuner.start_seen:
                    marker_file.seek(0, os.SEEK_END)
                    tuner.start_seen = False
                    if tuner.debug:
                        print("[tuner] marker file rewound to EOF on START", file=sys.stderr)
            try:
                line = lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if line.startswith("__SERIAL_ERROR__:"):
                if not tuner._reopen_serial():
                    sys.exit(2)
                lines = queue.Queue(maxsize=1024)
                threading.Thread(target=reader, args=(tuner.ser, lines), daemon=True).start()
                continue
            if "NOSF_TUNE:" in line:
                tuner.on_m118(line)
            elif EVENT_RE.match(line):
                tuner.on_event(line)
            elif "BUF:" in line and "BP:" in line:
                tuner.on_status(line)
                commit_now = (
                    (args.commit_on_idle and tuner.print_idle_ready()) or
                    (args.commit_on_finish and tuner.finish_seen and tuner.seen_print_activity)
                )
                if commit_now:
                    if getattr(args, "observe_daemon", False):
                        tuner._persist()
                        print(f"[tuner] print {tuner.run_seq} complete, {tuner.locked_bucket_count()} LOCKED buckets", file=sys.stderr)
                        tuner.finish_seen = False
                        tuner.seen_print_activity = False
                        tuner.idle_since = 0.0
                        tuner.total_print_mid_s = 0.0
                        if args.recommend_recheck:
                            do_recommend_recheck(args.state, args.machine_id)
                        continue
                    else:
                        finish_commit(tuner, args)
                        return
    except KeyboardInterrupt:
        if tuner:
            tuner._persist()
        print("[tuner] persisted state on exit", file=sys.stderr)
    finally:
        if tuner and tuner.csv_emitter:
            tuner.csv_emitter.close()
        if klipper_client:
            klipper_client.close()
        if klipper_log:
            klipper_log.close()
        if marker_file:
            marker_file.close()
        release_state_lock(lock_path)


def do_recommend_recheck(state_path: str, machine_id: str) -> None:
    if not os.path.exists(state_path):
        print(f"[recommend-recheck] no state file: {state_path}")
        sys.exit(0)
    try:
        with open(state_path) as fh:
            data = json.load(fh)
        data = migrate_state_data(data)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"[recommend-recheck] error reading state: {exc}")
        sys.exit(0)
    
    machine_data = data.get(machine_id, {})
    meta = machine_data.get("_meta", {})
    
    total_samples = 0
    locked_buckets = []
    for k, v in machine_data.items():
        if k.startswith("_"): continue
        total_samples += v.get("n", 0)
        if v.get("locked") or v.get("state") == "LOCKED":
            locked_buckets.append(v)
            
    last_commit_vals = meta.get("last_commit_values", {})
    last_commit_samples = meta.get("last_commit_sample_total", 0)
    
    config_applied_ats = [v["applied_at"] for v in last_commit_vals.values() if v.get("source") == "config.ini" and v.get("applied_at")]
    last_commit_at = max(config_applied_ats) if config_applied_ats else 0
    
    now_ts = time.time()
    days_since = (now_ts - last_commit_at) / 86400.0 if last_commit_at else 0.0
    
    new_locked = [b for b in locked_buckets if b.get("last_seen", 0) > last_commit_at]
    sample_diff = total_samples - last_commit_samples
    sample_pct = (sample_diff / max(1, last_commit_samples)) * 100.0
    
    drift_count = 0
    baseline_val = last_commit_vals.get("baseline_rate", {})
    if baseline_val.get("source") == "config.ini" and baseline_val.get("value"):
        import math
        ref = float(baseline_val["value"])
        thr = math.sqrt(P_STABLE_THR)
        for b in locked_buckets:
            if abs(b.get("x", ref) - ref) > thr:
                drift_count += 1

    reasons = []
    if len(new_locked) >= RECHECK_NEW_LOCKED:
        reasons.append(f"new LOCKED")
    if sample_pct >= RECHECK_SAMPLE_PCT and sample_diff > 0:
        reasons.append(f"sample mass")
    if drift_count >= 1:
        reasons.append(f"drift")
    if last_commit_at and days_since >= RECHECK_AGE_DAYS:
        reasons.append(f"age")
        
    date_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_commit_at)) if last_commit_at else "never"
    print(f"[recommend-recheck] last commit: {date_str} ({days_since:.1f} days ago)")
    print(f"[recommend-recheck] sample mass since commit: +{sample_diff} ({sample_pct:.1f}%)")
    print(f"[recommend-recheck] new LOCKED buckets since commit: {len(new_locked)}")
    print(f"[recommend-recheck] flagged buckets (drift > 1σ from committed): {drift_count}")
    
    if reasons:
        print("[recommend-recheck] RECOMMEND: yes — re-run analyze")
    else:
        print("[recommend-recheck] RECOMMEND: no")


def do_prune_stale(state_path: str, machine_id: str) -> None:
    if not os.path.exists(state_path):
        print(f"[prune-stale] no state file: {state_path}")
        return
    try:
        with open(state_path) as fh:
            data = json.load(fh)
        data = migrate_state_data(data)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"[prune-stale] error reading state: {exc}")
        return
    
    machine_data = data.get(machine_id)
    if not machine_data:
        print("[prune-stale] no buckets for machine")
        return
        
    now_ts = time.time()
    cutoff = now_ts - STALE_AGE_DAYS * 86400
    
    to_remove = []
    for k, v in machine_data.items():
        if k.startswith("_"): continue
        if v.get("last_seen", 0) < cutoff:
            to_remove.append(k)
            
    if not to_remove:
        print("[prune-stale] no stale buckets found")
        return
        
    for k in to_remove:
        del machine_data[k]
        
    tmp = state_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, state_path)
    print(f"[prune-stale] removed {len(to_remove)} stale buckets")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Closed-loop live tuner for NOSF sync buckets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--port", help="Serial port, e.g. /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--state", help="State JSON path; default is ~/nosf-state/buckets-<machine-id>.json")
    ap.add_argument("--machine-id", default="default")
    ap.add_argument("--poll-hz", type=float, default=10.0)
    ap.add_argument("--emit-config-patch", metavar="PATH", help="Emit config.ini patch from locked state and exit")
    ap.add_argument("--unlock", metavar="FEATURE", help="Unlock matching bucket label or feature prefix and exit")
    ap.add_argument("--state-info", action="store_true", help="Print state summary table and exit")
    ap.add_argument("--csv", action="store_true", help="With --state-info, emit machine-readable CSV rows")
    ap.add_argument("--verbose", action="store_true", help="With --state-info, append residual lock diagnostics")
    ap.add_argument("--include-stale", action="store_true", help="Include buckets not seen in >60 days in --state-info")
    ap.add_argument("--csv-out", metavar="PATH", help="Append per-status-row CSV alongside JSON state")
    ap.add_argument("--recommend-recheck", action="store_true", help="Evaluate watermark drift and suggest if analyzer should be run")
    ap.add_argument("--prune-stale", action="store_true", help="Remove buckets not seen in >60 days from state file")
    ap.add_argument("--observe-daemon", action="store_true", help="Persist and continue on FINISH instead of exiting")
    ap.add_argument("--reset-runtime", action="store_true", help="Send LIVE_TUNE_LOCK:0 and LD:, then exit")
    ap.add_argument("--commit-on-idle", action="store_true", help="On print idle, emit /tmp/nosf-patch.ini and exit")
    ap.add_argument("--commit-on-finish", action="store_true", help="Exit immediately on FINISH marker (no idle wait); implies commit if locked buckets exist")
    ap.add_argument("--klipper-log", help="Tail klippy.log for NOSF_TUNE marker echoes while tuning")
    ap.add_argument("--klipper-uds", default=DEFAULT_UDS_PATH, help="Klipper API Unix socket path")
    ap.add_argument(
        "--klipper-mode",
        choices=("auto", "on", "off"),
        default="auto",
        help="auto tries Klipper API then falls back; on requires it; off forces marker fallback",
    )
    ap.add_argument("--sidecar", metavar="PATH", help="Sidecar JSON generated by gcode_marker.py --emit sidecar")
    ap.add_argument("--marker-file", help="Tail local marker file written by scripts/nosf_marker.py")
    ap.add_argument(
        "--keep-marker-file",
        action="store_true",
        help="Do not truncate --marker-file on startup; useful only when attaching mid-print",
    )
    ap.add_argument(
        "--progress-interval",
        type=float,
        default=10.0,
        help="Seconds between per-bucket --debug progress lines; 0 disables progress lines",
    )
    ap.add_argument(
        "--allow-bias-writes",
        action="store_true",
        help="Experimental: allow live SET:TRAIL_BIAS_FRAC writes from learned buckets",
    )
    ap.add_argument(
        "--allow-baseline-writes",
        action="store_true",
        help="Experimental: allow live SET:BASELINE_SPS writes from learned buckets",
    )
    ap.add_argument("--debug", action="store_true", help="Print marker and commit diagnostics to stderr")
    args = ap.parse_args()
    if args.state is None:
        args.state = default_state_path(args.machine_id)

    if args.emit_config_patch:
        emit_patch(args.state, args.machine_id, args.emit_config_patch)
        return
    if args.state_info:
        print_state_info(
            args.state,
            args.machine_id,
            csv_mode=args.csv,
            include_stale=args.include_stale,
            verbose=args.verbose,
        )
        return
    if args.recommend_recheck:
        do_recommend_recheck(args.state, args.machine_id)
        return
    if args.prune_stale:
        do_prune_stale(args.state, args.machine_id)
        return
    if args.unlock:
        unlock_bucket(args.state, args.machine_id, args.unlock)
        return
    if args.reset_runtime:
        if not args.port:
            print("nosf_live_tuner: --port is required for --reset-runtime", file=sys.stderr)
            sys.exit(1)
        ser = open_serial(args.port, args.baud)
        ser.write(b"SET:LIVE_TUNE_LOCK:0\n")
        time.sleep(0.1)
        ser.write(b"LD:\n")
        ser.close()
        return
    if not args.port:
        print("nosf_live_tuner: --port is required for live tuning", file=sys.stderr)
        sys.exit(1)
    run_loop(args)

if __name__ == "__main__":
    main()
