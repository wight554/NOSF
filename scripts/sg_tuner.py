#!/usr/bin/env python3
"""
NOSF — StallGuard Auto-Tuner (ML/Physics/Klipper-Sync Refined)
Live-sweeps SGT values during a speed-varying test print, records SG readings,
and fits a physics-informed model to recommend optimal SGT vs. speed.
Supports G-code synchronization via Klipper console markers.
"""

import argparse
import csv
import glob
import os
import sys
import time
import threading
import configparser
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
    'target_speed': 0,
    'lock': threading.Lock()
}

def log_watcher(log_path):
    """Tails the Klipper log for NOSF_TUNE markers."""
    if not os.path.exists(log_path):
        print(f"[!] Klipper log {log_path} not found. Sync disabled.")
        return

    print(f"[*] Watching Klipper log: {log_path}")
    with open(log_path, 'r', errors='ignore') as f:
        f.seek(0, 2) # Move to end
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue
            
            # Format: // NOSF_TUNE:feature:speed
            # or: RESPOND MSG="NOSF_TUNE:feature:speed"
            if "NOSF_TUNE:" in line:
                try:
                    parts = line.split("NOSF_TUNE:")[1].strip().split(":")
                    feature = parts[0]
                    speed = float(parts[1])
                    with sync_context['lock']:
                        sync_context['feature'] = feature
                        sync_context['target_speed'] = speed
                    print(f"[*] Sync: Feature={feature}, Speed={speed:.0f}")
                except (IndexError, ValueError):
                    pass

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
        if line.startswith('OK') or line.startswith('ER'):
            return line
    return None

def get_status(ser):
    line = send_wait(ser, "?")
    if not line or not line.startswith("OK:"): return None
    data = {}
    parts = line[3:].split(',')
    for p in parts:
        if ':' in p:
            k, v = p.split(':', 1)
            data[k] = v
    return data

# --- Data Collection ---

def run_collection(args, ser):
    lane = args.lane
    output_file = args.output or f"sg_tuner_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Start log watcher thread if path provided
    if args.klipper_log:
        t = threading.Thread(target=log_watcher, args=(args.klipper_log,), daemon=True)
        t.start()

    print(f"[*] Recording StallGuard data to {output_file} ...")
    print(f"[*] Lane: {lane}, SGT Range: [{args.sgt_min}, {args.sgt_max}]")
    print("[*] Press Ctrl+C to stop collection and start analysis.")
    
    send_wait(ser, f"T:{lane}")
    fieldnames = ['timestamp_ms', 'speed_mm_min', 'sgt', 'sg_raw', 'buf_state', 'task', 'feature', 'target_speed']
    
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
                    feat = sync_context['feature']
                    t_speed = sync_context['target_speed']

                writer.writerow({
                    'timestamp_ms': int((now - t0) * 1000),
                    'speed_mm_min': speed,
                    'sgt': current_sgt,
                    'sg_raw': sg_raw,
                    'buf_state': buf_state,
                    'task': task,
                    'feature': feat,
                    'target_speed': t_speed
                })
                csvfile.flush()

                # Adaptive SGT sweeping
                if (now - last_sgt_change) >= args.step_interval:
                    if task == 'FEED' and buf_state != 'TRAILING' and speed > 100:
                        current_sgt += sgt_dir
                        if current_sgt > args.sgt_max:
                            current_sgt = args.sgt_max
                            sgt_dir = -1
                        elif current_sgt < args.sgt_min:
                            current_sgt = args.sgt_min
                            sgt_dir = 1
                        
                        send_wait(ser, f"SET:SGT_L{lane}:{current_sgt}")
                        last_sgt_change = now
                        time.sleep(args.settle_ms / 1000.0)
                
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\n[!] Collection stopped.")
            
    return output_file

# --- Analysis & Models (Unchanged except for feature metadata) ---

def get_motor_params(args):
    if not args.motor or not args.motors_db: return None
    config = configparser.ConfigParser()
    if not os.path.exists(args.motors_db): return None
    config.read(args.motors_db)
    if args.motor not in config: return None
    m = config[args.motor]
    try:
        t, i = float(m['holding_torque']), float(m['max_current'])
        return {
            'R': float(m['resistance']), 'L': float(m['inductance']),
            'T': t, 'I': i, 'steps_per_rev': int(m['steps_per_revolution']),
            'Ke': t / i
        }
    except (KeyError, ValueError): return None

def physics_model(v, p0, p1, ke):
    omega = (v / 60.0) * (2.0 * np.pi / 10.0)
    return np.maximum(p0 * (1.0 - ke * omega / V_SUPPLY) + p1, 0)

def run_analysis(args, data_file):
    print(f"[*] Analyzing {data_file} ...")
    samples = []
    features_seen = set()
    with open(data_file, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['task'] == 'FEED' and float(row['speed_mm_min']) > 100:
                if int(row['sg_raw']) > 0:
                    samples.append(row)
                    features_seen.add(row.get('feature', 'Unknown'))
    
    if len(samples) < 20:
        print("[!] Not enough data."); return

    speeds = np.array([float(s['speed_mm_min']) for s in samples])
    sgrs = np.array([int(s['sg_raw']) for s in samples])
    
    motor = get_motor_params(args)
    try:
        if motor:
            popt, _ = curve_fit(lambda v, p0, p1: physics_model(v, p0, p1, motor['Ke']), speeds, sgrs, p0=[1000, 0])
            model_fn = lambda v: physics_model(v, *popt, motor['Ke'])
        else:
            popt, _ = curve_fit(lambda v, p0, p1, p2: p0 * np.exp(-p1 * v / 1000.0) + p2, speeds, sgrs, p0=[1000, 0.5, 100])
            model_fn = lambda v: popt[0] * np.exp(-popt[1] * v / 1000.0) + popt[2]
    except:
        p = np.polyfit(speeds, sgrs, 1); model_fn = lambda v: p[0] * v + p[1]

    std_dev = np.std(sgrs - model_fn(speeds))
    print(f"\n--- Global Analysis ---")
    print(f"Noise Floor (σ): {std_dev:.2f} SG units")
    print(f"Features mapped: {', '.join(features_seen)}")
    
    print(f"\nRecommended SGT values:")
    print(f"{'Speed (mm/min)':>15} | {'Mean SG':>10} | {'Rec SGT':>10}")
    print("-" * 45)
    for v in [500, 1000, 1500, 2000, 2500, 3000]:
        mean_sg = model_fn(v)
        rec_sgt = int(max(1, (mean_sg - 3 * std_dev) / 2.5))
        print(f"{v:15d} | {mean_sg:10.1f} | {rec_sgt:10d}")

def main():
    parser = argparse.ArgumentParser(description="NOSF StallGuard Sync Tuner")
    parser.add_argument("--port", help="Serial port")
    parser.add_argument("--lane", type=int, choices=[1, 2], default=1)
    parser.add_argument("--output", help="CSV file")
    parser.add_argument("--sgt-min", type=int, default=-20)
    parser.add_argument("--sgt-max", type=int, default=20)
    parser.add_argument("--step-interval", type=float, default=2.0)
    parser.add_argument("--settle-ms", type=int, default=300)
    parser.add_argument("--target-sg-low", type=int, default=200)
    parser.add_argument("--target-sg-high", type=int, default=400)
    parser.add_argument("--analyze-only", help="Analyze existing CSV")
    parser.add_argument("--motor", help="Motor name from database")
    parser.add_argument("--motors-db", default="scripts/motors.ini")
    parser.add_argument("--klipper-log", help="Path to Klipper log for sync markers (e.g. /tmp/printer)")

    args = parser.parse_args()

    if args.analyze_only:
        run_analysis(args, args.analyze_only); return

    port = args.port or find_port()
    if not port: print("[!] No port."); sys.exit(1)
    try:
        ser = serial.Serial(port, 115200, timeout=0.5); time.sleep(2)
        data_file = run_collection(args, ser)
        run_analysis(args, data_file)
    finally:
        if 'ser' in locals(): ser.close()

if __name__ == "__main__":
    main()
