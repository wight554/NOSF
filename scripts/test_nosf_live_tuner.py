#!/usr/bin/env python3
"""Stdlib regression fixture for nosf_live_tuner.py."""

import json
import os
import sys
import tempfile
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

import nosf_live_tuner as tuner_mod


class FakeSerial:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data.decode("utf-8").strip())
        return len(data)


class Clock:
    def __init__(self, start=0.0):
        self.t = start

    def now(self):
        return self.t

    def step(self, dt):
        self.t += dt
        return self.t


def status(est=1820, cf=0.90, apx=0, zone="MID", tc="IDLE", bp=-3.0, rt=-2.8):
    return (
        f"OK:LN:1,TC:{tc},BUF:{zone},BP:{bp:.2f},RT:{rt:.2f},"
        f"EST:{est:.1f},CF:{cf:.2f},APX:{apx}"
    )


def make_tuner(state_path, clock):
    fake = FakeSerial()
    t = tuner_mod.Tuner(fake, state_path, "test", now_fn=clock.now, wall_fn=clock.now)
    t.on_m118("echo: NOSF_TUNE:PERIMETER:V40:W0.45:H0.20")
    return t, fake


def test_cold_start_no_set():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        for _ in range(120):
            clock.step(0.25)
            t.on_status(status(est=1800))
        assert not fake.writes, fake.writes
        return "no SETs during first 30 s warm-up"


def test_locked_warm_start_zero_sets():
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        with open(state_path, "w") as fh:
            json.dump(
                {
                    "_schema": tuner_mod.SCHEMA_VERSION,
                    "test": {
                        "PERIMETER_v40": {
                            "x": 1820.0,
                            "P": 78.2,
                            "n": 4321,
                            "bias": 0.420,
                            "bp_ewma": -3.1,
                            "locked": True,
                            "last_set_x": 1820.0,
                            "last_set_bias": 0.420,
                        }
                    },
                },
                fh,
            )
        clock = Clock()
        t, fake = make_tuner(state_path, clock)
        for _ in range(3):
            clock.step(1.0)
            t.on_status(status(est=1820))
        assert not fake.writes, fake.writes
        return "locked warm-start emits zero SETs"


def test_adv_risk_freeze_and_rollback():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        t.allow_baseline_writes = True
        b = tuner_mod.Bucket(
            label="PERIMETER_v40",
            x=1900.0,
            P=50.0,
            n=250,
            last_set_x=1850.0,
            last_set_bias=0.4,
        )
        t.buckets[b.label] = b
        t.active_label = b.label
        t.on_event("EV:SYNC,ADV_RISK_HIGH")
        assert "SET:BASELINE_SPS:1850" in fake.writes, fake.writes
        before = len(fake.writes)
        for _ in range(20):
            clock.step(1.0)
            b.x += 100.0
            t.on_status(status(est=b.x))
        assert len(fake.writes) == before, fake.writes
        assert t.frozen_until >= 30.0
        return "ADV_RISK_HIGH rolls back and freezes writes"


def test_adv_dwell_stop_halts():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        t.on_event("EV:SYNC,ADV_DWELL_STOP")
        for _ in range(250):
            clock.step(0.25)
            t.on_status(status(est=2200))
        assert t.halted
        assert not fake.writes, fake.writes
        return "ADV_DWELL_STOP halts without further writes"


def test_rate_limit_three_sets_per_window():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        t.allow_baseline_writes = True
        b = tuner_mod.Bucket(label="PERIMETER_v40", x=1100.0, P=50.0, n=250, last_set_x=1000.0)
        for _ in range(5):
            clock.step(3.0)
            b.x += 100.0
            t._maybe_emit_set(b, clock.now())
        baseline_sets = [w for w in fake.writes if w.startswith("SET:BASELINE_SPS:")]
        assert len(baseline_sets) == 3, fake.writes
        return "rate limit allows only 3 SETs per 30 s window"


def test_baseline_writes_disabled_by_default():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        b = tuner_mod.Bucket(
            label="PERIMETER_v40",
            x=1300.0,
            P=50.0,
            n=250,
            bias=0.4,
            last_set_x=1600.0,
            last_set_bias=0.4,
        )
        clock.step(3.0)
        t._maybe_emit_set(b, clock.now())
        assert not [w for w in fake.writes if w.startswith("SET:BASELINE_SPS:")], fake.writes
        return "baseline SETs are opt-in only"


def test_observe_default_no_writes():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        b = tuner_mod.Bucket(
            label="PERIMETER_v40",
            x=1600.0,
            P=50.0,
            n=250,
            bias=0.470,
            last_set_x=1600.0,
            last_set_bias=0.400,
        )
        clock.step(3.0)
        t._maybe_emit_set(b, clock.now())
        assert not fake.writes, fake.writes
        return "observe mode emits zero SET writes by default"


def test_allow_bias_writes_writes():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        t.allow_bias_writes = True
        b = tuner_mod.Bucket(
            label="PERIMETER_v40",
            x=1600.0,
            P=50.0,
            n=250,
            bias=0.470,
            last_set_x=1600.0,
            last_set_bias=0.400,
        )
        clock.step(3.0)
        t._maybe_emit_set(b, clock.now())
        assert "SET:LIVE_TUNE_LOCK:1" in fake.writes, fake.writes
        assert "SET:TRAIL_BIAS_FRAC:0.470" in fake.writes, fake.writes
        return "explicit bias-write mode still emits guarded bias SETs"


def test_finish_commit_emits_patch_no_sv():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        state_path = os.path.join(td, "state.json")
        patch_path = os.path.join(td, "patch.ini")
        fake = FakeSerial()
        t = tuner_mod.Tuner(fake, state_path, "test", now_fn=clock.now, wall_fn=clock.now)
        t.buckets["PERIMETER_v40"] = tuner_mod.Bucket(
            label="PERIMETER_v40",
            x=1820.0,
            P=20.0,
            n=250,
            bias=0.420,
            state="LOCKED",
            locked=True,
            last_seen=clock.now(),
        )
        args = SimpleNamespace(state=state_path, machine_id="test")
        tuner_mod.finish_commit(t, args, patch_path)
        assert "SET:LIVE_TUNE_LOCK:0" not in fake.writes, fake.writes
        assert "SV:" not in fake.writes, fake.writes
        assert os.path.exists(patch_path), patch_path
        return "finish_commit emits patch without SV"


def test_schema1_migration():
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        with open(state_path, "w") as fh:
            json.dump(
                {
                    "_schema": 1,
                    "test": {
                        "PERIMETER_v40": {
                            "x": 1820.0,
                            "P": 78.2,
                            "n": 4321,
                            "bias": 0.420,
                            "bp_ewma": -3.1,
                            "locked": True,
                            "last_set_x": 1820.0,
                            "last_set_bias": 0.420,
                        }
                    },
                },
                fh,
            )
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), state_path, "test", now_fn=clock.now, wall_fn=clock.now)
        b = t.buckets["PERIMETER_v40"]
        assert b.x == 1820.0, b
        assert b.n == 4321, b
        assert b.low_flow_skip_count == 0, b.low_flow_skip_count
        assert b.rail_skip_count == 0, b.rail_skip_count
        assert b.rollback_count == 0, b.rollback_count
        assert b.runs_seen == 0, b.runs_seen
        assert b.layers_seen == 0, b.layers_seen
        assert b.cumulative_mid_s == 0.0, b.cumulative_mid_s
        t._persist()
        with open(state_path) as fh:
            migrated = json.load(fh)
        assert migrated["_schema"] == tuner_mod.SCHEMA_VERSION, migrated
        raw = migrated["test"]["PERIMETER_v40"]
        assert raw["low_flow_skip_count"] == 0, raw
        assert raw["rail_skip_count"] == 0, raw
        assert raw["rollback_count"] == 0, raw
        return "schema 1 state migrates to schema 2 with zeroed counters"


def test_schema2_to_3_migration_preserves_buckets():
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        with open(state_path, "w") as fh:
            json.dump(
                {
                    "_schema": 2,
                    "test": {
                        "PERIMETER_v40": {
                            "x": 1820.0,
                            "P": 78.2,
                            "n": 4321,
                            "bias": 0.420,
                            "bp_ewma": -3.1,
                            "locked": True,
                            "state": "LOCKED",
                            "last_set_x": 1820.0,
                            "last_set_bias": 0.420,
                            "runs_seen": 2,
                            "layers_seen": 5,
                            "cumulative_mid_s": 80.0,
                            "low_flow_skip_count": 0,
                            "rail_skip_count": 0,
                            "rollback_count": 0,
                            "first_seen": 100.0,
                            "last_seen": 200.0,
                            "first_seen_run": "run1",
                        }
                    },
                },
                fh,
            )
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), state_path, "test", now_fn=clock.now, wall_fn=clock.now)
        b = t.buckets["PERIMETER_v40"]
        assert b.x == 1820.0, b
        assert b.n == 4321, b
        assert b.locked is True, b.locked
        assert b.state == "LOCKED", b.state
        
        t._persist()
        with open(state_path) as fh:
            migrated = json.load(fh)
        assert migrated["_schema"] == 3, migrated
        assert "_meta" in migrated["test"], migrated
        meta = migrated["test"]["_meta"]
        assert "baseline_rate" in meta["last_commit_values"], meta
        assert meta["last_commit_values"]["baseline_rate"]["source"] == "default", meta
        return "schema 2 state migrates to schema 3 preserving buckets and creating _meta"

def test_schema_chain_1_to_3():
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        with open(state_path, "w") as fh:
            json.dump(
                {
                    "_schema": 1,
                    "test": {
                        "PERIMETER_v40": {
                            "x": 1820.0,
                            "P": 78.2,
                            "n": 4321,
                            "bias": 0.420,
                            "bp_ewma": -3.1,
                            "locked": True,
                            "state": "LOCKED",
                        }
                    },
                },
                fh,
            )
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), state_path, "test", now_fn=clock.now, wall_fn=clock.now)
        b = t.buckets["PERIMETER_v40"]
        assert b.x == 1820.0, b
        
        t._persist()
        with open(state_path) as fh:
            migrated = json.load(fh)
        assert migrated["_schema"] == 3, migrated
        return "schema 1 state migrates to schema 3 via chain"

def test_schema_too_new_refused():
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        with open(state_path, "w") as fh:
            json.dump({"_schema": 99, "test": {}}, fh)
        clock = Clock()
        try:
            tuner_mod.Tuner(FakeSerial(), state_path, "test", now_fn=clock.now, wall_fn=clock.now)
        except SystemExit as exc:
            assert exc.code == 1, exc.code
            return "schema 99 refused"
        assert False, "expected SystemExit"

def test_existing_production_state_loads():
    return "skipped (no production state file in dev)"


def test_counter_increments():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        clock.step(1.0)
        t.on_status(status(est=1800))
        label = "PERIMETER_v50"
        b = t.buckets[label]

        clock.step(1.0)
        t.on_status(status(est=0.5))
        assert b.low_flow_skip_count == 1, b.low_flow_skip_count

        t.allow_bias_writes = True
        b.P = 50.0
        b.n = 250
        b.bias = 0.700
        b.last_set_x = b.x
        b.last_set_bias = 0.400
        clock.step(3.0)
        t._maybe_emit_set(b, clock.now())
        assert b.rail_skip_count == 1, b.rail_skip_count
        assert not [w for w in fake.writes if w.startswith("SET:TRAIL_BIAS_FRAC:")], fake.writes

        t.active_label = label
        t.allow_baseline_writes = True
        b.locked = False
        b.state = "TRACKING"
        b.last_set_x = 1775.0
        t._rollback_active()
        assert b.rollback_count == 1, b.rollback_count
        assert "SET:BASELINE_SPS:1775" in fake.writes, fake.writes
        return "low-flow, rail, and rollback counters increment"


def test_short_print_no_lock():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        b = tuner_mod.Bucket(
            label="PERIMETER_v50",
            x=1600.0,
            P=20.0,
            n=250,
            bias=0.400,
            runs_seen=1,
            layers_seen=3,
            cumulative_mid_s=60.0,
            state="STABLE",
        )
        t._maybe_lock(b, clock.now())
        assert b.state == "STABLE", b.state
        assert not b.locked
        assert t._bucket_wait_reason(b, clock.now()) == "runs 1/2"
        return "single short calibration run cannot lock bucket"


def test_three_run_lock():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        b = tuner_mod.Bucket(
            label="PERIMETER_v50",
            x=1600.0,
            P=20.0,
            n=250,
            bias=0.400,
            runs_seen=3,
            layers_seen=3,
            cumulative_mid_s=90.0,
            state="STABLE",
        )
        t.buckets[b.label] = b
        t._maybe_lock(b, clock.now())
        assert b.state == "LOCKED", b.state
        assert b.locked
        return "three cumulative calibration runs can lock bucket"


def test_layer_count_required():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        b = tuner_mod.Bucket(
            label="PERIMETER_v50",
            x=1600.0,
            P=20.0,
            n=250,
            bias=0.400,
            runs_seen=3,
            layers_seen=2,
            cumulative_mid_s=90.0,
            state="STABLE",
        )
        t._maybe_lock(b, clock.now())
        assert b.state == "STABLE", b.state
        assert t._bucket_wait_reason(b, clock.now()) == "layers 2/3"
        return "layer count gates LOCKED state"


def test_start_and_layer_markers_increment_counters():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        t.on_m118("NT:START")
        t.on_m118("echo: NOSF_TUNE:PERIMETER:V40:W0.45:H0.20")
        clock.step(1.0)
        t.on_status(status(est=1800))
        b = t.buckets["PERIMETER_v50"]
        assert b.runs_seen == 1, b.runs_seen
        assert b.first_seen_run, b.first_seen_run
        t.on_m118("NT:LAYER:1")
        t.on_m118("NT:LAYER:1")
        assert b.layers_seen == 1, b.layers_seen
        t.on_m118("NT:LAYER:2")
        assert b.layers_seen == 2, b.layers_seen
        return "START and layer markers increment cumulative counters once"


def test_bucket_sample_credits_current_layer_once():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        t.on_m118("NT:START")
        t.on_m118("NT:LAYER:0")
        t.on_m118("echo: NOSF_TUNE:PERIMETER:V40:W0.45:H0.20")
        clock.step(1.0)
        t.on_status(status(est=1800))
        b = t.buckets["PERIMETER_v50"]
        assert b.layers_seen == 1, b.layers_seen
        clock.step(1.0)
        t.on_status(status(est=1810))
        assert b.layers_seen == 1, b.layers_seen

        t.on_m118("NT:LAYER:1")
        assert b.layers_seen == 2, b.layers_seen
        t.on_m118("echo: NOSF_TUNE:Sparse_infill:V40:W0.45:H0.20")
        clock.step(1.0)
        t.on_status(status(est=1700))
        s = t.buckets["Sparse_infill_v50"]
        assert s.layers_seen == 1, s.layers_seen
        clock.step(1.0)
        t.on_status(status(est=1710))
        assert s.layers_seen == 1, s.layers_seen
        return "bucket samples credit current layer once per bucket"


def test_low_flow_samples_are_ignored():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        for _ in range(20):
            clock.step(1.0)
            t.on_status(status(est=0.5))
        assert not t.buckets, t.buckets
        assert not fake.writes, fake.writes
        return "low-flow EST samples do not create buckets"


def test_bias_rail_guard_blocks_set_and_lock():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        b = tuner_mod.Bucket(
            label="PERIMETER_v40",
            x=1600.0,
            P=50.0,
            n=250,
            bias=0.700,
            last_set_x=1600.0,
            last_set_bias=0.4,
        )
        clock.step(3.0)
        t._maybe_emit_set(b, clock.now())
        t._maybe_lock(b, clock.now())
        assert not [w for w in fake.writes if w.startswith("SET:TRAIL_BIAS_FRAC:")], fake.writes
        assert b.state == "TRACKING", b.state
        return "rail-clamped bias is not written or locked"


def test_debug_bucket_progress_line():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, _fake = make_tuner(os.path.join(td, "state.json"), clock)
        t.debug = True
        t.progress_interval = 1.0
        b = tuner_mod.Bucket(label="PERIMETER_v40", x=1300.0, P=50.0, n=25, bias=0.39)
        out = StringIO()
        with redirect_stderr(out):
            t._debug_bucket_progress(b, 2.0, 1280.0, -6.5, 0.96, 1)
        line = out.getvalue()
        assert "bucket PERIMETER_v40" in line, line
        assert "n=25" in line, line
        assert "wait=samples 25/200" in line, line
        return "debug progress reports active bucket state"


def test_commit_idle_requires_activity():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        fake = FakeSerial()
        t = tuner_mod.Tuner(fake, os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        for _ in range(50):
            clock.step(1.0)
            t.on_status(status(est=1800))
        assert not t.print_idle_ready(), "idle should not commit before marker activity"
        t.on_m118("echo: NOSF_TUNE:PERIMETER:V40:W0.45:H0.20")
        clock.step(1.0)
        t.on_status(status(est=1800))
        clock.step(31.0)
        t.on_status(status(est=1800))
        assert not t.print_idle_ready(), "idle should wait for FINISH marker"
        t.on_m118("echo: NOSF_TUNE:FINISH:0:0:0")
        clock.step(1.0)
        t.on_status(status(est=1800))
        clock.step(31.0)
        t.on_status(status(est=1800))
        assert t.print_idle_ready(), "idle should commit after FINISH marker"
        return "commit-on-idle arms only after FINISH marker"


def test_status_mk_marker_fallback():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        t.last_feature = ""
        t.last_v_fil = 0.0
        line = status(est=1800) + ",MK:7:NT:Outer_wall:V40"
        for _ in range(3):
            clock.step(1.0)
            t.on_status(line)
        assert t.last_feature == "Outer_wall"
        assert t.last_v_fil == 40.0
        assert t.active_label == "Outer_wall_v50"
        assert not fake.writes
        return "firmware MK status marker seeds active bucket"


def test_csv_out_writes_rows():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        state_path = os.path.join(td, "state.json")
        csv_path = os.path.join(td, "run.csv")
        t, fake = make_tuner(state_path, clock)
        t.csv_emitter = tuner_mod.CsvEmitter(csv_path)
        for i in range(5):
            clock.step(1.0)
            t.on_status(status(est=1800 + i))
        t.csv_emitter.close()
        
        with open(csv_path) as fh:
            lines = fh.readlines()
        assert len(lines) == 6, lines
        assert "wall_ts" in lines[0], lines[0]
        assert "1800" in lines[1], lines[1]
        return "CsvEmitter writes header and rows"


def test_csv_out_appends_across_runs():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        state_path = os.path.join(td, "state.json")
        csv_path = os.path.join(td, "run.csv")
        t1, fake1 = make_tuner(state_path, clock)
        t1.csv_emitter = tuner_mod.CsvEmitter(csv_path)
        t1.on_status(status(est=1800))
        t1.csv_emitter.close()
        
        t2, fake2 = make_tuner(state_path, clock)
        t2.csv_emitter = tuner_mod.CsvEmitter(csv_path)
        t2.on_status(status(est=1801))
        t2.csv_emitter.close()
        
        with open(csv_path) as fh:
            lines = fh.readlines()
        assert len(lines) == 3, lines
        assert "wall_ts" in lines[0], lines[0]
        assert "1800" in lines[1], lines[1]
        assert "1801" in lines[2], lines[2]
        return "CsvEmitter appends without duplicating header"


def test_single_print_path_locks():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        b = tuner_mod.Bucket(
            label="PERIMETER_v50",
            x=1600.0,
            P=20.0,
            n=500,
            bias=0.400,
            runs_seen=1,
            layers_seen=5,
            cumulative_mid_s=60.0,
            state="STABLE",
        )
        t.total_print_mid_s = 300.0
        t._maybe_lock(b, clock.now())
        assert b.state == "LOCKED", b.state
        assert b.locked
        return "Path B locks bucket in a single print"


def test_neither_path_no_lock():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        b = tuner_mod.Bucket(
            label="PERIMETER_v50",
            x=1600.0,
            P=20.0,
            n=400,
            bias=0.400,
            runs_seen=1,
            layers_seen=4,
            cumulative_mid_s=60.0,
            state="STABLE",
        )
        t.total_print_mid_s = 200.0
        t._maybe_lock(b, clock.now())
        assert b.state == "STABLE", b.state
        assert not b.locked
        return "bucket remains STABLE if neither path is satisfied"


def test_either_path_no_double_count():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        b = tuner_mod.Bucket(
            label="PERIMETER_v50",
            x=1600.0,
            P=20.0,
            n=600,
            bias=0.400,
            runs_seen=3,
            layers_seen=6,
            cumulative_mid_s=120.0,
            state="STABLE",
        )
        t.total_print_mid_s = 400.0
        t._maybe_lock(b, clock.now())
        assert b.state == "LOCKED", b.state
        assert b.locked
        t._maybe_lock(b, clock.now())
        assert b.state == "LOCKED"
        return "bucket locks safely when both paths satisfied"


def main():
    tests = [
        ("warm-up", test_cold_start_no_set),
        ("locked", test_locked_warm_start_zero_sets),
        ("adv-risk", test_adv_risk_freeze_and_rollback),
        ("halt", test_adv_dwell_stop_halts),
        ("rate-limit", test_rate_limit_three_sets_per_window),
        ("baseline-off", test_baseline_writes_disabled_by_default),
        ("observe-default", test_observe_default_no_writes),
        ("bias-on", test_allow_bias_writes_writes),
        ("no-sv-patch", test_finish_commit_emits_patch_no_sv),
        ("schema1", test_schema1_migration),
        ("schema2-to-3", test_schema2_to_3_migration_preserves_buckets),
        ("schema-chain", test_schema_chain_1_to_3),
        ("schema-too-new", test_schema_too_new_refused),
        ("schema-prod", test_existing_production_state_loads),
        ("counters", test_counter_increments),
        ("short-print", test_short_print_no_lock),
        ("three-run", test_three_run_lock),
        ("layer-gate", test_layer_count_required),
        ("markers", test_start_and_layer_markers_increment_counters),
        ("sample-layer", test_bucket_sample_credits_current_layer_once),
        ("low-flow", test_low_flow_samples_are_ignored),
        ("bias-rail", test_bias_rail_guard_blocks_set_and_lock),
        ("debug-log", test_debug_bucket_progress_line),
        ("idle-arm", test_commit_idle_requires_activity),
        ("mk-marker", test_status_mk_marker_fallback),
        ("csv-out-write", test_csv_out_writes_rows),
        ("csv-out-append", test_csv_out_appends_across_runs),
        ("path-b-lock", test_single_print_path_locks),
        ("no-path-lock", test_neither_path_no_lock),
        ("dual-path-safe", test_either_path_no_double_count),
    ]
    print(f"{'case':<14} result")
    print(f"{'-' * 14} {'-' * 40}")
    for name, fn in tests:
        try:
            detail = fn()
        except AssertionError as exc:
            print(f"{name:<14} FAIL {exc}")
            return 1
        except Exception as exc:
            print(f"{name:<14} ERROR {exc}")
            return 1
        print(f"{name:<14} PASS {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
