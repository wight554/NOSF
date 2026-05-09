#!/usr/bin/env python3
"""nosf_live_tuner.py - closed-loop live tuner for NOSF Phase 2.8.

Pure stdlib plus pyserial. No numpy, scipy, sklearn, or pandas.

Usage examples:
    python3 scripts/nosf_live_tuner.py --port /dev/ttyACM0 --machine-id myprinter
    python3 scripts/nosf_live_tuner.py --state ~/nosf-state/buckets-myprinter.json \
        --machine-id myprinter --emit-config-patch /tmp/nosf-patch.ini
    python3 scripts/nosf_live_tuner.py --port /dev/ttyACM0 --reset-runtime

The live loop reads NOSF status and marker lines, learns per
(feature, v_fil_bin) buckets, and writes bounded SET updates for
TRAIL_BIAS_FRAC. Runtime BASELINE_SPS writes are disabled by default
because the status EST field is a live flow estimate, not a safe global
baseline target; use --allow-baseline-writes only for controlled
experiments. If a serial write fails, the tuner waits 1 s and attempts
to reopen the configured port up to five times. If reconnect fails, it
exits non-zero without modifying the state file.

Config patch emission uses recency-weighted bucket means:
    weight = n / (1 + age_seconds / 86400)
where age_seconds is computed from each bucket's last_seen wall-clock
timestamp. Recent locked buckets therefore carry full sample weight while
older buckets decay gently over multi-day tuning cycles.
"""

import argparse
import json
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


CF_GATE = 0.6
APX_THR = 4
P_STABLE_THR = 100.0
SET_DEADBAND_SPS = 30.0
BIAS_DEADBAND = 0.03
MIN_SET_SPACING = 2.0
N_MIN_SAMPLES = 200
BUCKET_LOCK_S = 60.0
Q_PROCESS = 25.0
R_BASE = 100.0
SCHEMA_VERSION = 1
DEFAULT_STATE_DIR = os.path.expanduser("~/nosf-state")

STATUS_FIELD_RE = re.compile(r"(?P<key>[A-Z0-9]+):(?P<val>-?\d+(?:\.\d+)?|[A-Z_]+|[^,]*)")
EVENT_RE = re.compile(r"^EV:([A-Z_]+),([A-Z_]+)")
MARK_RE = re.compile(r"MK:(?P<seq>\d+):(?P<tag>[^,]*)")
M118_RE = re.compile(
    r"NOSF_TUNE:(?P<feature>[^:]+):V(?P<vfil>[^:]+):W(?P<w>[^:]+):H(?P<h>[^:\s]+)"
)
COMPACT_MARK_RE = re.compile(r"NT:(?P<feature>[^:]+):V(?P<vfil>[^:\s]+)")


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
    return f"{feature}_v{int(round(v_fil / 5.0)) * 5}"


def kf_predict_update(bucket: Bucket, z: float, cf: float, apx: int, dt_s: float) -> None:
    bucket.P += Q_PROCESS * max(dt_s, 0.0)
    R = (R_BASE / max(cf, 0.05)) * (1.0 + apx / float(APX_THR))
    K = bucket.P / (bucket.P + R)
    bucket.x += K * (z - bucket.x)
    bucket.P *= 1.0 - K
    bucket.n += 1


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
        self.debug = False
        self.recent_sets = deque()
        self.last_rate_limit_warn_t = -999.0
        self.allow_baseline_writes = False
        self.last_baseline_skip_warn_t = -999.0
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
        if data.get("_schema") != SCHEMA_VERSION:
            print("[tuner] warning: state file schema mismatch; ignoring", file=sys.stderr)
            return
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
            )
            self.buckets[label] = b

    def _persist(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.state_path))
        os.makedirs(parent, exist_ok=True)
        try:
            with open(self.state_path) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            data = {}
        data["_schema"] = SCHEMA_VERSION
        data[self.machine_id] = {
            label: {
                "x": b.x,
                "P": b.P,
                "n": b.n,
                "bias": b.bias,
                "bp_ewma": b.bp_ewma,
                "locked": b.state == "LOCKED" or b.locked,
                "last_set_x": b.last_set_x,
                "last_set_bias": b.last_set_bias,
                "first_seen": b.first_seen,
                "last_seen": b.last_seen,
            }
            for label, b in sorted(self.buckets.items())
        }
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
        if est <= 0.0:
            return

        label = bucket_label(self.last_feature, self.last_v_fil)
        wall_now = self.wall_fn()
        b = self.buckets.setdefault(label, Bucket(label=label, x=est, first_seen=wall_now))
        self.active_label = label
        b.last_seen = wall_now
        if b.first_seen == 0.0:
            b.first_seen = wall_now

        if self.last_status_t == 0.0:
            self.last_status_t = now
            return
        dt_s = now - self.last_status_t
        self.last_status_t = now

        if b.state == "LOCKED" or b.locked:
            if abs(est - b.x) * abs(est - b.x) > 4.0 * (b.P + R_BASE):
                b.state = "TRACKING"
                b.locked = False
                b.P = max(b.P, 1e4)
            return

        kf_predict_update(b, est, cf, apx, dt_s)
        b.bp_ewma = 0.95 * b.bp_ewma + 0.05 * bp if b.n > 1 else bp
        bias_target = 0.4 + (b.bp_ewma - rt) / 7.8
        b.bias = max(0.0, min(0.7, 0.95 * b.bias + 0.05 * bias_target))
        self._maybe_emit_set(b, now)
        self._maybe_lock(b, now)

    def _maybe_emit_set(self, b: Bucket, now: float) -> None:
        if now - self.last_set_t < MIN_SET_SPACING:
            return
        if b.n < N_MIN_SAMPLES:
            return
        if b.P > 4.0 * P_STABLE_THR:
            return
        if abs(b.x - b.last_set_x) >= SET_DEADBAND_SPS:
            if not self.allow_baseline_writes:
                if self.debug and now - self.last_baseline_skip_warn_t >= 30.0:
                    print(
                        "[tuner] baseline write skipped; pass --allow-baseline-writes to enable",
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
            if self._rate_limited(now):
                return
            self._engage_lock()
            self._send(f"SET:TRAIL_BIAS_FRAC:{b.bias:.3f}")
            b.last_set_bias = b.bias
            self.last_set_t = now

    def _maybe_lock(self, b: Bucket, now: float) -> None:
        x_stable = (not self.allow_baseline_writes) or abs(b.x - b.last_set_x) < SET_DEADBAND_SPS
        stable = (
            b.P < P_STABLE_THR
            and b.n >= N_MIN_SAMPLES
            and x_stable
            and abs(b.bias - b.last_set_bias) < BIAS_DEADBAND
        )
        if not stable:
            b.state = "TRACKING"
            b.stable_since = 0.0
            return
        if b.state != "STABLE":
            b.state = "STABLE"
            b.stable_since = now
            return
        if now - b.stable_since >= BUCKET_LOCK_S:
            b.state = "LOCKED"
            b.locked = True
            self._persist()

    def _rollback_active(self) -> None:
        if not self.active_label:
            return
        b = self.buckets.get(self.active_label)
        if not b or b.locked or b.state == "LOCKED":
            return
        b.P = max(b.P, 1e4)
        if self.allow_baseline_writes and b.last_set_x:
            self._send(f"SET:BASELINE_SPS:{int(round(b.last_set_x))}")

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
        if float(raw.get("P", 1e6)) < P_STABLE_THR and int(raw.get("n", 0)) >= N_MIN_SAMPLES:
            return "STABLE"
    except (TypeError, ValueError):
        pass
    return "TRACKING"


def print_state_info(state_path: str, machine_id: str) -> None:
    if not os.path.exists(state_path):
        print(f"[tuner] no state file: {state_path}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(state_path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[tuner] state file unreadable: {exc}", file=sys.stderr)
        sys.exit(1)
    if data.get("_schema") != SCHEMA_VERSION:
        print(
            f"[tuner] state file schema mismatch: got {data.get('_schema')!r}, expected {SCHEMA_VERSION}",
            file=sys.stderr,
        )
        sys.exit(1)
    buckets = data.get(machine_id, {})
    print(f"{'feature_v_fil':<18} {'x':>7} {'P':>8} {'n':>6} {'bias':>7} {'state':>8}")
    locked = 0
    total_n = 0
    for label, raw in sorted(buckets.items()):
        st = state_label(raw)
        if st == "LOCKED":
            locked += 1
        n = int(raw.get("n", 0))
        total_n += n
        print(
            f"{label:<18} {float(raw.get('x', 0.0)):>7.0f} "
            f"{float(raw.get('P', 0.0)):>8.1f} {n:>6d} "
            f"{float(raw.get('bias', 0.0)):>7.3f} {st:>8}"
        )
    print(f"TOTAL: {len(buckets)} buckets, {locked} locked, {total_n} samples")


def emit_patch(state_path: str, machine_id: str, out_path: str) -> None:
    if not os.path.exists(state_path):
        print("[tuner] no state file", file=sys.stderr)
        sys.exit(1)
    with open(state_path) as fh:
        data = json.load(fh)
    if data.get("_schema") != SCHEMA_VERSION:
        print("[tuner] state file schema mismatch", file=sys.stderr)
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
    with open(out_path, "w") as fh:
        fh.write("# nosf_live_tuner.py emitted patch\n")
        fh.write("# Apply to config.ini after review.\n")
        fh.write("# recency weight: n / (1 + age_seconds / 86400)\n")
        fh.write(f"# locked buckets: {len(locked)}\n")
        for label, raw in sorted(locked.items()):
            fh.write(
                f"#   {label}: x={float(raw.get('x', 0.0)):.0f} "
                f"bias={float(raw.get('bias', 0.4)):.3f} "
                f"n={int(raw.get('n', 0))} weight={weights[label]:.1f}\n"
            )
        fh.write(
            f"# baseline_rate_sps_suggestion: {int(round(baseline))} "
            "(experimental; verify manually before applying)\n"
        )
        fh.write(f"sync_trailing_bias_frac: {bias:.3f}\n")
    print(f"[tuner] wrote patch: {out_path}", file=sys.stderr)


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


def run_loop(args) -> None:
    lock_path = acquire_state_lock(args.state)
    klipper_log = None
    marker_file = None
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
            marker_file = open(args.marker_file, "a+")
            marker_file.seek(0, os.SEEK_END)
            print(f"[tuner] tailing marker file: {args.marker_file}", file=sys.stderr)
        ser = open_serial(args.port, args.baud)
        lines: queue.Queue = queue.Queue(maxsize=1024)
        threading.Thread(target=reader, args=(ser, lines), daemon=True).start()
        tuner = Tuner(ser, args.state, args.machine_id, port=args.port, baud=args.baud)
        tuner.debug = args.debug
        tuner.allow_baseline_writes = args.allow_baseline_writes
        poll_interval = 1.0 / args.poll_hz
        next_poll = time.monotonic()
        while True:
            now = time.monotonic()
            if now >= next_poll:
                tuner._send("?:", count_rate=False)
                next_poll = now + poll_interval
            if klipper_log:
                for log_line in klipper_log.readlines():
                    if "NOSF_TUNE:" in log_line:
                        tuner.on_m118(log_line)
            if marker_file:
                for marker_line in marker_file.readlines():
                    parts = marker_line.strip().split(" ", 1)
                    if len(parts) == 2:
                        tuner.on_m118(parts[1])
                    elif parts:
                        tuner.on_m118(parts[0])
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
                if args.commit_on_idle and tuner.print_idle_ready():
                    tuner._persist()
                    if tuner.locked_bucket_count() == 0:
                        print("[tuner] FINISH seen, but no LOCKED buckets yet; persisted tracking state without SV", file=sys.stderr)
                        raise SystemExit(1)
                    tuner._send("SET:LIVE_TUNE_LOCK:0", count_rate=False)
                    time.sleep(0.1)
                    tuner._send("SV:", count_rate=False)
                    emit_patch(args.state, args.machine_id, "/tmp/nosf-patch.ini")
                    print("[tuner] commit-on-idle patch: /tmp/nosf-patch.ini", file=sys.stderr)
                    return
    except KeyboardInterrupt:
        tuner._persist()
        print("[tuner] persisted state on exit", file=sys.stderr)
    finally:
        if klipper_log:
            klipper_log.close()
        if marker_file:
            marker_file.close()
        release_state_lock(lock_path)


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
    ap.add_argument("--reset-runtime", action="store_true", help="Send LIVE_TUNE_LOCK:0 and LD:, then exit")
    ap.add_argument("--commit-on-idle", action="store_true", help="On print idle, unlock, SV:, emit /tmp/nosf-patch.ini, and exit")
    ap.add_argument("--klipper-log", help="Tail klippy.log for NOSF_TUNE marker echoes while tuning")
    ap.add_argument("--marker-file", help="Tail local marker file written by scripts/nosf_marker.py")
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
        print_state_info(args.state, args.machine_id)
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
