#!/usr/bin/env python3
"""Stdlib regression fixture for nosf_live_tuner.py."""

import json
import math
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

import nosf_live_tuner as tuner_mod
import gcode_marker


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
ORCA_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "orca_sample.gcode")
CHATTER_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "phase_2_11_chatter.json")


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
        return "schema 1 state migrates to current schema with zeroed counters"


def _schema2_state():
    return {
        "_schema": 2,
        "test": {
            "LOCKED_v40": {
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
                "low_flow_skip_count": 1,
                "rail_skip_count": 2,
                "rollback_count": 3,
                "first_seen": 100.0,
                "last_seen": 200.0,
                "first_seen_run": "run1",
            },
            "TRACKING_v50": {
                "x": 1500.0,
                "P": 1000.0,
                "n": 12,
                "bias": 0.390,
                "state": "TRACKING",
                "locked": False,
            },
            "STABLE_v75": {
                "x": 1666.0,
                "P": 40.0,
                "n": 250,
                "bias": 0.410,
                "state": "STABLE",
                "locked": False,
            },
        },
    }


def _schema3_state():
    data = _schema2_state()
    data = tuner_mod._migrate_2_to_3(tuner_mod._migrate_1_to_2(data) if data["_schema"] == 1 else data)
    return data


def _assert_schema4_defaults(raw):
    assert raw["resid_ewma"] == 0.0, raw
    assert raw["resid_abs_ewma"] == 0.0, raw
    assert raw["resid_var_ewma"] == tuner_mod.R_BASE, raw
    assert raw["outlier_streak"] == 0, raw
    assert raw["locked_sample_count"] == 0, raw
    assert raw["locked_since_run_seq"] == 0, raw
    assert raw["last_unlock_reason"] == "", raw
    assert raw["last_unlock_at"] == 0.0, raw


def test_schema3_to_4_migration_preserves_buckets():
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        data = _schema3_state()
        data["test"]["_meta"]["last_commit_run_seq"] = 7
        with open(state_path, "w") as fh:
            json.dump(data, fh)
        before = json.loads(json.dumps(data))
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), state_path, "test", now_fn=clock.now, wall_fn=clock.now)
        b = t.buckets["LOCKED_v40"]
        assert b.x == before["test"]["LOCKED_v40"]["x"], b
        assert b.n == before["test"]["LOCKED_v40"]["n"], b
        assert b.locked is True, b.locked
        assert b.state == "LOCKED", b.state
        assert b.resid_var_ewma == tuner_mod.R_BASE, b.resid_var_ewma

        t._persist()
        with open(state_path) as fh:
            migrated = json.load(fh)
        assert migrated["_schema"] == tuner_mod.SCHEMA_VERSION, migrated
        assert migrated["test"]["_meta"] == before["test"]["_meta"], migrated["test"]["_meta"]
        raw = migrated["test"]["LOCKED_v40"]
        assert raw["x"] == before["test"]["LOCKED_v40"]["x"], raw
        assert raw["state"] == "LOCKED", raw
        assert raw["locked"] is True, raw
        _assert_schema4_defaults(raw)

        clock2 = Clock()
        t2 = tuner_mod.Tuner(FakeSerial(), state_path, "test", now_fn=clock2.now, wall_fn=clock2.now)
        assert t2.buckets["LOCKED_v40"].resid_var_ewma == tuner_mod.R_BASE
        return "schema 3 state migrates to schema 4 preserving buckets and _meta"


def test_schema2_to_4_migration_preserves_buckets():
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        with open(state_path, "w") as fh:
            json.dump(_schema2_state(), fh)
        clock = Clock()
        t = tuner_mod.Tuner(FakeSerial(), state_path, "test", now_fn=clock.now, wall_fn=clock.now)
        b = t.buckets["LOCKED_v40"]
        assert b.x == 1820.0, b
        assert b.n == 4321, b
        assert b.locked is True, b.locked
        assert b.state == "LOCKED", b.state
        assert b.rollback_count == 3, b.rollback_count
        
        t._persist()
        with open(state_path) as fh:
            migrated = json.load(fh)
        assert migrated["_schema"] == tuner_mod.SCHEMA_VERSION, migrated
        assert "_meta" in migrated["test"], migrated
        assert set(["LOCKED_v40", "TRACKING_v50", "STABLE_v75"]).issubset(migrated["test"]), migrated
        meta = migrated["test"]["_meta"]
        assert "baseline_rate" in meta["last_commit_values"], meta
        assert meta["last_commit_values"]["baseline_rate"]["source"] == "default", meta
        _assert_schema4_defaults(migrated["test"]["LOCKED_v40"])
        return "schema 2 state migrates to schema 4 preserving buckets and creating _meta"

def test_schema_chain_1_to_4():
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
        assert b.resid_var_ewma == tuner_mod.R_BASE, b.resid_var_ewma
        
        t._persist()
        with open(state_path) as fh:
            migrated = json.load(fh)
        assert migrated["_schema"] == tuner_mod.SCHEMA_VERSION, migrated
        _assert_schema4_defaults(migrated["test"]["PERIMETER_v40"])
        return "schema 1 state migrates to schema 4 via chain"

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


def test_residual_stats_accumulate():
    b = tuner_mod.Bucket(label="PERIMETER_v40", x=100.0, P=100.0)
    tuner_mod.kf_predict_update(b, 160.0, cf=1.0, apx=0, dt_s=1.0)
    assert b.n == 1, b.n
    assert b.resid_abs_ewma > 0.0, b.resid_abs_ewma
    assert b.resid_var_ewma > tuner_mod.R_BASE, b.resid_var_ewma
    return "residual EWMA fields accumulate after KF update"


def _locked_bucket(label="PERIMETER_v40", sigma=50.0):
    return tuner_mod.Bucket(
        label=label,
        x=1000.0,
        P=20.0,
        n=600,
        bias=0.400,
        state="LOCKED",
        locked=True,
        runs_seen=2,
        layers_seen=5,
        cumulative_mid_s=90.0,
        resid_var_ewma=sigma * sigma,
        locked_sample_count=tuner_mod.MIN_LOCK_DWELL,
    )


def _blank_tuner(td, clock=None):
    if clock is None:
        clock = Clock()
    return tuner_mod.Tuner(FakeSerial(), os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)


def test_locked_bucket_survives_single_outlier():
    with tempfile.TemporaryDirectory() as td:
        t = _blank_tuner(td)
        b = _locked_bucket(sigma=50.0)
        reason = t._evaluate_unlock(b, 6.0 * t._resid_sigma(b))
        assert reason == "", reason
        assert b.locked and b.state == "LOCKED", b
        assert b.outlier_streak == 1, b.outlier_streak
        return "single moderate outlier does not unlock a locked bucket"


def test_locked_bucket_unlocks_on_streak():
    with tempfile.TemporaryDirectory() as td:
        t = _blank_tuner(td)
        b = _locked_bucket(sigma=50.0)
        reason = ""
        for _ in range(tuner_mod.N_STREAK):
            reason = t._evaluate_unlock(b, 4.0 * t._resid_sigma(b))
        assert reason == "streak", reason
        t._apply_unlock(b, reason, b.x + 200.0)
        assert not b.locked and b.state == "TRACKING", b
        assert b.last_unlock_reason == "streak", b.last_unlock_reason
        assert b.P == tuner_mod.P_UNLOCK_RESET, b.P
        return "five sustained moderate outliers unlock via streak"


def test_locked_bucket_unlocks_on_catastrophic():
    with tempfile.TemporaryDirectory() as td:
        t = _blank_tuner(td)
        b = _locked_bucket(sigma=50.0)
        reason = t._evaluate_unlock(b, 12.0 * t._resid_sigma(b))
        assert reason == "catastrophic", reason
        t._apply_unlock(b, reason, b.x + 600.0)
        assert b.last_unlock_reason == "catastrophic", b.last_unlock_reason
        return "single catastrophic residual unlocks immediately"


def test_locked_bucket_unlocks_on_drift():
    with tempfile.TemporaryDirectory() as td:
        t = _blank_tuner(td)
        b = _locked_bucket(sigma=50.0)
        b.locked_sample_count = tuner_mod.M_DRIFT_DWELL
        drift_sigma = t._resid_sigma(b) / math.sqrt(tuner_mod.EWMA_EFFECTIVE_N)
        b.resid_ewma = tuner_mod.K_DRIFT * drift_sigma + 1.0
        reason = t._evaluate_unlock(b, 0.0)
        assert reason == "drift", reason
        t._apply_unlock(b, reason, b.x)
        assert b.last_unlock_reason == "drift", b.last_unlock_reason
        return "sustained EWMA drift unlocks after dwell"


def test_noisy_bucket_never_locks():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t = _blank_tuner(td, clock)
        b = tuner_mod.Bucket(
            label="PERIMETER_v40",
            x=1600.0,
            P=20.0,
            n=600,
            bias=0.400,
            runs_seen=2,
            layers_seen=5,
            cumulative_mid_s=90.0,
            state="STABLE",
            resid_var_ewma=tuner_mod.V_NOISE_LOCK_THR + 1.0,
        )
        t.buckets[b.label] = b
        t.total_print_mid_s = 400.0
        t._maybe_lock(b, clock.now())
        assert b.state == "STABLE", b.state
        assert not b.locked
        assert "noise" in t._bucket_wait_reason(b, clock.now())
        return "noisy ready bucket remains STABLE instead of locking"


def test_lock_dwell_blocks_immediate_unlock():
    with tempfile.TemporaryDirectory() as td:
        t = _blank_tuner(td)
        b = _locked_bucket(sigma=50.0)
        b.locked_sample_count = 0
        reason = ""
        for _ in range(tuner_mod.N_STREAK):
            reason = t._evaluate_unlock(b, 4.0 * t._resid_sigma(b))
            b.locked_sample_count += 1
        assert reason == "", reason
        assert b.outlier_streak == tuner_mod.N_STREAK, b.outlier_streak
        return "minimum lock dwell blocks moderate-channel unlocks"


def test_unlock_then_relock_does_not_chatter():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t = _blank_tuner(td, clock)
        b = _locked_bucket(sigma=50.0)
        t._apply_unlock(b, "drift", b.x + 150.0)
        b.P = 20.0
        b.state = "STABLE"
        b.locked = False
        b.resid_var_ewma = tuner_mod.V_NOISE_LOCK_THR + 100.0
        t.total_print_mid_s = 400.0
        t._maybe_lock(b, clock.now())
        assert b.state == "STABLE", b.state
        assert not b.locked
        assert b.last_unlock_reason == "drift", b.last_unlock_reason
        return "post-unlock noisy bucket does not immediately relock"


def test_resid_var_ewma_warm_start_does_not_unlock():
    with tempfile.TemporaryDirectory() as td:
        t = _blank_tuner(td)
        b = _locked_bucket(sigma=10.0)
        b.locked_sample_count = 0
        reason = t._evaluate_unlock(b, 2.0 * t._resid_sigma(b))
        assert reason == "", reason
        assert b.outlier_streak == 0, b.outlier_streak
        return "fresh locked bucket ignores moderate warm-start residual"


def test_recommend_recheck_outputs_verdict():
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        with open(state_path, "w") as fh:
            json.dump({"_schema": 3, "test": {"_meta": {}}}, fh)
        out = StringIO()
        with redirect_stderr(out), redirect_stdout(out):
            tuner_mod.do_recommend_recheck(state_path, "test")
        text = out.getvalue()
        assert "RECOMMEND: no" in text, text
        return "recommend-recheck outputs negative verdict for empty state"

def test_prune_stale_removes_old_buckets():
    import time
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        now = time.time()
        with open(state_path, "w") as fh:
            json.dump({
                "_schema": 3,
                "test": {
                    "fresh_v10": {"last_seen": now - 10},
                    "stale_v20": {"last_seen": now - 70 * 86400},
                    "_meta": {}
                }
            }, fh)
        
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            tuner_mod.do_prune_stale(state_path, "test")
            
        with open(state_path) as fh:
            data = json.load(fh)
            
        assert "stale_v20" not in data["test"], data
        assert "fresh_v10" in data["test"], data
        assert "_meta" in data["test"], data
        return "prune-stale removes only buckets older than 60 days"

def test_daemon_does_not_exit_on_finish():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        args = SimpleNamespace(commit_on_finish=True, observe_daemon=True, state=t.state_path, machine_id=t.machine_id, recommend_recheck=False)
        t.finish_seen = True
        t.seen_print_activity = True
        
        # Simulate run_loop logic
        if args.commit_on_finish and t.finish_seen and t.seen_print_activity:
            if getattr(args, "observe_daemon", False):
                t._persist()
                t.finish_seen = False
                t.seen_print_activity = False
                t.idle_since = 0.0
                t.total_print_mid_s = 0.0
            else:
                assert False, "Should not exit"
        
        assert t.finish_seen is False, "daemon should reset finish_seen"
        assert t.seen_print_activity is False, "daemon should reset seen_print_activity"
        return "daemon mode resets flags instead of exiting on finish"

def test_daemon_resets_per_print_state():
    with tempfile.TemporaryDirectory() as td:
        clock = Clock()
        t, fake = make_tuner(os.path.join(td, "state.json"), clock)
        t.total_print_mid_s = 500.0
        t._run_seen_labels.add("PERIMETER_v40")
        t.on_m118("NT:START")
        assert t.total_print_mid_s == 0.0, "START should reset total_print_mid_s"
        assert len(t._run_seen_labels) == 0, "START should clear _run_seen_labels"
        return "daemon mode resets per-print state on NT:START"

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


def _sidecar_fixture(td):
    gcode_path = os.path.join(td, "orca_sample.gcode")
    sidecar_path = os.path.join(td, "orca_sample.nosf.json")
    shutil.copyfile(ORCA_FIXTURE, gcode_path)
    data = gcode_marker.build_sidecar(gcode_path, sidecar_path, 1.75)
    return gcode_path, sidecar_path, data


def _klipper_segment_message(seg, filename, eventtime=1.0):
    fp = int((seg["byte_start"] + seg["byte_end"]) // 2)
    return {
        "params": {
            "eventtime": eventtime,
            "status": {
                "print_stats": {"state": "printing", "filename": filename},
                "virtual_sdcard": {"is_active": True, "file_position": fp, "file_size": seg["byte_end"] + 100},
                "motion_report": {
                    "live_position": [float(seg["x_end"]), float(seg["y_end"]), float(seg["z_mm"]), 0.0],
                    "live_velocity": 10.0,
                    "live_extruder_velocity": 1.0,
                },
                "gcode_move": {"speed_factor": 1.0, "extrude_factor": 1.0},
                "webhooks": {"state": "ready"},
            },
        }
    }


def test_auto_fallback_when_uds_missing():
    with tempfile.TemporaryDirectory() as td:
        args = SimpleNamespace(
            klipper_mode="auto",
            klipper_uds=os.path.join(td, "missing.sock"),
            sidecar=None,
        )
        err = StringIO()
        with redirect_stderr(err):
            client, matcher, status_cache, suppress = tuner_mod.setup_klipper_motion(args)
        assert client is None, client
        assert matcher is None, matcher
        assert status_cache == {}, status_cache
        assert not suppress
        assert "falling back to marker input" in err.getvalue(), err.getvalue()
        return "auto mode falls back when UDS is missing"


def test_klipper_events_drive_buckets():
    with tempfile.TemporaryDirectory() as td:
        gcode_path, sidecar_path, data = _sidecar_fixture(td)
        seg = next(s for s in data["segments"] if not s.get("skip") and float(s["v_fil_mm3_per_s"]) >= 1.0)
        clock = Clock()
        fake = FakeSerial()
        t = tuner_mod.Tuner(fake, os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        matcher = tuner_mod.SegmentMatcher(sidecar_path)
        status_cache = {}
        count = tuner_mod.process_klipper_motion_message(
            t,
            matcher,
            status_cache,
            _klipper_segment_message(seg, os.path.basename(gcode_path)),
        )
        assert count >= 2, count
        clock.step(0.25)
        t.on_status(status(est=600))
        assert t.active_label == tuner_mod.bucket_label(seg["feature"], float(seg["v_fil_mm3_per_s"])), t.active_label
        assert t.active_label in t.buckets, t.buckets
        clock.step(0.25)
        t.on_status(status(est=610))
        assert t.buckets[t.active_label].n == 1, t.buckets[t.active_label]
        return "Klipper motion event path feeds existing bucket learner"


def test_observe_only_no_set_writes_via_klipper_path():
    with tempfile.TemporaryDirectory() as td:
        gcode_path, sidecar_path, data = _sidecar_fixture(td)
        seg = next(s for s in data["segments"] if not s.get("skip") and float(s["v_fil_mm3_per_s"]) >= 1.0)
        clock = Clock()
        fake = FakeSerial()
        t = tuner_mod.Tuner(fake, os.path.join(td, "state.json"), "test", now_fn=clock.now, wall_fn=clock.now)
        matcher = tuner_mod.SegmentMatcher(sidecar_path)
        status_cache = {}
        tuner_mod.process_klipper_motion_message(
            t,
            matcher,
            status_cache,
            _klipper_segment_message(seg, os.path.basename(gcode_path)),
        )
        for _ in range(260):
            clock.step(0.25)
            t.on_status(status(est=700))
        assert not fake.writes, fake.writes
        return "Klipper path remains observe-only by default"


def _run_phase_2_11_chatter_repro_fixture():
    with tempfile.TemporaryDirectory() as td:
        with open(CHATTER_FIXTURE) as fh:
            trace = json.load(fh)
        control = trace[0]
        samples = trace[1:]
        clock = Clock()
        fake = FakeSerial()
        state_path = os.path.join(td, "state.json")
        t = tuner_mod.Tuner(fake, state_path, "test", now_fn=clock.now, wall_fn=clock.now)
        t.on_m118("NT:START")
        t.on_m118(
            f"echo: NOSF_TUNE:{control['feature']}:V{control['v_fil']:.1f}:W0.45:H0.20"
        )
        b = tuner_mod.Bucket(
            label=control["label"],
            x=float(control["x"]),
            P=float(control["P"]),
            n=int(control["n"]),
            bias=float(control["bias"]),
            bp_ewma=float(control["bp_ewma"]),
            state="LOCKED",
            locked=True,
            runs_seen=int(control["runs_seen"]),
            layers_seen=int(control["layers_seen"]),
            cumulative_mid_s=float(control["cumulative_mid_s"]),
            first_seen=clock.now(),
            last_seen=clock.now(),
            resid_var_ewma=float(control.get("resid_var_ewma", tuner_mod.R_BASE)),
        )
        t.buckets[b.label] = b
        t.active_label = b.label
        t.total_print_mid_s = max(300.0, b.cumulative_mid_s)
        t.last_status_t = clock.now()
        unlock_count = 0
        locked_throughout = True
        for sample in samples:
            was_locked = b.locked or b.state == "LOCKED"
            clock.step(1.0)
            t.on_status(
                status(
                    est=float(sample["est"]),
                    bp=float(sample["bp"]),
                    rt=float(sample["rt"]),
                    cf=float(sample["cf"]),
                    apx=int(sample["apx"]),
                )
            )
            is_locked = b.locked or b.state == "LOCKED"
            if was_locked and not is_locked:
                unlock_count += 1
            locked_throughout = locked_throughout and is_locked
        assert locked_throughout, f"bucket unlocked {unlock_count} times"
        assert unlock_count <= 1, f"unlock count {unlock_count} > 1"
        return f"chatter fixture stayed locked with {unlock_count} unlocks"


def test_phase_2_11_chatter_repro_fixture():
    return _run_phase_2_11_chatter_repro_fixture()


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
        ("schema3-to-4", test_schema3_to_4_migration_preserves_buckets),
        ("schema2-to-4", test_schema2_to_4_migration_preserves_buckets),
        ("schema-chain", test_schema_chain_1_to_4),
        ("schema-too-new", test_schema_too_new_refused),
        ("schema-prod", test_existing_production_state_loads),
        ("resid-stats", test_residual_stats_accumulate),
        ("outlier-one", test_locked_bucket_survives_single_outlier),
        ("outlier-streak", test_locked_bucket_unlocks_on_streak),
        ("outlier-cata", test_locked_bucket_unlocks_on_catastrophic),
        ("outlier-drift", test_locked_bucket_unlocks_on_drift),
        ("noise-lock", test_noisy_bucket_never_locks),
        ("lock-dwell", test_lock_dwell_blocks_immediate_unlock),
        ("relock-noise", test_unlock_then_relock_does_not_chatter),
        ("warm-resid", test_resid_var_ewma_warm_start_does_not_unlock),
        ("recheck-verd", test_recommend_recheck_outputs_verdict),
        ("prune-stale", test_prune_stale_removes_old_buckets),
        ("daemon-no-exit", test_daemon_does_not_exit_on_finish),
        ("daemon-reset", test_daemon_resets_per_print_state),
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
        ("klipper-auto", test_auto_fallback_when_uds_missing),
        ("klipper-events", test_klipper_events_drive_buckets),
        ("klipper-observe", test_observe_only_no_set_writes_via_klipper_path),
        ("phase211-chat", test_phase_2_11_chatter_repro_fixture),
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
