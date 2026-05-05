#!/usr/bin/env python3
"""
NOSF — StallGuard Auto-Tuner (Sync Dominant)
Enables StallGuard-based sync (SYNC_SG_INTERP) and uses buffer endstops 
as failure trackers to identify the reliable tuning envelope.
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
    'feature': 'Unknown', 'v_fil': 0,
    'stop_requested': False,
    'lock': threading.Lock()
}

MARKER_RE = re.compile(r"NOSF_TUNE:([^:]+):V?([\d.]+)")

def log_watcher(log_path):
    if not os.path.exists(log_path): return
    with open(log_path, 'r', errors='ignore') as f:
        f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1); continue
            if "NOSF_TUNE:FINISH" in line:
                sync_context['stop_requested'] = True; break
            match = MARKER_RE.search(line)
            if match:
                feature, v = match.group(1), match.group(2)
                with sync_context['lock']:
                    sync_context['feature'], sync_context['v_fil'] = feature, float(v)

# --- Serial & Collection ---

def find_port():
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    return ports[0] if ports else None

def send_wait(ser, cmd):
    ser.write(f"{cmd}\n".encode())
    time.sleep(0.1)

def get_status(ser):
    ser.reset_input_buffer()
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
    
    # 1. Prepare Controller for Tuning
    print("[*] Preparing controller for sync-dominant tuning...")
    send_wait(ser, "SET:SYNC_SG_INTERP:1")
    
    # Apply baseline TMC settings
    if baseline:
        for k in ['driver_tbl', 'driver_toff', 'driver_hstrt', 'driver_hend']:
            if k in baseline: send_wait(ser, f"SET:{k.upper()}:{baseline[k]}")

    sgt_center = int(baseline.get('sgt_nominal', 0)) if baseline else 0
    sgt_min, sgt_max = sgt_center + args.sgt_offset_min, sgt_center + args.sgt_offset_max

    if args.klipper_log:
        threading.Thread(target=log_watcher, args=(args.klipper_log,), daemon=True).start()

    print(f"[*] Sweeping SGT: [{sgt_min}, {sgt_max}]")
    
    fieldnames = ['timestamp_ms', 'sps_mm_min', 'sgt', 'sg_raw', 'buf_state', 'feature', 'v_target']
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
                buf_state = status.get('BUF', 'MID')
                task = status.get(f'L{lane}T', 'IDLE')
                with sync_context['lock']: feat, vt = sync_context['feature'], sync_context['v_fil']

                writer.writerow({
                    'timestamp_ms': int((now-t0)*1000), 'sps_mm_min': speed, 'sgt': current_sgt,
                    'sg_raw': sg_raw, 'buf_state': buf_state, 'feature': feat, 'v_target': vt
                })
                csvfile.flush()

                # If we hit an endstop, log it (failure tracking)
                if buf_state in ['LEADING', 'TRAILING'] and task == 'FEED':
                    # Don't print constantly, but keep in CSV
                    pass

                if (now - last_sgt_change) >= args.step_interval:
                    if task == 'FEED' and speed > 100:
                        current_sgt += sgt_dir
                        if current_sgt > sgt_max: current_sgt = sgt_max; sgt_dir = -1
                        elif current_sgt < sgt_min: current_sgt = sgt_min; sgt_dir = 1
                        send_wait(ser, f"SET:SGT_L{lane}:{current_sgt}")
                        last_sgt_change = now
                time.sleep(0.05)
        except KeyboardInterrupt: pass
        
    return output_file

def run_analysis(args, data_file):
    print(f"\n[*] Analyzing data for failure boundaries...")
    collisions = []
    samples = []
    with open(data_file, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            samples.append(row)
            if row['buf_state'] in ['LEADING', 'TRAILING']:
                collisions.append(row)

    print(f"[*] Total Samples: {len(samples)}")
    print(f"[*] Buffer Collisions Detected: {len(collisions)}")
    
    if collisions:
        print("\n--- Failure Envelopes (Buffer Collisions) ---")
        # Identify speeds where collisions happened
        bad_speeds = sorted(list(set([float(c['v_target']) for c in collisions])))
        for s in bad_speeds:
            c_at_s = [c for c in collisions if float(c['v_target']) == s]
            bad_sgts = [int(c['sgt']) for c in c_at_s]
            print(f"  Target {s:7.1f} mm/min | Failed SGTs: {min(bad_sgts)} to {max(bad_sgts)}")

    print("\n[*] Analysis Complete.")

def main():
    parser = argparse.ArgumentParser(description="NOSF StallGuard Sync Tuner")
    parser.add_argument("--lane", type=int, default=1)
    parser.add_argument("--baseline", help="Baseline name (e.g. fysetc_g36_erb20)")
    parser.add_argument("--sgt-offset-min", type=int, default=-10)
    parser.add_argument("--sgt-offset-max", type=int, default=10)
    parser.add_argument("--step-interval", type=float, default=1.0)
    parser.add_argument("--klipper-log", default="/tmp/printer")
    parser.add_argument("--motors-db", default="scripts/motors.ini")
    parser.add_argument("--output")

    args = parser.parse_args()
    port = find_port()
    if not port: print("[!] No port."); sys.exit(1)
    
    try:
        ser = serial.Serial(port, 115200, timeout=0.5); time.sleep(2)
        
        baseline = {}
        if args.baseline:
            config = configparser.ConfigParser(); config.read(args.motors_db)
            baseline = config[f"tuning_baseline_{args.baseline}"]
        
        csv_file = run_collection(args, ser, baseline)
        run_analysis(args, csv_file)
    finally:
        if 'ser' in locals(): ser.close()

if __name__ == "__main__":
    main()
