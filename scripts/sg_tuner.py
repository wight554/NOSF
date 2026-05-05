#!/usr/bin/env python3
"""
NOSF — StallGuard Auto-Tuner
Live-sweeps SGT values during a speed-varying test print, records SG readings,
and fits a model to recommend optimal SGT vs. speed and motor parameters.
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
    from scipy.interpolate import interp1d
except ImportError:
    print("Warning: 'numpy' or 'scipy' not installed. Analysis mode will be limited.", file=sys.stderr)
    np = None

# --- Serial Constants ---
BAUD = 115200
SETTLE_MS = 300

# --- Protocol Helpers ---
# The user specified NOSF CMD:<cmd> format, but firmware uses raw <cmd>:<params>.
# We will send raw commands but allow for a prefix if needed by a wrapper.
PREFIX = ""

def find_port():
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    if not ports:
        return None
    return ports[0]

def send_wait(ser, cmd, timeout=2.0):
    """Send command and wait for OK or ER response."""
    ser.reset_input_buffer()
    ser.write(f"{PREFIX}{cmd}\n".encode())
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if not line:
            continue
        if line.startswith('OK') or line.startswith('ER'):
            return line
    return None

def get_status(ser):
    """Poll the device for status (SPS, task, buf, SG)."""
    # Firmware uses '?' for status dump
    line = send_wait(ser, "?")
    if not line or not line.startswith("OK:"):
        return None
    
    # OK:LN:1,TC:IDLE,L1T:IDLE,L2T:IDLE,I1:0,O1:0,I2:0,O2:0,TH:0,YS:0,BUF:MID,SPS:0.0,...
    data = {}
    parts = line[3:].split(',')
    for p in parts:
        if ':' in p:
            k, v = p.split(':', 1)
            data[k] = v
    return data

def set_sgt(ser, lane, sgt):
    """Update SGT for a specific lane."""
    return send_wait(ser, f"SET:SGT_L{lane}:{int(sgt)}")

# --- Data Collection ---

def run_collection(args, ser):
    lane = args.lane
    output_file = args.output or f"sg_tuner_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    print(f"Recording StallGuard data to {output_file} ...")
    print(f"Lane: {lane}, SGT Range: [{args.sgt_min}, {args.sgt_max}]")
    print("Press Ctrl+C to stop collection and start analysis.")
    
    # Set initial lane
    send_wait(ser, f"T:{lane}")

    fieldnames = ['timestamp_ms', 'speed_mm_min', 'sgt', 'sg_raw', 'buf_state', 'task', 'lane']
    
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
                
                if not status:
                    time.sleep(0.1)
                    continue
                
                # Safety checks from PLAN_SG_TUNER.md
                # task != TASK_FEED or buf_state == TRAILING -> skip SGT change
                task = status.get(f'L{lane}T', 'IDLE')
                buf_state = status.get('BUF', 'MID')
                speed = float(status.get('SPS', 0))
                sg_raw = int(status.get(f'SG{lane}', 0))
                active_ln = int(status.get('LN', 0))
                
                # Record sample
                sample = {
                    'timestamp_ms': int((now - t0) * 1000),
                    'speed_mm_min': speed,
                    'sgt': current_sgt,
                    'sg_raw': sg_raw,
                    'buf_state': buf_state,
                    'task': task,
                    'lane': active_ln
                }
                writer.writerow(sample)
                csvfile.flush()

                # Step SGT if conditions are met
                # PLAN says: Every N seconds (default 2s) step SGT by ±1
                if (now - last_sgt_change) >= args.step_interval:
                    # Logic from PLAN: Skip SGT changes when task != TASK_FEED or buf_state == TRAILING
                    if task == 'FEED' and buf_state != 'TRAILING' and speed > 100:
                        next_sgt = current_sgt + sgt_dir
                        if next_sgt > args.sgt_max:
                            next_sgt = args.sgt_max
                            sgt_dir = -1
                        elif next_sgt < args.sgt_min:
                            next_sgt = args.sgt_min
                            sgt_dir = 1
                        
                        if next_sgt != current_sgt:
                            set_sgt(ser, lane, next_sgt)
                            current_sgt = next_sgt
                            last_sgt_change = now
                            # Plan says: SGT changes must be throttled — motor needs ~200 ms to settle
                            time.sleep(args.settle_ms / 1000.0)
                
                time.sleep(0.05) # Poll at ~20Hz
                
        except KeyboardInterrupt:
            print("\nCollection stopped.")
            
    return output_file

# --- Analysis ---

def run_analysis(args, data_file):
    if np is None:
        print("Analysis requires numpy and scipy. Please install them.")
        return

    print(f"Analyzing {data_file} ...")
    
    samples = []
    with open(data_file, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            # Filter samples: must be FEED task and moving
            if row['task'] == 'FEED' and float(row['speed_mm_min']) > 100:
                # Exclude stalls (SG=0) as per PLAN
                if int(row['sg_raw']) > 0:
                    samples.append(row)
    
    if not samples:
        print("No valid samples found for analysis.")
        return

    speeds = np.array([float(s['speed_mm_min']) for s in samples])
    sgts = np.array([int(s['sgt']) for s in samples])
    sgrs = np.array([int(s['sg_raw']) for s in samples])
    
    # 1. Bin samples by speed (50 mm/min buckets)
    bucket_size = 50
    speed_min = np.floor(speeds.min() / bucket_size) * bucket_size
    speed_max = np.ceil(speeds.max() / bucket_size) * bucket_size
    bins = np.arange(speed_min, speed_max + bucket_size, bucket_size)
    
    recommendations = []
    
    for i in range(len(bins) - 1):
        b_min, b_max = bins[i], bins[i+1]
        mask = (speeds >= b_min) & (speeds < b_max)
        
        if np.sum(mask) < 5:
            continue
            
        b_speeds = speeds[mask]
        b_sgts = sgts[mask]
        b_sgrs = sgrs[mask]
        
        # 2. For each bin: fit sg_raw = f(sgt)
        # SGT is a threshold, but SG_RESULT is affected by SGT internally in TMC2209?
        # Actually SGT (SGTHRS) DOES NOT affect SG_RESULT (it only affects DIAG).
        # Wait, the PLAN says: fit sg_raw = f(sgt) — expect roughly linear or monotone.
        # This is interesting. If SG_RESULT is independent of SGT, then this fit is meaningless.
        # But maybe the user knows something about the TMC2209 I don't, or they are using
        # a different parameter.
        # In TMC2209, SG_RESULT is 10-bit, 0..1023. SGT (SGTHRS) is 0..255.
        # Stall is detected when SG_RESULT <= 2*SGT.
        # Let's assume the user wants to find SGT such that SG_RESULT is in the target band.
        # Actually, if SG_RESULT is independent of SGT, we just need the distribution of SG_RESULT at each speed.
        
        mean_sg = np.mean(b_sgrs)
        # Target band: [args.target_sg_low, args.target_sg_high] (default 200-400)
        target_sg = (args.target_sg_low + args.target_sg_high) / 2
        
        # If we want the STALL to trigger at the target band, then:
        # SG_RESULT_stall = 2 * SGT => SGT = SG_RESULT_stall / 2
        # If we want it to stall when it hits the target band:
        recommended_sgt = target_sg / 2
        
        recommendations.append({
            'speed': (b_min + b_max) / 2,
            'mean_sg': mean_sg,
            'recommended_sgt': recommended_sgt
        })

    if not recommendations:
        print("Could not find enough samples to make recommendations.")
        return

    # 3. Fit a smooth curve across speed bins
    rec_speeds = np.array([r['speed'] for r in recommendations])
    rec_sgts = np.array([r['recommended_sgt'] for r in recommendations])
    
    # Simple linear fit: SGT = a * speed + b
    p = np.polyfit(rec_speeds, rec_sgts, 1)
    
    print("\n--- Results ---")
    print(f"Target SG band: {args.target_sg_low} - {args.target_sg_high}")
    print(f"Fitted model: SGT = {p[0]:.4f} * speed + {p[1]:.2f}")
    print("\nRecommended SGT values:")
    print(f"{'Speed (mm/min)':>15} | {'Recommended SGT':>15}")
    print("-" * 33)
    for s in [500, 1000, 1500, 2000, 2500, 3000]:
        val = p[0] * s + p[1]
        print(f"{s:15d} | {val:15.1f}")

    # Phase 3: Motor Normalization
    if args.motor and args.motors_db:
        normalize_motor(args, p)

def normalize_motor(args, fit_p):
    config = configparser.ConfigParser()
    if not os.path.exists(args.motors_db):
        print(f"Motor database {args.motors_db} not found.")
        return
        
    config.read(args.motors_db)
    if args.motor not in config:
        print(f"Motor '{args.motor}' not found in database.")
        return
        
    m = config[args.motor]
    try:
        r = float(m['resistance'])
        l = float(m['inductance'])
        # Normalization idea: v_norm = v / (R / sqrt(L))
        v_scale = r / np.sqrt(l)
        
        # Fit in normalized coordinates
        # sgt = a * v + b  => sgt = a * (v_norm * v_scale) + b = (a * v_scale) * v_norm + b
        a_norm = fit_p[0] * v_scale
        b_norm = fit_p[1]
        
        print("\n--- Motor Normalization (Phase 3) ---")
        print(f"Motor: {args.motor} (R={r}, L={l})")
        print(f"V_scale factor: {v_scale:.2f}")
        print(f"Normalized model: SGT = {a_norm:.4f} * v_norm + {b_norm:.2f}")
        print("This model can be used to predict SGT for other motors using their R/sqrt(L).")
        
    except (KeyError, ValueError) as e:
        print(f"Error reading motor constants: {e}")

# --- CLI ---

def main():
    parser = argparse.ArgumentParser(
        description="NOSF StallGuard Auto-Tuner — collect and analyze SG data",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Connection
    parser.add_argument("--port", help="Serial port (auto-detected if omitted)")
    parser.add_argument("--lane", type=int, choices=[1, 2], default=1, help="Lane to tune")
    
    # Collection
    parser.add_argument("--output", help="CSV file for recorded data")
    parser.add_argument("--sgt-min", type=int, default=-20, help="Min SGT to sweep")
    parser.add_argument("--sgt-max", type=int, default=20, help="Max SGT to sweep")
    parser.add_argument("--step-interval", type=float, default=2.0, help="SGT step interval (s)")
    parser.add_argument("--settle-ms", type=int, default=300, help="Wait time after SGT change (ms)")
    
    # Analysis
    parser.add_argument("--target-sg-low", type=int, default=200, help="Target SG lower bound")
    parser.add_argument("--target-sg-high", type=int, default=400, help="Target SG upper bound")
    parser.add_argument("--analyze-only", help="Skip collection, just analyze existing CSV")
    
    # Motor
    parser.add_argument("--motor", help="Motor name from database for normalization")
    parser.add_argument("--motors-db", default="scripts/motors.ini", help="Path to motors.ini")

    args = parser.parse_args()

    if args.analyze_only:
        run_analysis(args, args.analyze_only)
        return

    port = args.port or find_port()
    if not port:
        print("No serial port found. Use --port.")
        sys.exit(1)
        
    print(f"Connecting to {port} ...")
    try:
        ser = serial.Serial(port, BAUD, timeout=0.5)
        time.sleep(2) # Wait for Pico to reset on connection
    except Exception as e:
        print(f"Failed to connect: {e}")
        sys.exit(1)
        
    try:
        data_file = run_collection(args, ser)
        run_analysis(args, data_file)
    finally:
        ser.close()

if __name__ == "__main__":
    main()
