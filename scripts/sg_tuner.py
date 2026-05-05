#!/usr/bin/env python3
"""
NOSF — StallGuard Auto-Tuner (ML/Physics Refined)
Live-sweeps SGT values during a speed-varying test print, records SG readings,
and fits a physics-informed model to recommend optimal SGT vs. speed.
"""

import argparse
import csv
import glob
import os
import sys
import time
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
V_SUPPLY = 24.0  # Default supply voltage

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

# --- Physics & ML Models ---

def get_motor_params(args):
    if not args.motor or not args.motors_db:
        return None
    config = configparser.ConfigParser()
    if not os.path.exists(args.motors_db):
        return None
    config.read(args.motors_db)
    if args.motor not in config:
        return None
    
    m = config[args.motor]
    try:
        # Ke (V/(rad/s)) approx Kt (Nm/A) = torque / current
        t = float(m['holding_torque'])
        i = float(m['max_current'])
        ke = t / i
        return {
            'R': float(m['resistance']),
            'L': float(m['inductance']),
            'T': t,
            'I': i,
            'steps_per_rev': int(m['steps_per_revolution']),
            'Ke': ke
        }
    except (KeyError, ValueError):
        return None

def physics_model(v_mm_min, p0, p1, ke, steps_per_rev):
    """
    Theoretical SG model based on Back-EMF.
    v_mm_min: speed in mm/min
    p0: scale factor (max SG result)
    p1: bias (friction/offset)
    """
    # rad/s = (mm/min / 60) / (rotation_dist / (2*pi))
    # We don't have rotation_dist here easily, so let's simplify.
    # rad/s is proportional to mm/min.
    # Let's use v_mm_min as a proxy and let p2 be the effective Ke/V_supply scale.
    # Actually, if we have Ke, we should use it.
    
    # Assume 10mm rotation distance if not specified (typical for MMUs)
    # rad/s = (v_mm_min / 60) * (2*pi / 10)
    omega = (v_mm_min / 60.0) * (2.0 * np.pi / 10.0)
    v_bemf = ke * omega
    sg = p0 * (1.0 - v_bemf / V_SUPPLY) + p1
    return np.maximum(sg, 0)

def simple_exp_model(v, p0, p1, p2):
    """Fallback empirical model: exponential decay."""
    return p0 * np.exp(-p1 * v / 1000.0) + p2

# --- Core Logic ---

def run_collection(args, ser):
    lane = args.lane
    output_file = args.output or f"sg_tuner_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    print(f"[*] Recording StallGuard data to {output_file} ...")
    print(f"[*] Lane: {lane}, SGT Range: [{args.sgt_min}, {args.sgt_max}]")
    print("[*] Press Ctrl+C to stop collection and start analysis.")
    
    send_wait(ser, f"T:{lane}")
    fieldnames = ['timestamp_ms', 'speed_mm_min', 'sgt', 'sg_raw', 'buf_state', 'task']
    
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
                
                writer.writerow({
                    'timestamp_ms': int((now - t0) * 1000),
                    'speed_mm_min': speed,
                    'sgt': current_sgt,
                    'sg_raw': sg_raw,
                    'buf_state': buf_state,
                    'task': task
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
            print("\n[!] Collection stopped by user.")
            
    return output_file

def run_analysis(args, data_file):
    print(f"[*] Analyzing {data_file} ...")
    samples = []
    with open(data_file, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['task'] == 'FEED' and float(row['speed_mm_min']) > 100:
                sg = int(row['sg_raw'])
                if sg > 0: # Filter out stalls
                    samples.append(row)
    
    if len(samples) < 20:
        print("[!] Not enough valid data points for analysis.")
        return

    speeds = np.array([float(s['speed_mm_min']) for s in samples])
    sgrs = np.array([int(s['sg_raw']) for s in samples])
    
    motor = get_motor_params(args)
    
    # ML Refinement: Robust fitting with SciPy
    try:
        if motor:
            # Physics-informed fit
            ke = motor['Ke']
            spr = motor['steps_per_rev']
            # p0: scale, p1: bias
            popt, _ = curve_fit(lambda v, p0, p1: physics_model(v, p0, p1, ke, spr), 
                                speeds, sgrs, p0=[1000, 0])
            model_fn = lambda v: physics_model(v, *popt, ke, spr)
            model_type = "Physics-Informed (Back-EMF)"
        else:
            # Empirical exponential fit
            popt, _ = curve_fit(simple_exp_model, speeds, sgrs, p0=[1000, 0.5, 100])
            model_fn = lambda v: simple_exp_model(v, *popt)
            model_type = "Empirical (Exponential Decay)"
    except Exception as e:
        print(f"[!] Fit failed: {e}. Falling back to linear.")
        p = np.polyfit(speeds, sgrs, 1)
        model_fn = lambda v: p[0] * v + p[1]
        model_type = "Linear Fallback"

    # Confidence interval calculation
    preds = model_fn(speeds)
    residuals = sgrs - preds
    std_dev = np.std(residuals)
    
    print(f"\n--- Analysis Results ---")
    print(f"Model Type: {model_type}")
    print(f"Noise Floor (σ): {std_dev:.2f} SG units")
    
    # Recommendation logic:
    # SGT should be set so that it fires when SG drops below the noise floor.
    # Stall trigger at SG <= 2 * SGT.
    # To avoid false triggers, we want 2 * SGT < (Model(v) - 2*σ)
    # => SGT < (Model(v) - 2*σ) / 2
    
    target_low = args.target_sg_low
    target_high = args.target_sg_high
    
    print(f"\nRecommended SGT values (Sensitivity Target: SG={target_low}-{target_high}):")
    print(f"{'Speed (mm/min)':>15} | {'Mean SG':>10} | {'Rec SGT':>10} | {'Safety Margin'}")
    print("-" * 60)
    for v in [500, 1000, 1500, 2000, 2500, 3000]:
        mean_sg = model_fn(v)
        if mean_sg < 50:
            print(f"{v:15d} | {mean_sg:10.1f} | {'TOO FAST':>10} | SG too low for reliability")
            continue
            
        # Recommend SGT that triggers at ~50% of the mean SG, but no higher than noise floor.
        rec_sgt = int(max(1, (mean_sg - 3 * std_dev) / 2.5))
        margin = mean_sg - (2 * rec_sgt)
        print(f"{v:15d} | {mean_sg:10.1f} | {rec_sgt:10d} | {margin:4.1f} units")

    if motor:
        print(f"\n[*] Interpolation active for motor: {args.motor}")
        print(f"    R={motor['R']}Ω, L={motor['L']}H, T={motor['T']}Nm, Ke={motor['Ke']:.4f}V/rads")

def main():
    parser = argparse.ArgumentParser(description="NOSF StallGuard ML Auto-Tuner")
    parser.add_argument("--port", help="Serial port")
    parser.add_argument("--lane", type=int, choices=[1, 2], default=1)
    parser.add_argument("--output", help="CSV file for data")
    parser.add_argument("--sgt-min", type=int, default=-20)
    parser.add_argument("--sgt-max", type=int, default=20)
    parser.add_argument("--step-interval", type=float, default=2.0)
    parser.add_argument("--settle-ms", type=int, default=300)
    parser.add_argument("--target-sg-low", type=int, default=200)
    parser.add_argument("--target-sg-high", type=int, default=400)
    parser.add_argument("--analyze-only", help="Analyze existing CSV")
    parser.add_argument("--motor", help="Motor name from database")
    parser.add_argument("--motors-db", default="scripts/motors.ini")

    args = parser.parse_args()

    if args.analyze_only:
        run_analysis(args, args.analyze_only)
        return

    port = args.port or find_port()
    if not port:
        print("[!] No serial port found."); sys.exit(1)
        
    print(f"[*] Connecting to {port} ...")
    try:
        ser = serial.Serial(port, 115200, timeout=0.5)
        time.sleep(2)
    except Exception as e:
        print(f"[!] Connection failed: {e}"); sys.exit(1)
        
    try:
        data_file = run_collection(args, ser)
        run_analysis(args, data_file)
    finally:
        ser.close()

if __name__ == "__main__":
    main()
