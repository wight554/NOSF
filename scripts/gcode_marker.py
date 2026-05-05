#!/usr/bin/env python3
"""
NOSF — G-code Metadata Marker (Auto-Stop Aware)
Injects markers for feature/speed/geometry and a final FINISH marker 
to allow for automatic tuner termination.
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

    print(f"[*] Processing file for full-print tuning ...")
    
    current_f = 0
    current_w = None
    current_h = None
    current_feature = "Unknown"
    
    last_reported_v_fil = -1
    is_in_segment = False
    injected_count = 0
    
    fil_area = math.pi * (filament_dia / 2)**2

    with open(input_path, 'r') as fin, open(output_path, 'w') as fout:
        for line in fin:
            raw_line = line.strip()
            
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
                    if not is_in_segment or abs(v_fil - last_reported_v_fil) > (last_reported_v_fil * 0.05):
                        fout.write(f"M118 NOSF_TUNE:{current_feature}:V{v_fil:.1f}:W{current_w:.2f}:H{current_h:.2f}\n")
                        is_in_segment = True
                        last_reported_v_fil = v_fil
                        injected_count += 1
                else:
                    if is_in_segment:
                        fout.write(f"M118 NOSF_TUNE:IDLE:0:0:0\n")
                        is_in_segment = False
                        last_reported_v_fil = -1
                        injected_count += 1

            fout.write(line)
        
        # Inject final finish marker
        fout.write("\n; --- NOSF TUNING FINISH ---\n")
        fout.write("M118 NOSF_TUNE:FINISH:0:0:0\n")

    print(f"[*] Done. File is marked for auto-stopping.")
    return True

def main():
    parser = argparse.ArgumentParser(description="Inject markers with auto-stop")
    parser.add_argument("input", help="Input G-code")
    parser.add_argument("--output", help="Output path")
    parser.add_argument("--dia", type=float, default=1.75, help="Filament diameter")
    
    args = parser.parse_args()
    output = args.output or f"{os.path.splitext(args.input)[0]}_final{os.path.splitext(args.input)[1]}"
    
    if process_gcode(args.input, output, args.dia):
        sys.exit(0)
    sys.exit(1)

if __name__ == "__main__":
    main()
