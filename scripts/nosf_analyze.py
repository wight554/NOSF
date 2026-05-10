#!/usr/bin/env python3
"""nosf_analyze.py - offline calibration analyzer for NOSF Phase 2.9.

Reads one or more nosf_logger.py CSV captures, optionally combines them
with nosf_live_tuner.py schema-2 bucket state, and writes a review-only
config patch. Pure stdlib only.
"""

import argparse
import csv
import json
import math
import os
import statistics as stats
import sys
from collections import defaultdict


SAFETY_K = {"safe": 1.5, "aggressive": 1.0}
BIAS_SAFE_MIN = 0.05
BIAS_SAFE_MAX = 0.65
DEFAULTS = {
    "baseline_rate": 1600.0,
    "sync_trailing_bias_frac": 0.4,
    "mid_creep_timeout_ms": 4000.0,
    "mid_creep_rate_sps_per_s": 5.0,
    "mid_creep_cap_frac": 10.0,
    "buf_variance_blend_frac": 0.5,
    "buf_variance_blend_ref_mm": 1.0,
}


def bin_v_fil(v):
    return int(round(float(v) / 25.0)) * 25


def bucket_label(feature, v_fil):
    return f"{feature}_v{bin_v_fil(v_fil)}"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def percentile(values, pct):
    if not values:
        return 0.0
    vs = sorted(values)
    if len(vs) == 1:
        return vs[0]
    pos = (len(vs) - 1) * (pct / 100.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vs[lo]
    return vs[lo] + (vs[hi] - vs[lo]) * (pos - lo)


def median(values):
    return stats.median(values) if values else 0.0


def stdev(values):
    return stats.stdev(values) if len(values) > 1 else 0.0


def read_config(path):
    values = DEFAULTS.copy()
    if not path or not os.path.exists(path):
        return values
    with open(path) as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, raw = line.split(":", 1)
            key = key.strip()
            if key not in values:
                continue
            try:
                values[key] = float(raw.strip().split()[0])
            except (ValueError, IndexError):
                pass
    return values


def to_float(raw, default=0.0):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def read_csv_runs(paths):
    runs = []
    for idx, path in enumerate(paths, 1):
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        for row in rows:
            row["_run"] = idx
            row["_path"] = path
        runs.append({"path": path, "rows": rows})
    rows = [row for run in runs for row in run["rows"]]
    return runs, rows


def load_state(path):
    if not path:
        return {}
    with open(path) as fh:
        data = json.load(fh)
    schema = data.get("_schema")
    if schema not in (1, 2):
        raise ValueError(f"unsupported state schema {schema!r}")
    buckets = {}
    for machine, machine_buckets in data.items():
        if machine.startswith("_") or not isinstance(machine_buckets, dict):
            continue
        for label, raw in machine_buckets.items():
            buckets[label] = raw
    return buckets


def locked_bucket_labels(state_buckets):
    return {
        label for label, raw in state_buckets.items()
        if raw.get("locked") or raw.get("state") == "LOCKED"
    }


def mid_rows(rows):
    return [
        r for r in rows
        if r.get("zone") == "MID"
        and to_float(r.get("est_sps")) > 0.0
        and to_float(r.get("v_fil")) > 0.0
    ]


def run_duration_s(run):
    ts = [to_float(r.get("ts_ms"), -1.0) for r in run["rows"]]
    ts = [v for v in ts if v >= 0.0]
    if len(ts) < 2:
        return 0.0
    return max(ts) / 1000.0 - min(ts) / 1000.0


def confidence(n, high, medium=None):
    if medium is None:
        medium = max(1, high // 2)
    if n >= high:
        return "HIGH"
    if n >= medium:
        return "MEDIUM"
    if n > 0:
        return "LOW"
    return "DEFAULT"


def compute_recommendations(rows, runs, state_buckets, current, mode):
    mids = mid_rows(rows)
    safety = SAFETY_K[mode]
    by_bucket = defaultdict(list)
    for r in mids:
        by_bucket[bucket_label(r.get("feature", ""), to_float(r.get("v_fil")))].append(r)

    dominant = max(by_bucket.values(), key=len) if by_bucket else []
    est_vals = [to_float(r.get("est_sps")) for r in dominant]
    est_p50 = median(est_vals)
    est_sigma = stdev(est_vals)
    baseline = max(0.0, est_p50 - safety * est_sigma) if est_vals else current["baseline_rate"]
    baseline_conf = "HIGH" if len(est_vals) >= 1000 and est_sigma < 0.15 * max(est_p50, 1.0) else confidence(len(est_vals), 1000)

    bp_delta = [to_float(r.get("bp_mm")) - to_float(r.get("rt_mm")) for r in mids]
    bias = clamp(0.4 + (stats.mean(bp_delta) / 7.8 if bp_delta else 0.0), BIAS_SAFE_MIN, BIAS_SAFE_MAX)
    bias_conf = confidence(len(bp_delta), 1000)

    dwell_ms = []
    for run in runs:
        segment_start = None
        last_ts = None
        for r in run["rows"]:
            ts = to_float(r.get("ts_ms"), -1.0)
            active = r.get("zone") == "MID" and to_float(r.get("est_sps")) > 0.0 and ts >= 0.0
            if active and segment_start is None:
                segment_start = ts
            if not active and segment_start is not None and last_ts is not None:
                dwell_ms.append(max(0.0, last_ts - segment_start))
                segment_start = None
            if ts >= 0.0:
                last_ts = ts
        if segment_start is not None and last_ts is not None:
            dwell_ms.append(max(0.0, last_ts - segment_start))
    mid_timeout = percentile(dwell_ms, 95) if len(dwell_ms) >= 50 else current["mid_creep_timeout_ms"]
    mid_timeout_conf = confidence(len(dwell_ms), 50, 20) if dwell_ms else "DEFAULT"

    creep_slopes = []
    creep_caps = []
    for run in runs:
        prev = None
        segment_start_est = None
        for r in run["rows"]:
            ts = to_float(r.get("ts_ms"), -1.0) / 1000.0
            est = to_float(r.get("est_sps"))
            creeping = r.get("zone") == "MID" and to_float(r.get("mc")) > 0.0 and ts >= 0.0 and est > 0.0
            if not creeping:
                prev = None
                segment_start_est = None
                continue
            if segment_start_est is None:
                segment_start_est = est
            else:
                creep_caps.append((est / max(segment_start_est, 1.0)) * 100.0)
            if prev:
                dt = ts - prev[0]
                if dt > 0.0:
                    creep_slopes.append((est - prev[1]) / dt)
            prev = (ts, est)
    creep_rate = median(creep_slopes) if len(creep_slopes) >= 20 else current["mid_creep_rate_sps_per_s"]
    creep_rate_conf = confidence(len(creep_slopes), 20, 10) if creep_slopes else "DEFAULT"
    creep_cap = percentile(creep_caps, 90) if len(creep_caps) >= 20 else current["mid_creep_cap_frac"]
    creep_cap_conf = confidence(len(creep_caps), 20, 10) if creep_caps else "DEFAULT"

    sigma_vals = [to_float(r.get("sigma_mm")) for r in mids if r.get("sigma_mm") not in ("", None)]
    sigma_p95 = percentile(sigma_vals, 95)
    var_blend = 0.5 if sigma_p95 <= current["buf_variance_blend_ref_mm"] else 0.3
    var_conf = confidence(len(sigma_vals), 500)
    var_ref = max(0.5, round(sigma_p95 / 0.5) * 0.5) if sigma_vals else current["buf_variance_blend_ref_mm"]
    ref_conf = confidence(len(sigma_vals), 500)

    return {
        "baseline_rate": (baseline, baseline_conf, f"n={len(est_vals)}, sigma={est_sigma:.1f}"),
        "sync_trailing_bias_frac": (bias, bias_conf, f"n={len(bp_delta)}"),
        "mid_creep_timeout_ms": (mid_timeout, mid_timeout_conf, f"{len(dwell_ms)} dwells"),
        "mid_creep_rate_sps_per_s": (creep_rate, creep_rate_conf, f"{len(creep_slopes)} creep slopes"),
        "mid_creep_cap_frac": (creep_cap, creep_cap_conf, f"{len(creep_caps)} creep ratios"),
        "buf_variance_blend_frac": (var_blend, var_conf, f"sigma p95 {sigma_p95:.2f}"),
        "buf_variance_blend_ref_mm": (var_ref, ref_conf, f"sigma p95 {sigma_p95:.2f}"),
    }


def consistency_by_run(runs):
    baseline_vals = defaultdict(list)
    bias_vals = defaultdict(list)
    for run in runs:
        grouped = defaultdict(list)
        for r in mid_rows(run["rows"]):
            grouped[bucket_label(r.get("feature", ""), to_float(r.get("v_fil")))].append(r)
        for label, bucket_rows in grouped.items():
            ests = [to_float(r.get("est_sps")) for r in bucket_rows]
            bp_delta = [to_float(r.get("bp_mm")) - to_float(r.get("rt_mm")) for r in bucket_rows]
            if ests:
                baseline_vals[label].append(median(ests))
            if bp_delta:
                bias_vals[label].append(clamp(0.4 + stats.mean(bp_delta) / 7.8, BIAS_SAFE_MIN, BIAS_SAFE_MAX))
    max_baseline_delta = max((max(vs) - min(vs) for vs in baseline_vals.values() if len(vs) >= 2), default=0.0)
    max_bias_delta = max((max(vs) - min(vs) for vs in bias_vals.values() if len(vs) >= 2), default=0.0)
    return max_baseline_delta, max_bias_delta


def acceptance_gate(rows, runs, state_buckets, current):
    reasons = []
    mids = mid_rows(rows)
    locked = locked_bucket_labels(state_buckets)
    locked_mid = [
        r for r in mids
        if bucket_label(r.get("feature", ""), to_float(r.get("v_fil"))) in locked
    ]
    coverage = (len(locked_mid) / len(mids)) if mids else 0.0
    if coverage < 0.80:
        reasons.append(f"coverage {coverage * 100:.1f}% < 80.0%")

    base_delta, bias_delta = consistency_by_run(runs)
    if base_delta > 50.0:
        reasons.append(f"baseline consistency delta {base_delta:.0f} sps > 50")
    if bias_delta > 0.05:
        reasons.append(f"bias consistency delta {bias_delta:.3f} > 0.050")

    sigma_vals = [to_float(r.get("sigma_mm")) for r in mids if r.get("sigma_mm") not in ("", None)]
    sigma_p95 = percentile(sigma_vals, 95)
    if sigma_p95 >= current["buf_variance_blend_ref_mm"]:
        reasons.append(f"sigma p95 {sigma_p95:.2f} >= current ref {current['buf_variance_blend_ref_mm']:.2f}")

    if len(runs) < 3:
        reasons.append(f"run count {len(runs)} < 3")
    durations = [run_duration_s(run) for run in runs]
    if not (durations and all(d >= 600.0 for d in durations) or sum(durations) >= 1800.0):
        reasons.append(f"duration total {sum(durations) / 60.0:.1f} min < 30 min and at least one run < 10 min")
    if len(locked) < 3:
        reasons.append(f"locked bucket count {len(locked)} < 3")

    telemetry = {
        "ADV_DWELL_STOP": 0,
        "ADV_RISK_HIGH": 0,
        "EST_FALLBACK": 0,
    }
    if telemetry["ADV_DWELL_STOP"] != 0:
        reasons.append("ADV_DWELL_STOP count > 0")
    if telemetry["ADV_RISK_HIGH"] > 5 * max(1, len(runs)):
        reasons.append("ADV_RISK_HIGH count > 5 per run")
    if telemetry["EST_FALLBACK"] != 0:
        reasons.append("EST_FALLBACK count > 0")

    return {
        "pass": not reasons,
        "reasons": reasons,
        "coverage": coverage,
        "max_baseline_delta": base_delta,
        "max_bias_delta": bias_delta,
        "sigma_p95": sigma_p95,
        "telemetry": telemetry,
    }


def format_value(key, value):
    if key in ("sync_trailing_bias_frac", "buf_variance_blend_frac", "buf_variance_blend_ref_mm"):
        return f"{value:.3f}"
    return f"{int(round(value))}"


def write_patch(path, runs, rows, state_buckets, current, recommendations, gate):
    locked = locked_bucket_labels(state_buckets)
    rejected = gate and not gate["pass"]
    with open(path, "w") as fh:
        fh.write("# nosf_analyze.py emitted patch\n")
        fh.write(f"# Source: {len(runs)} runs, {len(rows)} samples, {len(locked)} LOCKED buckets\n")
        if gate:
            fh.write(f"# Acceptance gate: {'PASS' if gate['pass'] else 'FAIL'}\n")
            fh.write(f"# Coverage: {gate['coverage'] * 100:.1f} % of MID time in LOCKED buckets\n")
            fh.write(
                f"# Consistency: max baseline delta {gate['max_baseline_delta']:.0f} sps, "
                f"max bias delta {gate['max_bias_delta']:.3f}\n"
            )
            tel = gate["telemetry"]
            fh.write(
                "# Telemetry: "
                f"ADV_RISK_HIGH={tel['ADV_RISK_HIGH']}, "
                f"EST_FALLBACK={tel['EST_FALLBACK']}, "
                f"ADV_DWELL_STOP={tel['ADV_DWELL_STOP']}\n"
            )
            if gate["reasons"]:
                fh.write("# Failure reasons:\n")
                for reason in gate["reasons"]:
                    fh.write(f"# - {reason}\n")
        else:
            fh.write("# Acceptance gate: NOT RUN\n")
        fh.write("# WARNING: do not blindly apply; review against config.ini first.\n\n")
        fh.write("[nosf_review]\n")
        fh.write("# Each line: current_value -> suggested_value (confidence)\n")
        for key in DEFAULTS:
            suggested, conf, detail = recommendations[key]
            line_conf = "REJECTED" if rejected else conf
            fh.write(
                f"# {key:<28} {format_value(key, current[key]):>7} -> "
                f"{format_value(key, suggested):<7} ({line_conf}, {detail})\n"
            )
        fh.write("\n")
        fh.write("# To apply, copy reviewed values into config.ini, then run:\n")
        fh.write("#   python3 scripts/gen_config.py\n")
        fh.write("#   ninja -C build_local\n")
        fh.write("#   bash scripts/flash_nosf.sh\n")


def run(args):
    try:
        runs, rows = read_csv_runs(args.inputs)
    except FileNotFoundError as exc:
        print(f"Error: file not found: {exc.filename}", file=sys.stderr)
        return 1
    if not rows:
        print("Error: no data rows found", file=sys.stderr)
        return 1
    current = read_config(args.config)
    try:
        state_buckets = load_state(args.state) if args.state else {}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error: could not read state file: {exc}", file=sys.stderr)
        return 1
    recommendations = compute_recommendations(rows, runs, state_buckets, current, args.mode)
    gate = acceptance_gate(rows, runs, state_buckets, current) if args.acceptance_gate else None
    write_patch(args.out, runs, rows, state_buckets, current, recommendations, gate)
    if gate and not gate["pass"]:
        for reason in gate["reasons"]:
            print(f"acceptance gate failed: {reason}", file=sys.stderr)
        print(f"[*] Wrote rejected review patch to {args.out}", file=sys.stderr)
        return 1
    print(f"[*] Wrote review patch to {args.out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Analyze NOSF calibration CSVs")
    ap.add_argument("--in", dest="inputs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["safe", "aggressive"], default="safe")
    ap.add_argument("--state", help="Optional nosf_live_tuner.py bucket state JSON")
    ap.add_argument("--config", default="config.ini", help="Current config.ini for current-value display")
    ap.add_argument("--acceptance-gate", action="store_true", help="Exit non-zero unless Phase 2.9 acceptance checks pass")
    ap.add_argument("--feedforward", action="store_true", help=argparse.SUPPRESS)
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
