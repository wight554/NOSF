#!/usr/bin/env python3
"""Stdlib regression fixture for nosf_live_tuner.py."""

import json
import os
import sys
import tempfile

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
        assert t.active_label == "Outer_wall_v40"
        assert not fake.writes
        return "firmware MK status marker seeds active bucket"


def main():
    tests = [
        ("warm-up", test_cold_start_no_set),
        ("locked", test_locked_warm_start_zero_sets),
        ("adv-risk", test_adv_risk_freeze_and_rollback),
        ("halt", test_adv_dwell_stop_halts),
        ("rate-limit", test_rate_limit_three_sets_per_window),
        ("baseline-off", test_baseline_writes_disabled_by_default),
        ("idle-arm", test_commit_idle_requires_activity),
        ("mk-marker", test_status_mk_marker_fallback),
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
