#!/usr/bin/env python3
"""
NOSF — G-code Marker
Injects M118 console markers into G-code files to sync StallGuard tuning with Klipper moves.
Identifies slicer features (Infill, Perimeter, etc.) and speed changes.
"""

import argparse
import sys
import re
import os

# Common slicer feature markers
FEATURE_RES = [
    re.compile(r"^; TYPE:(.*)"),      # PrusaSlicer / SuperSlicer
    re.compile(r"^; FEATURE:(.*)"),   # OrcaSlicer / BambuStudio
    re.compile(r"^;TYPE:(.*)"),       # Cura
]

# Speed extraction G1/G0 F<val>
SPEED_RE = re.compile(r"[Gg][01].*[Ff](\d+\.?\d*)")

def process_gcode(input_path, output_path):
    if not os.path.exists(input_path):
        print(f"Error: Input file {input_path} not found.")
        return False

    print(f"[*] Processing {input_path} ...")
    
    current_feature = "Unknown"
    current_speed = 0
    injected_count = 0

    with open(input_path, 'r') as fin, open(output_path, 'w') as fout:
        for line in fin:
            raw_line = line.strip()
            
            # Check for feature change
            feature_match = None
            for r in FEATURE_RES:
                m = r.match(raw_line)
                if m:
                    feature_match = m.group(1).strip()
                    break
            
            if feature_match:
                current_feature = feature_match
                # We don't inject immediately, wait for the next speed change
                fout.write(line)
                continue

            # Check for speed change
            speed_match = SPEED_RE.search(raw_line)
            if speed_match:
                new_speed = float(speed_match.group(1))
                if new_speed != current_speed:
                    current_speed = new_speed
                    # Inject marker before the move
                    marker = f"M118 NOSF_TUNE:{current_feature}:{current_speed:.0f}\n"
                    fout.write(marker)
                    injected_count += 1
            
            fout.write(line)

    print(f"[*] Done. Injected {injected_count} sync markers.")
    print(f"[*] Output saved to: {output_path}")
    return True

def main():
    parser = argparse.ArgumentParser(description="Inject NOSF sync markers into G-code")
    parser.add_argument("input", help="Input G-code file")
    parser.add_argument("--output", help="Output G-code file (default: input_nosf.gcode)")
    
    args = parser.parse_args()
    
    output = args.output
    if not output:
        base, ext = os.path.splitext(args.input)
        output = f"{base}_nosf{ext}"
    
    if process_gcode(args.input, output):
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
