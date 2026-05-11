#!/usr/bin/env python3
"""Stdlib regression tests for gcode_marker.py Phase 2.10 sidecars."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

import gcode_marker


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
ORCA_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "orca_sample.gcode")


def read_json(path):
    with open(path) as fh:
        return json.load(fh)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def test_sidecar_orca_sample():
    with tempfile.TemporaryDirectory() as td:
        sidecar_path = os.path.join(td, "orca_sample.nosf.json")
        data = gcode_marker.build_sidecar(ORCA_FIXTURE, sidecar_path, 1.75)
        assert os.path.exists(sidecar_path), sidecar_path
        assert data["_schema"] == 1, data
        assert data["e_mode"] == "absolute", data
        assert len(data["layers"]) == 4, data["layers"]
        assert len(data["segments"]) >= 10, len(data["segments"])
        features = {seg["feature"] for seg in data["segments"]}
        for feature in ("Bottom_surface", "Outer_wall", "Sparse_infill", "Top_surface"):
            assert feature in features, features
        assert all(seg["v_fil_bin"] > 0 for seg in data["segments"]), data["segments"]
        assert not any(seg.get("skip") for seg in data["segments"]), data["segments"]
        assert any(seg.get("object") for seg in data["segments"]), data["segments"]
        return "Orca sample sidecar has layers, features, v bins, object metadata"


def test_sidecar_relative_e():
    gcode = """M83
;LAYER_CHANGE
;HEIGHT:0.20
G1 Z0.20 F720
;TYPE:Outer_wall
;WIDTH:0.45
G1 X0 Y0 F9000
G1 X10 Y0 E0.50 F1800
G1 X20 Y0 E0.25
G1 E-0.20 F1800
G1 X30 Y0 E0.75 F1800
"""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "relative.gcode")
        sidecar = os.path.join(td, "relative.nosf.json")
        with open(path, "w") as fh:
            fh.write(gcode)
        data = gcode_marker.build_sidecar(path, sidecar, 1.75)
        assert data["e_mode"] == "relative", data
        assert data["segments"], data
        assert abs(data["segments"][-1]["e_end"] - 1.30) < 0.01, data["segments"]
        return "relative E sidecar reconstructs cumulative E"


def test_sidecar_byte_positions_monotonic():
    with tempfile.TemporaryDirectory() as td:
        data = gcode_marker.build_sidecar(ORCA_FIXTURE, os.path.join(td, "s.json"), 1.75)
        starts = [seg["byte_start"] for seg in data["segments"]]
        ends = [seg["byte_end"] for seg in data["segments"]]
        assert starts == sorted(starts), starts
        assert all(end > start for start, end in zip(starts, ends)), data["segments"]
        assert all(
            a["byte_end"] <= b["byte_start"]
            for a, b in zip(data["segments"], data["segments"][1:])
        ), data["segments"]
        return "segment byte positions are monotonic"


def test_sidecar_sha256_matches_source():
    with tempfile.TemporaryDirectory() as td:
        sidecar = os.path.join(td, "s.json")
        data = gcode_marker.build_sidecar(ORCA_FIXTURE, sidecar, 1.75)
        disk = read_json(sidecar)
        assert data["source_sha256"] == sha256(ORCA_FIXTURE), data
        assert disk["source_sha256"] == data["source_sha256"], disk
        return "sidecar source_sha256 matches source file"


def test_sidecar_arc_expansion():
    gcode = """M82
;LAYER:0
;HEIGHT:0.20
G1 Z0.20 F720
;TYPE:Bridge
;WIDTH:0.50
G1 X0 Y0 F9000
G1 X10 Y0 E0.5 F1800
G2 X20 Y0 I5 J0 E1.0 F1800
G1 X30 Y0 E1.5 F1800
"""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "arc.gcode")
        with open(path, "w") as fh:
            fh.write(gcode)
        data = gcode_marker.build_sidecar(path, os.path.join(td, "arc.nosf.json"), 1.75)
        assert len(data["segments"]) == 3, data["segments"]
        arc = data["segments"][1]
        assert arc["feature"] == "Bridge", arc
        assert arc["x_start"] == 10.0 and arc["x_end"] == 20.0, arc
        assert arc["v_fil_bin"] == gcode_marker._v_bin(arc["v_fil_mm3_per_s"]), arc
        return "G2/G3 line becomes its own straight sidecar segment"


def test_emit_sidecar_no_shell_commands():
    with tempfile.TemporaryDirectory() as td:
        out_gcode = os.path.join(td, "orca_sample.nosf.gcode")
        sidecar = os.path.join(td, "orca_sample.nosf.json")
        ok = gcode_marker.process_gcode(
            ORCA_FIXTURE,
            out_gcode,
            filament_dia=1.75,
            every_layer=True,
            emit="sidecar",
            sidecar_path=sidecar,
        )
        assert ok
        with open(out_gcode) as fh:
            text = fh.read()
        assert "RUN_SHELL_COMMAND" not in text, text
        assert text.startswith("M118 NT:START\n"), text[:80]
        assert "M118 NOSF_TUNE:FINISH:0:0:0" in text, text[-120:]
        data = read_json(sidecar)
        assert data["source_sha256"] == sha256(out_gcode), data
        return "--emit sidecar writes JSON and M118 bookends without shell calls"


def test_cli_default_emit_sidecar():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "orca_sample.gcode")
        out_gcode = os.path.join(td, "orca_sample.nosf.gcode")
        with open(ORCA_FIXTURE, "rb") as fin, open(src, "wb") as fout:
            fout.write(fin.read())
        proc = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "gcode_marker.py"), src, "--output", out_gcode],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        sidecar = os.path.splitext(out_gcode)[0] + ".nosf.json"
        assert os.path.exists(sidecar), proc.stderr
        with open(out_gcode) as fh:
            text = fh.read()
        assert "RUN_SHELL_COMMAND" not in text, text
        assert "sidecar metadata" in proc.stdout, proc.stdout
        return "CLI default emits sidecar output"


def test_cli_file_emit_warns_and_still_writes_shell_markers():
    with tempfile.TemporaryDirectory() as td:
        out_gcode = os.path.join(td, "orca_sample.legacy.gcode")
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "gcode_marker.py"),
                ORCA_FIXTURE,
                "--output",
                out_gcode,
                "--emit",
                "file",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "deprecated" in proc.stderr, proc.stderr
        with open(out_gcode) as fh:
            text = fh.read()
        assert "RUN_SHELL_COMMAND CMD=nosf_marker" in text, text[:400]
        return "--emit file warns but preserves shell-marker fallback"


def main():
    tests = [
        ("orca-sidecar", test_sidecar_orca_sample),
        ("relative-e", test_sidecar_relative_e),
        ("byte-pos", test_sidecar_byte_positions_monotonic),
        ("sha256", test_sidecar_sha256_matches_source),
        ("arc", test_sidecar_arc_expansion),
        ("emit-sidecar", test_emit_sidecar_no_shell_commands),
        ("default-sidecar", test_cli_default_emit_sidecar),
        ("file-warning", test_cli_file_emit_warns_and_still_writes_shell_markers),
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
