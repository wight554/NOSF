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
PHASE_2_13_STATE_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "phase_2_13_three_run_state.json")
PHASE_2_13_RUN_FIXTURES = [
    os.path.join(REPO_ROOT, "tests", "fixtures", "phase_2_13_run_a.csv"),
    os.path.join(REPO_ROOT, "tests", "fixtures", "phase_2_13_run_b.csv"),
    os.path.join(REPO_ROOT, "tests", "fixtures", "phase_2_13_run_c.csv"),
]

PHASE_2_14_DILUTED_STATE = os.path.join(REPO_ROOT, "tests/fixtures/phase_2_14_diluted_state.json")
PHASE_2_14_HIGH_SIGMA_A = os.path.join(REPO_ROOT, "tests/fixtures/phase_2_14_high_sigma_run_a.csv")
PHASE_2_14_HIGH_SIGMA_B = os.path.join(REPO_ROOT, "tests/fixtures/phase_2_14_high_sigma_run_b.csv")

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


def write_tuner_csv(path, rows):
    fields = ["wall_ts", "BUF", "BP", "BPV", "BL", "EST", "RT", "feature", "v_fil"]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row_data in rows:
            base = {field: "" for field in fields}
            base.update(row_data)
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


def test_acceptance_gate_warns_low_raw_coverage():
    with tempfile.TemporaryDirectory() as td:
        csvs = []
        for idx in range(3):
            path = os.path.join(td, f"run{idx}.csv")
            rows = [row(i * 100, feature="LockedA", v_fil=1000) for i in range(100)]
            rows.append(row(601000, feature="Unlocked", v_fil=100))
            write_csv(path, rows)
            csvs.append(path)
        state = os.path.join(td, "state.json")
        write_state(state, ["LockedA_v1000", "LockedB_v1000", "LockedC_v1000"])
        config = os.path.join(td, "config.ini")
        write_config(config)
        out = os.path.join(td, "patch.ini")
        args = SimpleNamespace(inputs=csvs, out=out, mode="safe", state=state, config=config, acceptance_gate=True, commit_watermark=False, keys=None, machine_id="test")
        err = StringIO()
        with redirect_stderr(err):
            rc = analyze.run(args)
        assert rc == 0, (rc, err.getvalue())
        with open(out) as fh:
            text = fh.read()
        assert "Acceptance gate: PASS" in text, text
        assert "raw MID coverage" in text, text
        assert "Failure reasons" not in text, text
        return "low raw MID coverage warns but does not fail acceptance"


def test_acceptance_gate_pass_three_runs():
    with tempfile.TemporaryDirectory() as td:
        labels = ["Outer_wall_v100", "Inner_wall_v125", "Sparse_infill_v150"]
        csvs = []
        features = [("Outer_wall", 100), ("Inner_wall", 125), ("Sparse_infill", 150)]
        for idx in range(3):
            path = os.path.join(td, f"run{idx}.csv")
            rows = []
            for feature, v_fil in features:
                for i in range(60):
                    rows.append(row(i * 100, feature=feature, v_fil=v_fil, est=1500 + idx))
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


def test_buf_variance_blend_ref_mm_from_bp_not_bl():
    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "run.csv")
        labels = [f"Locked{i}" for i in range(5)]
        rows = []
        for idx, feature in enumerate(labels):
            for j in range(60):
                bp = [-0.5, 0.0, 0.5][j % 3] + idx * 0.01
                rows.append({
                    "wall_ts": str((idx * 1000 + j) / 10.0),
                    "BUF": "MID",
                    "BP": f"{bp:.3f}",
                    "BPV": "0.1",
                    "BL": "707.5",
                    "EST": "1000",
                    "RT": "0.0",
                    "feature": feature,
                    "v_fil": "1000",
                })
        write_tuner_csv(csv_path, rows)
        _runs, normalized = analyze.read_csv_runs([csv_path])
        state = {
            f"{feature}_v1000": {
                "state": "LOCKED", "locked": True, "x": 1000, "n": 60,
                "resid_var_ewma": 100, "cumulative_mid_s": 20,
            }
            for feature in labels
        }
        recs = analyze.compute_recommendations(normalized, [{"path": csv_path, "rows": normalized}], state, analyze.DEFAULTS.copy(), "safe")
        var_ref = recs["buf_variance_blend_ref_mm"][0]
        assert 0.1 <= var_ref <= 5.0, var_ref
        assert abs(var_ref - 0.5) < 0.001, var_ref
        assert var_ref != 707.5, var_ref
        return "variance blend reference derives from BP scatter, not BL baseline"


def test_mid_creep_timeout_default_when_insufficient_data():
    rows = []
    for i in range(100):
        zone = "MID" if i % 4 else "LOW"
        rows.append({
            "ts_ms": str(i * 100),
            "zone": zone,
            "bp_mm": "-3.0",
            "sigma_mm": "0.2",
            "est_sps": "1000",
            "rt_mm": "-3.0",
            "cf": "0.95",
            "adv_dwell_ms": "0",
            "tb": "40",
            "mc": "0",
            "vb": "50",
            "bpv_mm": "-3.0",
            "feature": "Outer_wall",
            "v_fil": "1000",
        })
    runs = [{"path": "run.csv", "rows": rows}]
    state = {"Outer_wall_v1000": {"state": "LOCKED", "locked": True, "x": 1000, "n": 100, "resid_var_ewma": 100}}
    current = analyze.DEFAULTS.copy()
    current["mid_creep_timeout_ms"] = 4321
    recs = analyze.compute_recommendations(rows, runs, state, current, "safe")
    value, conf, detail = recs["mid_creep_timeout_ms"]
    assert value == 4321, value
    assert conf == "DEFAULT", conf
    assert "deferred" in detail, detail
    return "mid_creep_timeout_ms stays current/default until a valid signal exists"


def test_contributors_block_emitted():
    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "run.csv")
        rows = []
        records = {}
        for i in range(5):
            feature = f"Locked{i}"
            label = f"{feature}_v1000"
            records[label] = {
                "state": "LOCKED",
                "locked": True,
                "x": 1000 + i * 5,
                "n": 100 + i,
                "resid_var_ewma": 100 + i * 10,
                "cumulative_mid_s": 20,
            }
            rows.extend(row(i * 1000 + j * 100, feature=feature, v_fil=1000, est=1000 + i * 5) for j in range(10))
        write_csv(csv_path, rows)
        state = os.path.join(td, "state.json")
        write_state_records(state, records)
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
        assert "[nosf_contributors]" in text, text
        assert "# baseline_rate" in text and "total n=" in text, text
        assert "sigma/x=" in text and "w=" in text, text
        return "patch includes contributors block with bucket weights"


def test_phase_2_13_field_repro_gate_should_pass():
    state = analyze.load_state(PHASE_2_13_STATE_FIXTURE)
    baselines = []
    biases = []
    for path in PHASE_2_13_RUN_FIXTURES:
        runs, rows = analyze.read_csv_runs([path])
        recs = analyze.compute_recommendations(rows, runs, state, analyze.DEFAULTS.copy(), "safe")
        baselines.append(recs["baseline_rate"][0])
        biases.append(recs["sync_trailing_bias_frac"][0])
    assert all(700 <= value <= 730 for value in baselines), baselines
    assert max(baselines) - min(baselines) <= 50.0, baselines
    assert max(biases) - min(biases) <= 0.05, biases

    runs, rows = analyze.read_csv_runs(PHASE_2_13_RUN_FIXTURES)
    gate = analyze.acceptance_gate(rows, runs, state, analyze.DEFAULTS.copy())
    # This NOW fails because the field fixture has very few rows, thus 0 comparable runs.
    assert not gate["pass"], "Expected parity fixture to FAIL comparable check"
    assert any("comparable run count 0 < 2" in r for r in gate["reasons"]), gate["reasons"]
    return "phase 2.13 fixture fails comparable check as expected"


def test_consistency_uses_recommendation_path():
    state = analyze.load_state(PHASE_2_13_STATE_FIXTURE)
    runs, _rows = analyze.read_csv_runs(PHASE_2_13_RUN_FIXTURES)
    raw_baseline_delta, _raw_bias_delta = analyze.raw_consistency_by_run(runs)
    baseline_delta, bias_delta = analyze.consistency_by_run(runs, state, analyze.DEFAULTS.copy(), "safe")
    assert raw_baseline_delta >= 600.0, raw_baseline_delta
    assert baseline_delta <= 50.0, baseline_delta
    assert bias_delta <= 0.05, bias_delta
    return "consistency check ignores raw per-bucket scatter and uses recommendations"


def test_consistency_matches_standalone_recommendations():
    state = analyze.load_state(PHASE_2_13_STATE_FIXTURE)
    runs, _rows = analyze.read_csv_runs(PHASE_2_13_RUN_FIXTURES)
    standalone_baselines = []
    standalone_biases = []
    for run in runs:
        recs = analyze.compute_recommendations(run["rows"], [run], state, analyze.DEFAULTS.copy(), "safe")
        standalone_baselines.append(recs["baseline_rate"][0])
        standalone_biases.append(recs["sync_trailing_bias_frac"][0])
    baseline_delta, bias_delta = analyze.consistency_by_run(runs, state, analyze.DEFAULTS.copy(), "safe")
    assert baseline_delta == max(standalone_baselines) - min(standalone_baselines), baseline_delta
    assert bias_delta == max(standalone_biases) - min(standalone_biases), bias_delta
    return "consistency deltas match standalone per-run recommendations"


def phase_2_13_state_records():
    return {
        f"Locked{letter}_v1000": {
            "state": "LOCKED",
            "locked": True,
            "x": x,
            "n": 500,
            "resid_var_ewma": 10000.0,
            "cumulative_mid_s": 120,
        }
        for letter, x in zip("ABCDE", [700, 710, 720, 730, 740])
    }


def phase_2_13_state_with_nonlocked_mass(extra_count, extra_n):
    records = phase_2_13_state_records()
    for idx in range(extra_count):
        records[f"Stable{idx}_v1000"] = {
            "state": "STABLE",
            "locked": False,
            "x": 700 + idx,
            "n": extra_n,
            "resid_var_ewma": 10000.0,
            "cumulative_mid_s": 120,
        }
    return records


def phase_2_13_comparable_run(path, bp_delta=-0.756):
    rows = []
    for i in range(50):
        ts = int(i * (601000 / 49))
        rows.append(row(ts, feature="LockedC", v_fil=1000, est=720, bp=-3.0 + bp_delta, rt=-3.0))
    return {"path": path, "rows": rows}


def phase_2_13_three_comparable_runs():
    return [
        phase_2_13_comparable_run("run-a.csv"),
        phase_2_13_comparable_run("run-b.csv"),
        phase_2_13_comparable_run("run-c.csv"),
    ]


def test_immature_run_skipped_with_reason():
    state = analyze.load_state(PHASE_2_13_STATE_FIXTURE)
    runs, _rows = analyze.read_csv_runs(PHASE_2_13_RUN_FIXTURES)
    info = analyze.classify_run(runs[0], state, analyze.DEFAULTS.copy(), "safe", False, False)
    assert not info["comparable"], info
    assert "rows per contributing bucket" in info["reason"], info
    return "immature run is skipped with row-count reason"


def test_only_one_comparable_run_skips_consistency_check():
    state = phase_2_13_state_records()
    comparable = phase_2_13_comparable_run("run-good.csv")
    immature = {"path": "run-short.csv", "rows": [row(0, feature="LockedC", v_fil=1000, est=720)]}
    report = analyze.consistency_report_by_run(
        [comparable, immature],
        state,
        analyze.DEFAULTS.copy(),
        "safe",
    )
    assert report["comparable_runs"] == 1, report
    assert report["consistency_skipped"], report
    return "single comparable run skips consistency reduction"


def test_three_run_field_repro_passes_after_filter():
    state = phase_2_13_state_records()
    runs = [
        phase_2_13_comparable_run("run-a.csv"),
        phase_2_13_comparable_run("run-b.csv"),
        phase_2_13_comparable_run("run-c.csv"),
    ]
    rows = [r for run in runs for r in run["rows"]]
    gate = analyze.acceptance_gate(rows, runs, state, analyze.DEFAULTS.copy())
    assert gate["pass"], gate
    assert gate["comparable_runs"] == 3, gate
    assert not gate["consistency_skipped"], gate
    return "three mature runs pass consistency after filtering"


def test_true_disagreement_still_fails():
    state = phase_2_13_state_records()
    runs = [
        phase_2_13_comparable_run("run-a.csv", bp_delta=-0.756),
        phase_2_13_comparable_run("run-b.csv", bp_delta=0.0),
        phase_2_13_comparable_run("run-c.csv", bp_delta=1.56),
    ]
    rows = [r for run in runs for r in run["rows"]]
    gate = analyze.acceptance_gate(rows, runs, state, analyze.DEFAULTS.copy())
    assert not gate["pass"], gate
    assert gate["max_bias_delta"] > 0.05, gate
    assert any("bias consistency" in reason for reason in gate["reasons"]), gate
    return "true per-run recommendation disagreement still fails"


def test_rejected_patch_includes_per_run_estimates():
    with tempfile.TemporaryDirectory() as td:
        csvs = []
        for idx, bp_delta in enumerate([-0.756, 0.0, 1.56], 1):
            path = os.path.join(td, f"run{idx}.csv")
            write_csv(path, phase_2_13_comparable_run(path, bp_delta=bp_delta)["rows"])
            csvs.append(path)
        state = os.path.join(td, "state.json")
        write_state_records(state, phase_2_13_state_records())
        config = os.path.join(td, "config.ini")
        write_config(config)
        out = os.path.join(td, "patch.ini")
        args = SimpleNamespace(
            inputs=csvs, out=out, mode="safe", state=state, config=config,
            acceptance_gate=True, commit_watermark=False, keys=None, machine_id="test",
            include_stale=False, force=False,
        )
        rc = analyze.run(args)
        assert rc == 1, rc
        with open(out) as fh:
            text = fh.read()
        assert "Per-run estimates used in consistency check" in text, text
        assert "Comparable runs: 3 of 3" in text, text
        assert "bias consistency" in text, text
        return "rejected patch includes per-run estimate diagnostics"


def test_contributor_mass_below_threshold_fails():
    state = phase_2_13_state_with_nonlocked_mass(extra_count=5, extra_n=1000)
    runs = phase_2_13_three_comparable_runs()
    rows = [r for run in runs for r in run["rows"]]
    gate = analyze.acceptance_gate(rows, runs, state, analyze.DEFAULTS.copy())
    assert not gate["pass"], gate
    assert gate["contributor_mass"] < analyze.CONTRIBUTOR_MASS_PASS, gate
    assert any("contributor mass" in reason for reason in gate["reasons"]), gate
    return "contributor mass below hard threshold fails acceptance"


def test_contributor_mass_warn_tier_passes():
    state = phase_2_13_state_with_nonlocked_mass(extra_count=3, extra_n=500)
    runs = phase_2_13_three_comparable_runs()
    rows = [r for run in runs for r in run["rows"]]
    gate = analyze.acceptance_gate(rows, runs, state, analyze.DEFAULTS.copy())
    assert gate["pass"], gate
    assert analyze.CONTRIBUTOR_MASS_PASS <= gate["contributor_mass"] < analyze.CONTRIBUTOR_MASS_WARN, gate
    assert any("contributor mass" in warning for warning in gate["warnings"]), gate
    return "contributor mass warning tier is visible but non-failing"


def test_raw_coverage_below_80_does_not_fail_alone():
    state = phase_2_13_state_records()
    runs = []
    for idx in range(3):
        run_rows = list(phase_2_13_comparable_run(f"run-{idx}.csv")["rows"])
        run_rows.extend(
            row(i * 6000, feature="Unqualified", v_fil=1000, est=720)
            for i in range(100)
        )
        runs.append({"path": f"run-{idx}.csv", "rows": run_rows})
    rows = [r for run in runs for r in run["rows"]]
    gate = analyze.acceptance_gate(rows, runs, state, analyze.DEFAULTS.copy())
    assert gate["pass"], gate
    assert gate["raw_coverage"] < analyze.RAW_COVERAGE_WARN, gate
    assert any("raw MID coverage" in warning for warning in gate["warnings"]), gate
    assert not any("coverage" in reason for reason in gate["reasons"]), gate
    return "raw row coverage below 80 percent is diagnostic only"


def test_acceptance_sigma_p95_uses_bp_derived_value():
    state = phase_2_13_state_records()
    runs = []
    for idx in range(3):
        rows = []
        for i in range(50):
            bp = [-6.0, -3.0, 0.0][i % 3]
            rows.append(row(int(i * (601000 / 49)), feature="LockedC", v_fil=1000, est=720, bp=bp, rt=-3.0, sigma=0.01))
        runs.append({"path": f"run-{idx}.csv", "rows": rows})
    rows = [r for run in runs for r in run["rows"]]
    current = analyze.DEFAULTS.copy()
    current["buf_variance_blend_ref_mm"] = 0.1
    gate = analyze.acceptance_gate(rows, runs, state, current)
    assert gate["pass"], f"Expected high sigma to PASS with warning; reasons: {gate['reasons']}"
    assert gate["sigma_p95"] > 0.1, gate
    assert any("config stale" in w for w in gate["warnings"]), gate["warnings"]
    return "acceptance sigma p95 comes from BP scatter and warns but passes"


def test_2_14_diluted_mass_fails_initially():
    """Reproduce Phase 2.14 bug: sparse buckets dilute mass < 50%."""
    state = analyze.load_state(PHASE_2_14_DILUTED_STATE)
    runs = []
    for i in range(3):
        run_rows = []
        for feature in ["Locked0", "Locked1", "Locked2"]:
            for j in range(100):
                run_rows.append(row(j * 100, feature=feature, v_fil=1000 + (0 if feature=="Locked0" else 25 if feature=="Locked1" else 50), est=1000))
        runs.append({"path": f"run-{i}.csv", "rows": run_rows})
    rows = [r for run in runs for r in run["rows"]]
    gate = analyze.acceptance_gate(rows, runs, state, analyze.DEFAULTS.copy())
    # After implementation, sparse buckets (n=14) are ignored in denominator.
    # total_n = 8000 (LOCKED) + 6000 (STABLE) = 14000.
    # Mass = 8000 / 14000 = 57.1%
    assert gate["pass"], f"Expected mass dilution to PASS after floor; reasons: {gate['reasons']}"
    assert 0.57 <= gate["contributor_mass"] <= 0.58, gate["contributor_mass"]
    return "diluted mass passes after floor implementation"


def test_2_14_high_sigma_fails_initially():
    """Reproduce Phase 2.14 bug: high sigma (2.0) fails when it should warn."""
    state = analyze.load_state(PHASE_2_13_STATE_FIXTURE)
    runs, rows = analyze.read_csv_runs([PHASE_2_14_HIGH_SIGMA_A, PHASE_2_14_HIGH_SIGMA_B, PHASE_2_13_RUN_FIXTURES[2]])
    current = analyze.DEFAULTS.copy()
    current["buf_variance_blend_ref_mm"] = 1.0
    gate = analyze.acceptance_gate(rows, runs, state, current)
    # After implementation, sigma > current_ref but < 5.0 is a WARN, not a FAIL.
    assert gate["pass"], f"Expected high sigma to PASS with warning; reasons: {gate['reasons']}"
    assert gate["sigma_p95"] > 1.0, gate["sigma_p95"]
    assert any("config stale" in w for w in gate["warnings"]), gate["warnings"]
    return "high sigma warns and passes after split implementation"


def test_2_14_two_runs_fails_initially():
    """Reproduce Phase 2.14 bug: 2 runs fail even if consistent and mature."""
    state = phase_2_13_state_records()
    runs = []
    for i in range(2):
        run_rows = []
        for feature in ["LockedA", "LockedB", "LockedC"]:
            for j in range(100):
                run_rows.append(row(j * 100, feature=feature, v_fil=1000, est=720))
        runs.append({"path": f"run-{i}.csv", "rows": run_rows})
    rows = [r for run in runs for r in run["rows"]]
    gate = analyze.acceptance_gate(rows, runs, state, analyze.DEFAULTS.copy())
    # After implementation, 2 runs that are consistent and mature pass with a warning.
    assert gate["pass"], f"Expected 2 runs to PASS with warning; reasons: {gate['reasons']}"
    assert any("soak immature: run count 2 < 3" in w for w in gate["warnings"]), gate["warnings"]
    return "two runs pass with warning after demotion"


def main():
    tests = [
        ("baseline", test_baseline_from_dominant_cluster),
        ("bias-clamp", test_bias_clamped_to_safe_range),
        ("gate-raw-warn", test_acceptance_gate_warns_low_raw_coverage),
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
        ("bp-sigma", test_buf_variance_blend_ref_mm_from_bp_not_bl),
        ("mid-timeout", test_mid_creep_timeout_default_when_insufficient_data),
        ("contributors", test_contributors_block_emitted),
        ("gate-parity", test_phase_2_13_field_repro_gate_should_pass),
        ("gate-shared", test_consistency_uses_recommendation_path),
        ("gate-standalone", test_consistency_matches_standalone_recommendations),
        ("run-skip", test_immature_run_skipped_with_reason),
        ("one-run-skip", test_only_one_comparable_run_skips_consistency_check),
        ("three-run-filter", test_three_run_field_repro_passes_after_filter),
        ("true-disagree", test_true_disagreement_still_fails),
        ("gate-diag", test_rejected_patch_includes_per_run_estimates),
        ("mass-fail", test_contributor_mass_below_threshold_fails),
        ("mass-warn", test_contributor_mass_warn_tier_passes),
        ("raw-warn", test_raw_coverage_below_80_does_not_fail_alone),
        ("gate-bp-sigma", test_acceptance_sigma_p95_uses_bp_derived_value),
        ("2.14-mass", test_2_14_diluted_mass_fails_initially),
        ("2.14-sigma", test_2_14_high_sigma_fails_initially),
        ("2.14-runs", test_2_14_two_runs_fails_initially),
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
