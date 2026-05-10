#!/usr/bin/env python3
"""Stdlib regression fixture for nosf_analyze.py."""

import csv
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

import nosf_analyze as analyze


FIELDS = [
    "ts_ms", "zone", "bp_mm", "sigma_mm", "est_sps", "rt_mm", "cf",
    "adv_dwell_ms", "tb", "mc", "vb", "bpv_mm", "feature", "v_fil",
]


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            base = {field: "" for field in FIELDS}
            base.update(row)
            writer.writerow(base)


def write_config(path, ref=1.0):
    with open(path, "w") as fh:
        fh.write("baseline_rate: 1600\n")
        fh.write("sync_trailing_bias_frac: 0.4\n")
        fh.write("mid_creep_timeout_ms: 4000\n")
        fh.write("mid_creep_rate_sps_per_s: 5\n")
        fh.write("mid_creep_cap_frac: 10\n")
        fh.write("buf_variance_blend_frac: 0.5\n")
        fh.write(f"buf_variance_blend_ref_mm: {ref}\n")


def write_state(path, locked_labels):
    with open(path, "w") as fh:
        json.dump(
            {
                "_schema": 2,
                "test": {
                    label: {
                        "x": 1600,
                        "P": 20,
                        "n": 250,
                        "bias": 0.4,
                        "state": "LOCKED",
                        "locked": True,
                        "runs_seen": 3,
                        "layers_seen": 3,
                        "cumulative_mid_s": 90,
                    }
                    for label in locked_labels
                },
            },
            fh,
        )


def row(ts, feature="Outer_wall", v_fil=1660, est=1600, bp=-3.0, rt=-3.0, sigma=0.2, mc=0):
    return {
        "ts_ms": str(ts),
        "zone": "MID",
        "bp_mm": str(bp),
        "sigma_mm": str(sigma),
        "est_sps": str(est),
        "rt_mm": str(rt),
        "cf": "0.95",
        "adv_dwell_ms": "0",
        "tb": "40",
        "mc": str(mc),
        "vb": "50",
        "bpv_mm": str(bp),
        "feature": feature,
        "v_fil": str(v_fil),
    }


def test_baseline_from_dominant_cluster():
    rows = [row(i * 100, est=1500 + (i % 5)) for i in range(30)]
    runs = [{"path": "run1.csv", "rows": rows}]
    recs = analyze.compute_recommendations(rows, runs, {}, analyze.DEFAULTS.copy(), "safe")
    baseline = recs["baseline_rate"][0]
    assert 1498 <= baseline <= 1503, baseline
    return "baseline derives from dominant MID cluster"


def test_bias_clamped_to_safe_range():
    rows = [row(i * 100, bp=10.0, rt=-7.8) for i in range(20)]
    runs = [{"path": "run1.csv", "rows": rows}]
    recs = analyze.compute_recommendations(rows, runs, {}, analyze.DEFAULTS.copy(), "safe")
    bias = recs["sync_trailing_bias_frac"][0]
    assert bias == analyze.BIAS_SAFE_MAX, bias
    return "bias recommendation clamps to safe max"


def test_acceptance_gate_fail_low_coverage():
    with tempfile.TemporaryDirectory() as td:
        csvs = []
        for idx in range(3):
            path = os.path.join(td, f"run{idx}.csv")
            write_csv(path, [row(0, feature="Unlocked", v_fil=100), row(601000, feature="Unlocked", v_fil=100)])
            csvs.append(path)
        state = os.path.join(td, "state.json")
        write_state(state, ["Locked_v100", "Locked_v125", "Locked_v150"])
        config = os.path.join(td, "config.ini")
        write_config(config)
        out = os.path.join(td, "patch.ini")
        args = SimpleNamespace(inputs=csvs, out=out, mode="safe", state=state, config=config, acceptance_gate=True)
        err = StringIO()
        with redirect_stderr(err):
            rc = analyze.run(args)
        assert rc == 1, rc
        assert "coverage" in err.getvalue(), err.getvalue()
        with open(out) as fh:
            text = fh.read()
        assert "Acceptance gate: FAIL" in text, text
        return "acceptance gate fails with explicit low-coverage reason"


def test_acceptance_gate_pass_three_runs():
    with tempfile.TemporaryDirectory() as td:
        labels = ["Outer_wall_v100", "Inner_wall_v125", "Sparse_infill_v150"]
        csvs = []
        features = [("Outer_wall", 100), ("Inner_wall", 125), ("Sparse_infill", 150)]
        for idx in range(3):
            path = os.path.join(td, f"run{idx}.csv")
            rows = []
            for feature, v_fil in features:
                rows.append(row(0, feature=feature, v_fil=v_fil, est=1500 + idx))
                rows.append(row(601000, feature=feature, v_fil=v_fil, est=1501 + idx))
            write_csv(path, rows)
            csvs.append(path)
        state = os.path.join(td, "state.json")
        write_state(state, labels)
        config = os.path.join(td, "config.ini")
        write_config(config)
        out = os.path.join(td, "patch.ini")
        args = SimpleNamespace(inputs=csvs, out=out, mode="safe", state=state, config=config, acceptance_gate=True)
        rc = analyze.run(args)
        assert rc == 0, rc
        with open(out) as fh:
            text = fh.read()
        assert "Acceptance gate: PASS" in text, text
        assert "# baseline_rate" in text, text
        return "acceptance gate passes three covered calibration runs"


def test_25_bin_alignment_with_tuner():
    assert analyze.bin_v_fil(40) == 50
    assert analyze.bin_v_fil(1660) == 1650
    return "analyzer uses 25 mm3/s bucket bins"


def main():
    tests = [
        ("baseline", test_baseline_from_dominant_cluster),
        ("bias-clamp", test_bias_clamped_to_safe_range),
        ("gate-fail", test_acceptance_gate_fail_low_coverage),
        ("gate-pass", test_acceptance_gate_pass_three_runs),
        ("bin-align", test_25_bin_alignment_with_tuner),
    ]
    print(f"{'case':<12} result")
    print(f"{'-' * 12} {'-' * 40}")
    for name, fn in tests:
        try:
            detail = fn()
        except AssertionError as exc:
            print(f"{name:<12} FAIL {exc}")
            return 1
        except Exception as exc:
            print(f"{name:<12} ERROR {exc}")
            return 1
        print(f"{name:<12} PASS {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
