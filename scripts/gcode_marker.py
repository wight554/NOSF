#!/usr/bin/env python3
"""
NOSF — G-code Metadata Marker (Lean)
Injects markers for feature/speed/geometry and a final FINISH marker.
Optimized to remove IDLE markers for cleaner G-code.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile

# Regular expressions
MOVE_RE = re.compile(r"([Gg][0123])\s*(.*)")
PARAM_RE = re.compile(r"([XYZEFIJ])([-+]?\d*\.?\d*)")
FEATURE_RES = [
    re.compile(r"^; TYPE:(.*)"),
    re.compile(r"^; FEATURE:(.*)"),
    re.compile(r"^;TYPE:(.*)"),
]
WIDTH_RE = re.compile(r"^;WIDTH:([-+]?\d*\.?\d*)")
HEIGHT_RE = re.compile(r"^;HEIGHT:([-+]?\d*\.?\d*)")
LHEIGHT_RE = re.compile(r"^;layer_height=([-+]?\d*\.?\d*)")
LAYER_RE = re.compile(r"^;LAYER:(\d+)")
LAYER_CHANGE_RE = re.compile(r"^;LAYER_CHANGE\b")
EXCLUDE_START_RE = re.compile(r"^EXCLUDE_OBJECT_START\b")
EXCLUDE_END_RE = re.compile(r"^EXCLUDE_OBJECT_END\b")


def compact_feature(name, max_len=18):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_")
    return (slug or "Unknown")[:max_len]


def marker_lines(tag, emit="m118", shell_cmd="nosf"):
    lines = []
    if emit in ("m118", "both"):
        lines.append(f"M118 {tag}\n")
    if emit in ("mark", "both", "file"):
        if tag.startswith("NOSF_TUNE:FINISH"):
            mark_tag = "FINISH"
        elif tag == "NT:START":
            mark_tag = "NT:START"
        elif tag.startswith("NOSF_TUNE:LAYER:"):
            parts = tag.split(":")
            if len(parts) < 3:
                return lines
            mark_tag = f"NT:LAYER:{parts[2]}"
        else:
            m = re.match(r"NOSF_TUNE:(?P<feature>[^:]+):V(?P<vfil>[^:]+):", tag)
            if not m:
                return lines
            mark_tag = f"NT:{compact_feature(m.group('feature'))}:V{float(m.group('vfil')):.0f}"
        if emit == "file":
            cmd = "nosf_marker" if shell_cmd == "nosf" else shell_cmd
            lines.append(f"RUN_SHELL_COMMAND CMD={cmd} PARAMS=\"{mark_tag}\"\n")
        else:
            lines.append(f"RUN_SHELL_COMMAND CMD={shell_cmd} PARAMS=\"MARK:{mark_tag}\"\n")
    return lines


def _source_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_params(raw_line):
    code = raw_line.split(";", 1)[0].upper()
    return {k: float(v) for k, v in PARAM_RE.findall(code) if v not in ("", "+", "-")}


def _v_fil(width, height, feedrate, fil_area):
    # Preserve the existing marker math so sidecar events bucket exactly like
    # legacy M118/RUN_SHELL_COMMAND markers.
    return (width * height * feedrate) / fil_area


def _v_bin(v_fil):
    return int(round(v_fil / 25.0)) * 25


def _empty_segment(
    line_start,
    line_end,
    layer,
    feature,
    z_mm,
    width,
    height,
    feedrate,
    v_fil,
    e_start,
    e_end,
    x_start,
    y_start,
    x_end,
    y_end,
    skip,
):
    return {
        "byte_start": line_start,
        "byte_end": line_end,
        "layer": int(layer),
        "feature": feature or "Unknown",
        "z_mm": float(z_mm),
        "width_mm": float(width),
        "height_mm": float(height),
        "feedrate_mm_per_min": float(feedrate),
        "v_fil_mm3_per_s": float(v_fil),
        "v_fil_bin": _v_bin(v_fil),
        "e_start": float(e_start),
        "e_end": float(e_end),
        "x_start": float(x_start),
        "y_start": float(y_start),
        "x_end": float(x_end),
        "y_end": float(y_end),
        "skip": bool(skip),
    }


def build_sidecar(input_path, sidecar_path, dia):
    """Build `<basename>.nosf.json` metadata for Klipper motion tracking."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)

    fil_area = math.pi * (dia / 2.0) ** 2
    current_f = 0.0
    current_w = None
    current_h = None
    current_feature = "Unknown"
    current_layer = 0
    current_z = 0.0
    current_x = 0.0
    current_y = 0.0
    current_e = 0.0
    e_mode = "absolute"
    layer_change_n = -1
    in_skip = False
    layers = []
    segments = []
    current_segment = None

    def close_segment():
        nonlocal current_segment
        if current_segment is not None:
            segments.append(current_segment)
            current_segment = None

    def note_layer(index, z_mm, byte_start):
        nonlocal current_layer
        if layers:
            layers[-1]["byte_end"] = byte_start
        current_layer = int(index)
        layers.append(
            {
                "index": int(index),
                "z_mm": float(z_mm),
                "byte_start": int(byte_start),
                "byte_end": int(byte_start),
            }
        )

    with open(input_path, "rb") as fh:
        while True:
            line_start = fh.tell()
            raw = fh.readline()
            if not raw:
                break
            line_end = fh.tell()
            line = raw.decode("utf-8", errors="replace")
            raw_line = line.strip()
            upper_line = raw_line.upper()

            if upper_line.startswith("M82"):
                e_mode = "absolute"
            elif upper_line.startswith("M83"):
                e_mode = "relative"
            elif upper_line.startswith("G92"):
                params = _parse_params(raw_line)
                if "E" in params:
                    current_e = params["E"]

            if EXCLUDE_START_RE.match(raw_line):
                in_skip = True
                close_segment()
            elif EXCLUDE_END_RE.match(raw_line):
                close_segment()
                in_skip = False

            layer_match = LAYER_RE.match(raw_line)
            if layer_match:
                close_segment()
                note_layer(int(layer_match.group(1)), current_z, line_start)
            elif LAYER_CHANGE_RE.match(raw_line):
                close_segment()
                layer_change_n += 1
                note_layer(layer_change_n, current_z, line_start)

            for regex in FEATURE_RES:
                match = regex.match(raw_line)
                if match:
                    feature = match.group(1).strip() or "Unknown"
                    if feature != current_feature:
                        close_segment()
                        current_feature = feature
                    break

            w_match = WIDTH_RE.match(raw_line)
            if w_match:
                new_w = float(w_match.group(1))
                if current_w != new_w:
                    close_segment()
                    current_w = new_w

            h_match = HEIGHT_RE.match(raw_line) or LHEIGHT_RE.match(raw_line)
            if h_match:
                new_h = float(h_match.group(1))
                if current_h != new_h:
                    close_segment()
                    current_h = new_h

            move_match = MOVE_RE.match(raw_line)
            if not move_match:
                continue

            move = move_match.group(1).upper()
            params = _parse_params(raw_line)
            prev_x, prev_y, prev_z, prev_e = current_x, current_y, current_z, current_e
            if "F" in params:
                current_f = params["F"]
            end_x = params.get("X", current_x)
            end_y = params.get("Y", current_y)
            end_z = params.get("Z", current_z)

            e_delta = 0.0
            end_e = current_e
            if "E" in params:
                if e_mode == "relative":
                    e_delta = params["E"]
                    end_e = current_e + params["E"]
                else:
                    end_e = params["E"]
                    e_delta = end_e - current_e

            current_x, current_y, current_z, current_e = end_x, end_y, end_z, end_e

            if "Z" in params and layers:
                layers[-1]["z_mm"] = float(end_z)

            if e_delta <= 0.0 or not (current_f > 0 and current_w and current_h):
                continue

            v_fil = _v_fil(current_w, current_h, current_f, fil_area)
            key = (
                current_layer,
                current_feature,
                current_w,
                current_h,
                _v_bin(v_fil),
                in_skip,
            )
            active_key = None
            if current_segment is not None:
                active_key = (
                    current_segment["layer"],
                    current_segment["feature"],
                    current_segment["width_mm"],
                    current_segment["height_mm"],
                    current_segment["v_fil_bin"],
                    current_segment["skip"],
                )

            if move in ("G2", "G3"):
                close_segment()
                segments.append(
                    _empty_segment(
                        line_start, line_end, current_layer, current_feature,
                        end_z, current_w, current_h, current_f, v_fil,
                        prev_e, end_e, prev_x, prev_y, end_x, end_y, in_skip,
                    )
                )
                continue

            if current_segment is None or key != active_key:
                close_segment()
                current_segment = _empty_segment(
                    line_start, line_end, current_layer, current_feature,
                    end_z, current_w, current_h, current_f, v_fil,
                    prev_e, end_e, prev_x, prev_y, end_x, end_y, in_skip,
                )
            else:
                current_segment["byte_end"] = line_end
                current_segment["e_end"] = float(end_e)
                current_segment["x_end"] = float(end_x)
                current_segment["y_end"] = float(end_y)
                current_segment["z_mm"] = float(end_z)

        file_size = fh.tell()

    close_segment()
    if not layers:
        layers.append({"index": 0, "z_mm": current_z, "byte_start": 0, "byte_end": file_size})
    else:
        layers[-1]["byte_end"] = file_size

    sidecar = {
        "_schema": 1,
        "generator": "gcode_marker.py 2.10.1",
        "source_gcode": os.path.basename(input_path),
        "source_sha256": _source_sha256(input_path),
        "filament_dia_mm": float(dia),
        "fil_area_mm2": fil_area,
        "e_mode": e_mode,
        "layers": layers,
        "segments": segments,
    }
    parent = os.path.dirname(os.path.abspath(sidecar_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(sidecar_path, "w") as fh:
        json.dump(sidecar, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return sidecar


def _write_sidecar_gcode(input_path, output_path):
    with open(input_path, "r") as fin, open(output_path, "w") as fout:
        fout.write("M118 NT:START\n")
        for line in fin:
            fout.write(line)
        fout.write("\n; --- NOSF TUNING FINISH ---\n")
        fout.write("M118 NOSF_TUNE:FINISH:0:0:0\n")


def process_gcode(input_path, output_path, filament_dia=1.75, every_layer=True,
                  emit="m118", shell_cmd="nosf", sidecar_path=None):
    if not os.path.exists(input_path):
        print(f"Error: Input file {input_path} not found.")
        return False

    if emit == "sidecar":
        print("[*] Processing file with NOSF sidecar metadata ...")
        _write_sidecar_gcode(input_path, output_path)
        if sidecar_path is None:
            base, _ext = os.path.splitext(output_path)
            sidecar_path = base + ".nosf.json"
        sidecar = build_sidecar(output_path, sidecar_path, filament_dia)
        print(
            f"[*] Done. Wrote {len(sidecar['segments'])} sidecar segments to {sidecar_path}.",
            file=sys.stderr,
        )
        return True

    print("[*] Processing file with lean sync markers ...")

    current_f = 0
    current_w = None
    current_h = None
    current_feature = "Unknown"

    last_reported_v_fil = -1
    last_reported_w = -1
    last_reported_h = -1
    injected_count = 0
    last_layer_n = None
    layer_change_n = -1

    fil_area = math.pi * (filament_dia / 2)**2

    with open(input_path, 'r') as fin, open(output_path, 'w') as fout:
        for marker in marker_lines("NT:START", emit, shell_cmd):
            fout.write(marker)

        for line in fin:
            raw_line = line.strip()

            # Layer marker
            if every_layer:
                layer_match = LAYER_RE.match(raw_line)
                if layer_match:
                    layer_n = layer_match.group(1)
                elif LAYER_CHANGE_RE.match(raw_line):
                    layer_change_n += 1
                    layer_n = str(layer_change_n)
                else:
                    layer_n = None
                if layer_n is not None and layer_n != last_layer_n:
                    for marker in marker_lines(f"NOSF_TUNE:LAYER:{layer_n}:0:0", emit, shell_cmd):
                        fout.write(marker)
                    last_layer_n = layer_n
                    injected_count += 1

            # Capture Metadata
            for r in FEATURE_RES:
                m = r.match(raw_line)
                if m:
                    current_feature = m.group(1).strip()
                    break

            w_match = WIDTH_RE.match(raw_line)
            if w_match:
                current_w = float(w_match.group(1))

            h_match = HEIGHT_RE.match(raw_line) or LHEIGHT_RE.match(raw_line)
            if h_match:
                current_h = float(h_match.group(1))

            # Parse Moves
            move_match = MOVE_RE.match(raw_line)
            if move_match:
                params = dict(PARAM_RE.findall(move_match.group(2).upper()))
                if 'F' in params:
                    current_f = float(params['F'])

                has_e = 'E' in params and float(params['E']) > 0

                if has_e and current_f > 0 and current_w and current_h:
                    v_fil = (current_w * current_h * current_f) / fil_area

                    # Only inject if geometry or speed changed significantly
                    if (abs(v_fil - last_reported_v_fil) > (last_reported_v_fil * 0.05) or
                        current_w != last_reported_w or current_h != last_reported_h):

                        tag = f"NOSF_TUNE:{current_feature}:V{v_fil:.1f}:W{current_w:.2f}:H{current_h:.2f}"
                        for marker in marker_lines(tag, emit, shell_cmd):
                            fout.write(marker)
                        last_reported_v_fil = v_fil
                        last_reported_w = current_w
                        last_reported_h = current_h
                        injected_count += 1

            fout.write(line)

        # Final finish marker
        fout.write("\n; --- NOSF TUNING FINISH ---\n")
        for marker in marker_lines("NOSF_TUNE:FINISH:0:0:0", emit, shell_cmd):
            fout.write(marker)

    print(f"[*] Done. Injected {injected_count} lean sync markers.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Inject lean sync markers")
    parser.add_argument("input", help="Input G-code")
    parser.add_argument("--output", help="Output path")
    parser.add_argument("--sidecar", help="Sidecar JSON path for --emit sidecar")
    parser.add_argument("--dia", type=float, default=1.75, help="Filament diameter")
    parser.add_argument("--every-layer", action="store_true", help="Deprecated. Layer markers are now on by default.")
    parser.add_argument("--no-layer-markers", action="store_false", dest="every_layer", help="Disable per-layer marker injection")
    parser.add_argument("--emit", choices=["m118", "mark", "file", "both", "sidecar"], default="m118",
                        help="Marker output: M118 echo, direct NOSF MARK command, local marker file, M118+MARK, or sidecar JSON")
    parser.add_argument("--shell-cmd", default="nosf",
                        help="Klipper gcode_shell_command name for --emit mark/both")
    parser.set_defaults(every_layer=True)

    args = parser.parse_args()
    if "--every-layer" in sys.argv:
        print("Warning: --every-layer is deprecated. Layer markers are now injected by default.", file=sys.stderr)

    in_place = args.output is None
    if in_place:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".gcode", dir=os.path.dirname(os.path.abspath(args.input)))
        os.close(tmp_fd)
        output = tmp_path
    else:
        output = args.output

    sidecar_arg = args.sidecar
    if in_place and args.emit == "sidecar" and sidecar_arg is None:
        base, _ext = os.path.splitext(args.input)
        sidecar_arg = base + ".nosf.json"

    ok = process_gcode(
        args.input,
        output,
        args.dia,
        args.every_layer,
        args.emit,
        args.shell_cmd,
        sidecar_arg,
    )
    if ok and in_place:
        os.replace(tmp_path, args.input)
        if args.emit == "sidecar":
            sidecar_path = sidecar_arg
            if sidecar_path is None:
                base, _ext = os.path.splitext(args.input)
                sidecar_path = base + ".nosf.json"
            build_sidecar(args.input, sidecar_path, args.dia)
    elif not ok and in_place and os.path.exists(tmp_path):
        os.unlink(tmp_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
