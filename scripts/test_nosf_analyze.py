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


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
FIELD_CSV_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "phase_2_12_field_csv.csv")
FIELD_STATE_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "phase_2_12_field_state.json")

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


def write_state_records(path, records):
    with open(path, "w") as fh:
        json.dump({"_schema": 4, "test": records}, fh)


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
        args = SimpleNamespace(inputs=csvs, out=out, mode="safe", state=state, config=config, acceptance_gate=True, commit_watermark=False, keys=None, machine_id="test")
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
        args = SimpleNamespace(inputs=csvs, out=out, mode="safe", state=state, config=config, acceptance_gate=True, commit_watermark=False, keys=None, machine_id="test")
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


def test_refuses_emit_when_zero_locked_in_safe_mode():
    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "run.csv")
        rows = [row(i * 100, feature="Inner_wall", v_fil=1000, est=900) for i in range(200)]
        write_csv(csv_path, rows)
        state = os.path.join(td, "state.json")
        write_state_records(state, {
            "Inner_wall_v1000": {
                "x": 900, "P": 20, "n": 200, "state": "STABLE", "locked": False,
                "resid_var_ewma": 400.0, "cumulative_mid_s": 20,
            }
        })
        config = os.path.join(td, "config.ini")
        write_config(config)
        out = os.path.join(td, "patch.ini")
        args = SimpleNamespace(
            inputs=[csv_path], out=out, mode="safe", state=state, config=config,
            acceptance_gate=False, commit_watermark=False, keys=None, machine_id="test",
            include_stale=False, force=False,
        )
        err = StringIO()
        with redirect_stderr(err):
            rc = analyze.run(args)
        assert rc == 2, rc
        with open(out) as fh:
            text = fh.read()
        assert text.startswith("# REFUSED: no LOCKED buckets"), text
        assert "baseline_rate" in text and "1600 -> 1600" in text, text
        assert "refused: no LOCKED buckets" in err.getvalue(), err.getvalue()
        return "safe mode refuses zero-LOCKED state and writes current-value patch"


def test_warns_emit_when_zero_locked_in_aggressive_mode():
    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "run.csv")
        write_csv(csv_path, [row(i * 100, feature="Inner_wall", v_fil=1000, est=900) for i in range(200)])
        state = os.path.join(td, "state.json")
        write_state_records(state, {
            "Inner_wall_v1000": {
                "x": 900, "P": 20, "n": 200, "state": "STABLE", "locked": False,
                "resid_var_ewma": 400.0, "cumulative_mid_s": 20,
            }
        })
        config = os.path.join(td, "config.ini")
        write_config(config)
        out = os.path.join(td, "patch.ini")
        args = SimpleNamespace(
            inputs=[csv_path], out=out, mode="aggressive", state=state, config=config,
            acceptance_gate=False, commit_watermark=False, keys=None, machine_id="test",
            include_stale=False, force=False,
        )
        err = StringIO()
        with redirect_stderr(err):
            rc = analyze.run(args)
        assert rc == 0, rc
        with open(out) as fh:
            text = fh.read()
        assert text.startswith("# WARNING: zero LOCKED buckets"), text
        assert "(LOW," in text, text
        assert "(HIGH," not in text, text
        assert "warning: zero LOCKED buckets" in err.getvalue(), err.getvalue()
        return "aggressive mode warns and emits low-confidence zero-LOCKED patch"


def test_force_emits_when_zero_locked_in_safe_mode():
    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "run.csv")
        write_csv(csv_path, [row(i * 100, feature="Inner_wall", v_fil=1000, est=900) for i in range(200)])
        state = os.path.join(td, "state.json")
        write_state_records(state, {
            "Inner_wall_v1000": {
                "x": 900, "P": 20, "n": 200, "state": "STABLE", "locked": False,
                "resid_var_ewma": 400.0, "cumulative_mid_s": 20,
            }
        })
        config = os.path.join(td, "config.ini")
        write_config(config)
        out = os.path.join(td, "patch.ini")
        args = SimpleNamespace(
            inputs=[csv_path], out=out, mode="safe", state=state, config=config,
            acceptance_gate=False, commit_watermark=False, keys=None, machine_id="test",
            include_stale=False, force=True,
        )
        rc = analyze.run(args)
        assert rc == 0, rc
        with open(out) as fh:
            text = fh.read()
        assert "--force bypassed locked-bucket floor" in text, text
        assert "REFUSED" not in text, text
        return "--force bypasses zero-LOCKED refusal but keeps warning banner"


def test_confidence_high_requires_5_locked():
    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "run.csv")
        write_csv(csv_path, [row(i * 100, feature="Outer_wall", v_fil=1000, est=1000) for i in range(1200)])
        state = os.path.join(td, "state.json")
        write_state(state, ["Outer_wall_v1000"])
        config = os.path.join(td, "config.ini")
        write_config(config)
        out = os.path.join(td, "patch.ini")
        args = SimpleNamespace(
            inputs=[csv_path], out=out, mode="safe", state=state, config=config,
            acceptance_gate=False, commit_watermark=False, keys=None, machine_id="test",
            include_stale=False, force=False,
        )
        rc = analyze.run(args)
        assert rc == 0, rc
        with open(out) as fh:
            text = fh.read()
        baseline_line = next(line for line in text.splitlines() if "baseline_rate" in line)
        assert "HIGH" not in baseline_line, baseline_line
        return "one LOCKED bucket never earns HIGH confidence"


def test_safety_k_removed_no_subtraction():
    rows = [
        row(0, feature="Outer_wall", v_fil=1000, est=800),
        row(100, feature="Outer_wall", v_fil=1000, est=1200),
    ] * 10
    runs = [{"path": "run1.csv", "rows": rows}]
    state = {"Outer_wall_v1000": {"state": "LOCKED", "locked": True, "x": 1000, "n": 20}}
    recs = analyze.compute_recommendations(rows, runs, state, analyze.DEFAULTS.copy(), "safe")
    baseline = recs["baseline_rate"][0]
    assert baseline == 1000.0, baseline
    assert baseline not in (700.0, 800.0), baseline
    return "baseline is centroid; SAFETY_K subtraction is gone"


def test_precision_weighted_baseline_across_buckets():
    state = {
        "A_v1000": {"state": "LOCKED", "locked": True, "x": 900, "n": 100, "resid_var_ewma": 100},
        "B_v1000": {"state": "LOCKED", "locked": True, "x": 1000, "n": 400, "resid_var_ewma": 100},
        "C_v1000": {"state": "LOCKED", "locked": True, "x": 1100, "n": 400, "resid_var_ewma": 400},
        "D_v1000": {"state": "LOCKED", "locked": True, "x": 1200, "n": 100, "resid_var_ewma": 100},
        "E_v1000": {"state": "LOCKED", "locked": True, "x": 1300, "n": 100, "resid_var_ewma": 100},
    }
    rows = [
        row(i * 100, feature=label.rsplit("_v", 1)[0], v_fil=1000, est=raw["x"])
        for i, (label, raw) in enumerate(state.items())
    ]
    recs = analyze.compute_recommendations(rows, [{"path": "run.csv", "rows": rows}], state, analyze.DEFAULTS.copy(), "safe")
    baseline = recs["baseline_rate"][0]
    assert 1030 <= baseline <= 1070, baseline
    assert baseline not in (900, 1300), baseline
    return "baseline uses trimmed precision-weighted bucket centroids"


def test_dominant_single_bucket_does_not_dictate_baseline():
    state = {
        "Dominant_v1000": {"state": "LOCKED", "locked": True, "x": 1000, "n": 100, "resid_var_ewma": 100},
        "B_v1000": {"state": "LOCKED", "locked": True, "x": 1010, "n": 100, "resid_var_ewma": 100},
        "C_v1000": {"state": "LOCKED", "locked": True, "x": 990, "n": 100, "resid_var_ewma": 100},
        "D_v1000": {"state": "LOCKED", "locked": True, "x": 1005, "n": 100, "resid_var_ewma": 100},
        "E_v1000": {"state": "LOCKED", "locked": True, "x": 995, "n": 100, "resid_var_ewma": 100},
    }
    rows = [row(i * 100, feature="Dominant", v_fil=1000, est=3000) for i in range(200)]
    rows.extend(row(20000 + i * 100, feature=f, v_fil=1000, est=1000) for i, f in enumerate(["B", "C", "D", "E"]))
    recs = analyze.compute_recommendations(rows, [{"path": "run.csv", "rows": rows}], state, analyze.DEFAULTS.copy(), "safe")
    baseline = recs["baseline_rate"][0]
    assert 980 <= baseline <= 1020, baseline
    assert baseline < 1200, baseline
    return "dominant CSV row count no longer dictates baseline"


def test_bias_only_from_qualifying_buckets():
    state = {
        f"Locked{i}_v1000": {"state": "LOCKED", "locked": True, "x": 1000 + i, "n": 100, "resid_var_ewma": 100}
        for i in range(5)
    }
    rows = []
    for i in range(5):
        rows.extend(row(i * 1000 + j * 100, feature=f"Locked{i}", v_fil=1000, est=1000, bp=-3.78, rt=-3.0) for j in range(5))
    rows.extend(row(10000 + j * 100, feature="Unlocked", v_fil=1000, est=1000, bp=-2.22, rt=-3.0) for j in range(100))
    recs = analyze.compute_recommendations(rows, [{"path": "run.csv", "rows": rows}], state, analyze.DEFAULTS.copy(), "safe")
    bias = recs["sync_trailing_bias_frac"][0]
    assert 0.295 <= bias <= 0.305, bias
    return "bias ignores non-qualifying bucket rows"


def test_field_oscillation_repro():
    with open(FIELD_STATE_FIXTURE) as fh:
        state = analyze.load_state(FIELD_STATE_FIXTURE)
    runs, rows = analyze.read_csv_runs([FIELD_CSV_FIXTURE])
    baselines = []
    for extra_count, extra_est in ((0, 0), (80, 1600), (120, 328)):
        synthetic = list(rows)
        synthetic.extend(
            row(2000 + i * 100, feature="Dominant", v_fil=1000, est=extra_est)
            for i in range(extra_count)
        )
        recs = analyze.compute_recommendations(
            synthetic,
            [{"path": "synthetic.csv", "rows": synthetic}],
            state,
            analyze.DEFAULTS.copy(),
            "safe",
            force=True,
        )
        baselines.append(recs["baseline_rate"][0])
    assert max(baselines) - min(baselines) <= 50.0, baselines
    return "field oscillation repro converges within 50 sps across synthetic runs"


def main():
    tests = [
        ("baseline", test_baseline_from_dominant_cluster),
        ("bias-clamp", test_bias_clamped_to_safe_range),
        ("gate-fail", test_acceptance_gate_fail_low_coverage),
        ("gate-pass", test_acceptance_gate_pass_three_runs),
        ("bin-align", test_25_bin_alignment_with_tuner),
        ("safe-refuse", test_refuses_emit_when_zero_locked_in_safe_mode),
        ("aggr-warn", test_warns_emit_when_zero_locked_in_aggressive_mode),
        ("force-emit", test_force_emits_when_zero_locked_in_safe_mode),
        ("conf-locked", test_confidence_high_requires_5_locked),
        ("no-safety-k", test_safety_k_removed_no_subtraction),
        ("weighted", test_precision_weighted_baseline_across_buckets),
        ("no-dominant", test_dominant_single_bucket_does_not_dictate_baseline),
        ("bias-qual", test_bias_only_from_qualifying_buckets),
        ("field-osc", test_field_oscillation_repro),
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
