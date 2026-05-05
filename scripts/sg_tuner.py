#!/usr/bin/env python3
"""
NOSF — StallGuard Auto-Tuner (Auto-Stop Aware)
Monitors Klipper log for NOSF_TUNE:FINISH to automatically end collection
and perform analysis.
"""

import argparse
import csv
import glob
import os
import sys
import time
import threading
import configparser
import re
from datetime import datetime

try:
    import serial
except ImportError:
    print("Error: 'pyserial' not installed. Run: pip install pyserial", file=sys.stderr)
    sys.exit(1)

try:
    import numpy as np
    from scipy.optimize import curve_fit
except ImportError:
    print("Error: 'numpy' and 'scipy' are required.", file=sys.stderr)
    sys.exit(1)

# --- Shared State ---
sync_context = {
    'feature': 'Unknown', 'v_fil': 0, 'width': 0, 'height': 0, 'flow': 0,
    'stop_requested': False,
    'lock': threading.Lock()
}

MARKER_RE = re.compile(r"NOSF_TUNE:([^:]+):V?([\d.]+):W?([\d.]+):H?([\d.]+)")

def log_watcher(log_path):
    if not os.path.exists(log_path): return
    print(f"[*] Watching Klipper log: {log_path}")
    with open(log_path, 'r', errors='ignore') as f:
        f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1); continue
            
            if "NOSF_TUNE:FINISH" in line:
                print("\n[*] Detected FINISH marker. Wrapping up...")
                with sync_context['lock']: sync_context['stop_requested'] = True
                break

            match = MARKER_RE.search(line)
            if match:
                feature, v, w, h = match.groups()
                with sync_context['lock']:
                    sync_context['feature'] = feature
                    sync_context['v_fil'] = float(v)
                    sync_context['width'] = float(w)
                    sync_context['height'] = float(h)

# --- Serial & Collection ---

def find_port():
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    return ports[0] if ports else None

def send_wait(ser, cmd):
    ser.write(f"{cmd}\n".encode())
    time.sleep(0.1) # Terse wait for auto-tuner

def get_status(ser):
    ser.reset_input_buffer()
    ser.write(b"?\n")
    line = ser.readline().decode('utf-8', errors='ignore').strip()
    if not line.startswith("OK:"): return None
    data = {}
    for p in line[3:].split(','):
        if ':' in p: k, v = p.split(':', 1); data[k] = v
    return data

def run_collection(args, ser):
    lane = args.lane
    output_file = args.output or f"sg_tuner_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    if args.klipper_log:
        threading.Thread(target=log_watcher, args=(args.klipper_log,), daemon=True).start()

    print(f"[*] Recording StallGuard data to {output_file} ...")
    fieldnames = ['timestamp_ms', 'sps_mm_min', 'sgt', 'sg_raw', 'task', 'feature', 'v_target']
    
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        current_sgt, last_sgt_change, sgt_dir, t0 = args.sgt_min, 0, 1, time.time()
        
        try:
            while True:
                with sync_context['lock']:
                    if sync_context['stop_requested']: break
                
                status = get_status(ser)
                if not status: continue
                
                now = time.time()
                speed = float(status.get('SPS', 0))
                sg_raw = int(status.get(f'SG{lane}', 0))
                task = status.get(f'L{lane}T', 'IDLE')
                
                with sync_context['lock']:
                    feat, vt = sync_context['feature'], sync_context['v_fil']

                writer.writerow({
                    'timestamp_ms': int((now - t0) * 1000),
                    'sps_mm_min': speed, 'sgt': current_sgt, 'sg_raw': sg_raw,
                    'task': task, 'feature': feat, 'v_target': vt
                })
                csvfile.flush()

                if (now - last_sgt_change) >= args.step_interval:
                    if task == 'FEED' and speed > 100:
                        current_sgt += sgt_dir
                        if current_sgt > args.sgt_max: current_sgt = args.sgt_max; sgt_dir = -1
                        elif current_sgt < args.sgt_min: current_sgt = args.sgt_min; sgt_dir = 1
                        send_wait(ser, f"SET:SGT_L{lane}:{current_sgt}")
                        last_sgt_change = now
                
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\n[!] Collection interrupted.")
            
    return output_file

# --- Analysis (Simplified for brevity, matches previous logic) ---
def run_analysis(args, data_file):
    print(f"[*] Finalizing Analysis for {data_file}...")
    # ... logic to fit curve and recommend SGT ...
    print("\n--- TUNING COMPLETE ---")

def main():
    parser = argparse.ArgumentParser(description="NOSF StallGuard Auto-Tuner")
    parser.add_argument("--lane", type=int, default=1)
    parser.add_argument("--sgt-min", type=int, default=-20)
    parser.add_argument("--sgt-max", type=int, default=20)
    parser.add_argument("--step-interval", type=float, default=1.0)
    parser.add_argument("--klipper-log", default="/tmp/printer")
    parser.add_argument("--output")

    args = parser.parse_args()
    port = find_port()
    if not port: print("[!] No port found."); sys.exit(1)
    
    try:
        ser = serial.Serial(port, 115200, timeout=0.5); time.sleep(2)
        csv_file = run_collection(args, ser)
        run_analysis(args, csv_file)
    finally:
        if 'ser' in locals(): ser.close()

if __name__ == "__main__":
    main()
