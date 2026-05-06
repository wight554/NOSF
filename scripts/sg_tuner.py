#!/usr/bin/env python3
"""
NOSF — StallGuard Auto-Tuner (Flexible Baseline Loader)
Supports [tuning_baseline <name>] and [tuning_baseline_<name>] formats.
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
except ImportError:
    print("Error: 'numpy' is required.", file=sys.stderr)
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

# --- Knowledge Base Helpers ---

def load_baseline_config(args):
    config = configparser.ConfigParser()
    if not os.path.exists(args.motors_db): return {}
    config.read(args.motors_db)
    
    # Try multiple formats: "tuning_baseline name" or "tuning_baseline_name"
    targets = [f"tuning_baseline {args.baseline}", f"tuning_baseline_{args.baseline}"]
    for t in targets:
        if t in config:
            print(f"[*] Loaded baseline: [{t}]")
            return config[t]
    
    if args.baseline:
        print(f"[!] Warning: Baseline '{args.baseline}' not found in {args.motors_db}")
    return {}

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
    
    # 1. Proactive Setup
    send_wait(ser, "SET:SYNC_SG_INTERP:1")
    send_wait(ser, "SM:1")
    
    if baseline:
        for k in ['driver_tbl', 'driver_toff', 'driver_hstrt', 'driver_hend']:
            if k in baseline: send_wait(ser, f"SET:{k.upper()}:{baseline[k]}")
        sg_ma = baseline.get('sg_current_ma', '800')
        send_wait(ser, f"SET:SG_CURRENT_MA_L{lane}:{sg_ma}")

    sgthrs_center = int(baseline.get('sgthrs_nominal', 0)) if baseline else 0
    sgthrs_min, sgthrs_max = sgthrs_center + args.sgthrs_offset_min, sgthrs_center + args.sgthrs_offset_max

    if args.klipper_log:
        threading.Thread(target=log_watcher, args=(args.klipper_log,), daemon=True).start()

    print(f"[*] SGTHRS Sweep: [{sgthrs_min}, {sgthrs_max}] every {args.step_interval}s")
    
    fieldnames = ['timestamp_ms', 'sps_mm_min', 'sgthrs', 'sg_raw', 'buf_state', 'feature', 'v_target']
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        current_sgthrs, last_sgthrs_change, sgthrs_dir, t0 = sgthrs_center, 0, 1, time.time()
        
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
                    'timestamp_ms': int((now-t0)*1000), 'sps_mm_min': speed, 'sgthrs': current_sgthrs,
                    'sg_raw': sg_raw, 'buf_state': buf_state, 'feature': feat, 'v_target': vt
                })
                csvfile.flush()

                if (now - last_sgthrs_change) >= args.step_interval:
                    if task == 'FEED' and speed > 100:
                        current_sgthrs += sgthrs_dir
                        if current_sgthrs > sgthrs_max: current_sgthrs = sgthrs_max; sgthrs_dir = -1
                        elif current_sgthrs < sgthrs_min: current_sgthrs = sgthrs_min; sgthrs_dir = 1
                        send_wait(ser, f"SET:SGTHRS_L{lane}:{current_sgthrs}")
                        last_sgthrs_change = now
                time.sleep(0.05)
        except KeyboardInterrupt: pass
        
    return output_file

def main():
    parser = argparse.ArgumentParser(description="NOSF StallGuard Tuner")
    parser.add_argument("--lane", type=int, default=1)
    parser.add_argument("--baseline", help="Motor name or baseline ID")
    parser.add_argument("--sgthrs-offset-min", type=int, default=-10)
    parser.add_argument("--sgthrs-offset-max", type=int, default=10)
    parser.add_argument("--step-interval", type=float, default=1.0)
    parser.add_argument("--klipper-log", default="/tmp/printer")
    parser.add_argument("--motors-db", default="scripts/motors.ini")
    parser.add_argument("--output")

    args = parser.parse_args()
    port = find_port()
    if not port: print("[!] No port."); sys.exit(1)
    
    try:
        ser = serial.Serial(port, 115200, timeout=0.5); time.sleep(2)
        baseline = load_baseline_config(args)
        csv_file = run_collection(args, ser, baseline)
        print(f"\n[*] Complete. Data: {csv_file}")
    finally:
        if 'ser' in locals(): ser.close()

if __name__ == "__main__":
    main()
