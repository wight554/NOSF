#!/usr/bin/env python3
"""
NOSF — StallGuard Auto-Tuner (Knowledge Base Aware)
Supports loading baselines from motors.ini to center sweeps and 
auto-configure TMC chopper settings before tuning.
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
    'feature': 'Unknown', 'v_fil': 0, 'width': 0, 'height': 0,
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
                print("\n[*] Detected FINISH marker. Wrapping up..."); sync_context['stop_requested'] = True; break
            
            match = MARKER_RE.search(line)
            if match:
                feature, v, w, h = match.groups()
                with sync_context['lock']:
                    sync_context['feature'], sync_context['v_fil'] = feature, float(v)

# --- Knowledge Base Helpers ---

def load_baseline(args, ser):
    if not args.baseline: return None
    config = configparser.ConfigParser()
    if not os.path.exists(args.motors_db): return None
    config.read(args.motors_db)
    
    section = f"tuning_baseline_{args.baseline}"
    if section not in config:
        print(f"[!] Baseline '{args.baseline}' not found in database."); return None
    
    b = config[section]
    print(f"[*] Applying baseline: {args.baseline}")
    
    # Auto-configure TMC settings if present
    for key in ['driver_tbl', 'driver_toff', 'driver_hstrt', 'driver_hend']:
        if key in b:
            send_wait(ser, f"SET:{key.upper()}:{b[key]}")
            print(f"    {key.upper()} = {b[key]}")
    
    return b

# --- Serial & Collection ---

def find_port():
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    return ports[0] if ports else None

def send_wait(ser, cmd):
    ser.reset_input_buffer()
    ser.write(f"{cmd}\n".encode())
    time.sleep(0.1)

def get_status(ser):
    ser.write(b"?\n")
    line = ser.readline().decode('utf-8', errors='ignore').strip()
    if not line.startswith("OK:"): return None
    data = {}
    for p in line[3:].split(','):
        if ':' in p: k, v = p.split(':', 1); data[k] = v
    return data

def run_collection(args, ser, baseline=None):
    lane = args.lane
    output_file = args.output or f"sg_tuner_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Center sweep around baseline if available
    sgt_center = int(baseline.get('sgt_nominal', 0)) if baseline else 0
    sgt_min = sgt_center + args.sgt_offset_min
    sgt_max = sgt_center + args.sgt_offset_max

    if args.klipper_log:
        threading.Thread(target=log_watcher, args=(args.klipper_log,), daemon=True).start()

    print(f"[*] Recording StallGuard data to {output_file} ...")
    print(f"[*] SGT Sweep Range: [{sgt_min}, {sgt_max}] (centered on {sgt_center})")
    
    fieldnames = ['timestamp_ms', 'sps_mm_min', 'sgt', 'sg_raw', 'task', 'feature', 'v_target']
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        current_sgt, last_sgt_change, sgt_dir, t0 = sgt_center, 0, 1, time.time()
        
        try:
            while not sync_context['stop_requested']:
                status = get_status(ser)
                if not status: continue
                
                now = time.time()
                speed, sg_raw = float(status.get('SPS', 0)), int(status.get(f'SG{lane}', 0))
                task = status.get(f'L{lane}T', 'IDLE')
                with sync_context['lock']: feat, vt = sync_context['feature'], sync_context['v_fil']

                writer.writerow({
                    'timestamp_ms': int((now-t0)*1000), 'sps_mm_min': speed, 'sgt': current_sgt,
                    'sg_raw': sg_raw, 'task': task, 'feature': feat, 'v_target': vt
                })
                csvfile.flush()

                if (now - last_sgt_change) >= args.step_interval:
                    if task == 'FEED' and speed > 100:
                        current_sgt += sgt_dir
                        if current_sgt > sgt_max: current_sgt = sgt_max; sgt_dir = -1
                        elif current_sgt < sgt_min: current_sgt = sgt_min; sgt_dir = 1
                        send_wait(ser, f"SET:SGT_L{lane}:{current_sgt}")
                        last_sgt_change = now
                time.sleep(0.05)
        except KeyboardInterrupt: print("\n[!] Collection stopped.")
    return output_file

def main():
    parser = argparse.ArgumentParser(description="NOSF StallGuard Sync Tuner (KB Aware)")
    parser.add_argument("--lane", type=int, default=1)
    parser.add_argument("--baseline", help="Baseline name from motors.ini (e.g. fysetc_g36_erb20)")
    parser.add_argument("--sgt-offset-min", type=int, default=-10, help="Offset from baseline min")
    parser.add_argument("--sgt-offset-max", type=int, default=10, help="Offset from baseline max")
    parser.add_argument("--step-interval", type=float, default=1.0)
    parser.add_argument("--klipper-log", default="/tmp/printer")
    parser.add_argument("--motors-db", default="scripts/motors.ini")
    parser.add_argument("--output")

    args = parser.parse_args()
    port = find_port()
    if not port: print("[!] No port."); sys.exit(1)
    
    try:
        ser = serial.Serial(port, 115200, timeout=0.5); time.sleep(2)
        baseline = load_baseline(args, ser)
        csv_file = run_collection(args, ser, baseline)
        print(f"\n[*] Tuning complete. Data saved to {csv_file}")
    finally:
        if 'ser' in locals(): ser.close()

if __name__ == "__main__":
    main()
