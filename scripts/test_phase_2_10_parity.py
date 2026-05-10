#!/usr/bin/env python3
"""Offline parity smoke test for Phase 2.10 marker replacement."""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

import gcode_marker
import klipper_motion_tracker as tracker


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
ORCA_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "orca_sample.gcode")
M118_RE = re.compile(r"M118 (NT:LAYER:\d+|NOSF_TUNE:[^\n]+)")


def normalize_event(raw):
    raw = raw.strip()
    if raw == "NT:START" or raw.startswith("NOSF_TUNE:FINISH"):
        return None
    if raw.startswith("NT:LAYER:"):
        return raw
    if raw.startswith("NOSF_TUNE:LAYER:"):
        parts = raw.split(":")
        if len(parts) >= 3:
            return f"NT:LAYER:{parts[2]}"
    if raw.startswith("NOSF_TUNE:"):
        parts = raw.split(":")
        if len(parts) >= 3 and parts[1] != "FINISH":
            feature = parts[1]
            v_raw = parts[2]
            if v_raw.startswith("V"):
                v_bin = int(round(float(v_raw[1:]) / 25.0)) * 25
                return f"{feature}:V{v_bin}"
    return None


def legacy_events(gcode_path):
    with tempfile.TemporaryDirectory() as td:
        out_path = os.path.join(td, "marked.gcode")
        ok = gcode_marker.process_gcode(gcode_path, out_path, emit="m118")
        assert ok
        events = []
        with open(out_path) as fh:
            for line in fh:
                m = M118_RE.search(line)
                if not m:
                    continue
                event = normalize_event(m.group(1))
                if event:
                    events.append(event)
        return events


def matcher_events(gcode_path):
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "orca_sample.gcode")
        sidecar = os.path.join(td, "orca_sample.nosf.json")
        with open(gcode_path, "rb") as fin, open(src, "wb") as fout:
            fout.write(fin.read())
        data = gcode_marker.build_sidecar(src, sidecar, 1.75)
        matcher = tracker.SegmentMatcher(sidecar)
        events = []
        eventtime = 1.0
        for seg in data["segments"]:
            if seg.get("skip"):
                continue
            eventtime += 0.25
            fp = int((seg["byte_start"] + seg["byte_end"]) // 2)
            state = {
                "filename": src,
                "print_state": "printing",
                "file_position": fp,
                "z_mm": seg["z_mm"],
                "v_extrude": 1.0,
                "speed_factor": 1.0,
                "extrude_factor": 1.0,
                "eventtime": eventtime,
            }
            for raw in matcher.update(state):
                event = normalize_event(raw)
                if event:
                    events.append(event)
        matcher.update({"filename": src, "print_state": "complete", "eventtime": eventtime + 1.0})
        return events


def test_compare_with_shell_marker_baseline():
    legacy = legacy_events(ORCA_FIXTURE)
    synth = matcher_events(ORCA_FIXTURE)
    assert legacy, legacy
    assert synth, synth
    delta = abs(len(legacy) - len(synth)) / float(len(legacy))
    assert delta <= 0.05, (legacy, synth, delta)
    assert legacy[0].startswith("NT:LAYER:0"), legacy
    assert synth[0].startswith("NT:LAYER:0"), synth
    assert set(legacy).issubset(set(synth)), (legacy, synth)
    return f"matcher event count within {delta * 100:.1f}% of M118 baseline"


def main():
    tests = [("parity", test_compare_with_shell_marker_baseline)]
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
