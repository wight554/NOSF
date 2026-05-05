#!/usr/bin/env python3
"""
NOSF — StallGuard Auto-Tuner (Geometry/Flow Aware)
Live-sweeps SGT values and correlates them with linear filament speed, 
volumetric flow (Q), and line geometry (W, H).
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
    print("Error: 'numpy' and 'scipy' are required for this script.", file=sys.stderr)
    sys.exit(1)

# --- Physical Constants ---
V_SUPPLY = 24.0

# --- Shared State for Klipper Sync ---
sync_context = {
    'feature': 'Unknown',
    'v_fil': 0,
    'width': 0,
    'height': 0,
    'flow': 0,
    'lock': threading.Lock()
}

# Regex for the new rich marker: NOSF_TUNE:feature:V<v>:W<w>:H<h> (Q:<q>)
MARKER_RE = re.compile(r"NOSF_TUNE:([^:]+):V([\d.]+):W([\d.]+):H([\d.]+)\s*\(Q:([\d.]+)\)")

def log_watcher(log_path):
    if not os.path.exists(log_path):
        print(f"[!] Klipper log {log_path} not found.")
        return

    print(f"[*] Watching Klipper log for flow markers: {log_path}")
    with open(log_path, 'r', errors='ignore') as f:
        f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1); continue
            
            match = MARKER_RE.search(line)
            if match:
                feature, v, w, h, q = match.groups()
                with sync_context['lock']:
                    sync_context['feature'] = feature
                    sync_context['v_fil'] = float(v)
                    sync_context['width'] = float(w)
                    sync_context['height'] = float(h)
                    sync_context['flow'] = float(q)
                # print(f"[*] Sync: {feature} | V={v} | Q={q} | W={w} H={h}")

# --- Serial Helpers ---
def find_port():
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    return ports[0] if ports else None

def send_wait(ser, cmd, timeout=2.0):
    ser.reset_input_buffer()
    ser.write(f"{cmd}\n".encode())
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if not line: continue
        if line.startswith('OK') or line.startswith('ER'): return line
    return None

def get_status(ser):
    line = send_wait(ser, "?")
    if not line or not line.startswith("OK:"): return None
    data = {}
    parts = line[3:].split(',')
    for p in parts:
        if ':' in p:
            k, v = p.split(':', 1); data[k] = v
    return data

# --- Core Logic ---

def run_collection(args, ser):
    lane = args.lane
    output_file = args.output or f"sg_tuner_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    if args.klipper_log:
        threading.Thread(target=log_watcher, args=(args.klipper_log,), daemon=True).start()

    print(f"[*] Recording StallGuard data to {output_file} ...")
    print(f"[!] Target Lane: {lane}, SGT Range: [{args.sgt_min}, {args.sgt_max}]")
    
    send_wait(ser, f"T:{lane}")
    fieldnames = ['timestamp_ms', 'sps_mm_min', 'sgt', 'sg_raw', 'buf_state', 'task', 
                  'feature', 'v_target', 'width', 'height', 'flow']
    
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        current_sgt = args.sgt_min
        last_sgt_change = 0
        sgt_dir = 1
        t0 = time.time()
        
        try:
            while True:
                now = time.time()
                status = get_status(ser)
                if not status: continue
                
                task = status.get(f'L{lane}T', 'IDLE')
                buf_state = status.get('BUF', 'MID')
                speed = float(status.get('SPS', 0))
                sg_raw = int(status.get(f'SG{lane}', 0))
                
                with sync_context['lock']:
                    feat, vt, w, h, q = (sync_context['feature'], sync_context['v_fil'], 
                                         sync_context['width'], sync_context['height'], sync_context['flow'])

                writer.writerow({
                    'timestamp_ms': int((now - t0) * 1000),
                    'sps_mm_min': speed,
                    'sgt': current_sgt,
                    'sg_raw': sg_raw,
                    'buf_state': buf_state,
                    'task': task,
                    'feature': feat,
                    'v_target': vt,
                    'width': w,
                    'height': h,
                    'flow': q
                })
                csvfile.flush()

                # Adaptive SGT sweeping
                if (now - last_sgt_change) >= args.step_interval:
                    if task == 'FEED' and buf_state != 'TRAILING' and speed > 100:
                        current_sgt += sgt_dir
                        if current_sgt > args.sgt_max: current_sgt = args.sgt_max; sgt_dir = -1
                        elif current_sgt < args.sgt_min: current_sgt = args.sgt_min; sgt_dir = 1
                        
                        send_wait(ser, f"SET:SGT_L{lane}:{current_sgt}")
                        last_sgt_change = now
                        time.sleep(args.settle_ms / 1000.0)
                
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\n[!] Collection stopped.")
            
    return output_file

def physics_model(v, p0, p1, ke):
    omega = (v / 60.0) * (2.0 * np.pi / 10.0)
    return np.maximum(p0 * (1.0 - ke * omega / V_SUPPLY) + p1, 0)

def run_analysis(args, data_file):
    print(f"[*] Analyzing {data_file} ...")
    samples = []
    with open(data_file, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['task'] == 'FEED' and float(row['sps_mm_min']) > 100:
                if int(row['sg_raw']) > 0: samples.append(row)
    
    if len(samples) < 20:
        print("[!] Not enough data."); return

    speeds = np.array([float(s['sps_mm_min']) for s in samples])
    flows = np.array([float(s['flow']) for s in samples])
    sgrs = np.array([int(s['sg_raw']) for s in samples])
    
    # ML Refinement: Multi-variate analysis
    # We want to see how SG responds to Flow (Q) independently of Speed (V).
    # Since Q = V * Area, they are perfectly correlated for a single nozzle.
    # However, if we have multiple features (Infill vs Wall), the geometry (W/H) might differ.
    
    motor = None # Placeholder for motor loading if args.motor provided
    
    # Global Fit
    try:
        popt, _ = curve_fit(lambda v, p0, p1: physics_model(v, p0, p1, 0.005), speeds, sgrs, p0=[1000, 0])
        model_fn = lambda v: physics_model(v, *popt, 0.005)
    except:
        p = np.polyfit(speeds, sgrs, 1); model_fn = lambda v: p[0] * v + p[1]

    residuals = sgrs - model_fn(speeds)
    std_dev = np.std(residuals)
    
    print(f"\n--- Analysis Results (Flow-Aware) ---")
    print(f"Avg Volumetric Flow: {np.mean(flows):.2f} mm3/s")
    print(f"Noise Floor (σ): {std_dev:.2f} SG units")
    
    # Feature-based breakdown
    feats = {}
    for s in samples:
        f = s['feature']
        if f not in feats: feats[f] = []
        feats[f].append(int(s['sg_raw']))
    
    print(f"\nFeature-specific Load (Mean SG):")
    for f, vals in feats.items():
        if f == 'Unknown': continue
        print(f"  {f:20s}: {np.mean(vals):.1f}")

    print(f"\nRecommended SGT values (Sensitivity Target: 200-400):")
    print(f"{'Speed (mm/min)':>15} | {'Flow (mm3/s)':>12} | {'Rec SGT':>10}")
    print("-" * 45)
    for v in [500, 1000, 2000, 3000]:
        mean_sg = model_fn(v)
        # Flow calculation for reference (assuming 1.75mm fil)
        q = (v / 60.0) * (math.pi * (1.75/2)**2)
        rec_sgt = int(max(1, (mean_sg - 3 * std_dev) / 2.5))
        print(f"{v:15d} | {q:12.1f} | {rec_sgt:10d}")

def main():
    parser = argparse.ArgumentParser(description="NOSF StallGuard Sync Tuner")
    parser.add_argument("--port", help="Serial port")
    parser.add_argument("--lane", type=int, choices=[1, 2], default=1)
    parser.add_argument("--output", help="CSV file")
    parser.add_argument("--sgt-min", type=int, default=-20)
    parser.add_argument("--sgt-max", type=int, default=20)
    parser.add_argument("--step-interval", type=float, default=2.0)
    parser.add_argument("--settle-ms", type=int, default=300)
    parser.add_argument("--analyze-only", help="Analyze existing CSV")
    parser.add_argument("--motor", help="Motor name from database")
    parser.add_argument("--motors-db", default="scripts/motors.ini")
    parser.add_argument("--klipper-log", help="Path to Klipper log")

    args = parser.parse_args()

    if args.analyze_only: run_analysis(args, args.analyze_only); return

    port = args.port or find_port()
    if not port: print("[!] No port found."); sys.exit(1)
    try:
        ser = serial.Serial(port, 115200, timeout=0.5); time.sleep(2)
        data_file = run_collection(args, ser)
        run_analysis(args, data_file)
    finally:
        if 'ser' in locals(): ser.close()

if __name__ == "__main__":
    main()
