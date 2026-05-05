#!/usr/bin/env python3
"""
NOSF — G-code Metadata Marker
Injects M118 markers only when slicer-intended Width and Height are known.
Calculates filament speed based on cross-sectional geometry (W*H).
"""

import argparse
import sys
import re
import os
import math

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

def process_gcode(input_path, output_path, filament_dia=1.75):
    if not os.path.exists(input_path):
        print(f"Error: Input file {input_path} not found.")
        return False

    print(f"[*] Analyzing geometry-based flow in {input_path} ...")
    
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
        for line in fin:
            raw_line = line.strip()
            
            # 1. Capture Metadata
            for r in FEATURE_RES:
                m = r.match(raw_line)
                if m: current_feature = m.group(1).strip(); break
            
            w_match = WIDTH_RE.match(raw_line)
            if w_match: current_w = float(w_match.group(1))
            
            h_match = HEIGHT_RE.match(raw_line) or LHEIGHT_RE.match(raw_line)
            if h_match: current_h = float(h_match.group(1))
            
            # 2. Parse Moves
            move_match = MOVE_RE.match(raw_line)
            if move_match:
                params = dict(PARAM_RE.findall(move_match.group(2).upper()))
                if 'F' in params: current_f = float(params['F'])
                
                # We only care about moves that HAVE extrusion
                if 'E' in params and current_f > 0:
                    # Only report if we have the full geometry context
                    if current_w is not None and current_h is not None:
                        # Theoretical filament speed based on geometry:
                        # Q = W * H * V_linear
                        # V_fil = Q / Area_fil
                        v_fil = (current_w * current_h * current_f) / fil_area
                        
                        # Report if anything meaningful changed
                        change = (abs(v_fil - last_reported_v_fil) > (last_reported_v_fil * 0.05) or
                                 current_w != last_reported_w or
                                 current_h != last_reported_h)
                                 
                        if change:
                            flow_mm3s = (v_fil / 60.0) * fil_area
                            marker = f"M118 NOSF_TUNE:{current_feature}:V{v_fil:.1f}:W{current_w:.2f}:H{current_h:.2f} (Q:{flow_mm3s:.2f})\n"
                            fout.write(marker)
                            injected_count += 1
                            last_reported_v_fil = v_fil
                            last_reported_w = current_w
                            last_reported_h = current_h

            fout.write(line)

    print(f"[*] Done. Injected {injected_count} geometry-aware markers.")
    return True

def main():
    parser = argparse.ArgumentParser(description="Inject geometry-aware sync markers")
    parser.add_argument("input", help="Input G-code")
    parser.add_argument("--output", help="Output path")
    parser.add_argument("--dia", type=float, default=1.75, help="Filament diameter")
    
    args = parser.parse_args()
    output = args.output or f"{os.path.splitext(args.input)[0]}_geo{os.path.splitext(args.input)[1]}"
    
    if process_gcode(args.input, output, args.dia):
        sys.exit(0)
    sys.exit(1)

if __name__ == "__main__":
    main()
