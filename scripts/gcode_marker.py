#!/usr/bin/env python3
"""
NOSF — G-code Metadata Marker (Lean)
Injects markers for feature/speed/geometry and a final FINISH marker.
Optimized to remove IDLE markers for cleaner G-code.
"""

import argparse
import sys
import re
import os
import math
import tempfile

# Regular expressions
MOVE_RE = re.compile(r"([Gg][0123])\s*(.*)")
PARAM_RE = re.compile(r"([XYZEF])([-+]?\d*\.?\d*)")
FEATURE_RES = [
    re.compile(r"^; TYPE:(.*)"),
    re.compile(r"^; FEATURE:(.*)"),
    re.compile(r"^;TYPE:(.*)"),
]
WIDTH_RE = re.compile(r"^;WIDTH:([-+]?\d*\.?\d*)")
HEIGHT_RE = re.compile(r"^;HEIGHT:([-+]?\d*\.?\d*)")
LHEIGHT_RE = re.compile(r"^;layer_height=([-+]?\d*\.?\d*)")
LAYER_RE = re.compile(r"^;LAYER:(\d+)")

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

def process_gcode(input_path, output_path, filament_dia=1.75, every_layer=False,
                  emit="m118", shell_cmd="nosf"):
    if not os.path.exists(input_path):
        print(f"Error: Input file {input_path} not found.")
        return False

    print(f"[*] Processing file with lean sync markers ...")
    
    current_f = 0
    current_w = None
    current_h = None
    current_feature = "Unknown"
    
    last_reported_v_fil = -1
    last_reported_w = -1
    last_reported_h = -1
    injected_count = 0
    
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
                    for marker in marker_lines(f"NOSF_TUNE:LAYER:{layer_n}:0:0", emit, shell_cmd):
                        fout.write(marker)
                    injected_count += 1

            # Capture Metadata
            for r in FEATURE_RES:
                m = r.match(raw_line)
                if m: current_feature = m.group(1).strip(); break
            
            w_match = WIDTH_RE.match(raw_line)
            if w_match: current_w = float(w_match.group(1))
            
            h_match = HEIGHT_RE.match(raw_line) or LHEIGHT_RE.match(raw_line)
            if h_match: current_h = float(h_match.group(1))
            
            # Parse Moves
            move_match = MOVE_RE.match(raw_line)
            if move_match:
                params = dict(PARAM_RE.findall(move_match.group(2).upper()))
                if 'F' in params: current_f = float(params['F'])
                
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
    parser.add_argument("--dia", type=float, default=1.75, help="Filament diameter")
    parser.add_argument("--every-layer", action="store_true", help="Inject marker on every layer boundary")
    parser.add_argument("--emit", choices=["m118", "mark", "file", "both"], default="m118",
                        help="Marker output: M118 echo, direct NOSF MARK command, local marker file, or M118+MARK")
    parser.add_argument("--shell-cmd", default="nosf",
                        help="Klipper gcode_shell_command name for --emit mark/both")
    
    args = parser.parse_args()
    in_place = args.output is None
    if in_place:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".gcode", dir=os.path.dirname(os.path.abspath(args.input)))
        os.close(tmp_fd)
        output = tmp_path
    else:
        output = args.output

    ok = process_gcode(args.input, output, args.dia, args.every_layer, args.emit, args.shell_cmd)
    if ok and in_place:
        os.replace(tmp_path, args.input)
    elif not ok and in_place and os.path.exists(tmp_path):
        os.unlink(tmp_path)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
