#!/usr/bin/env python3
"""
NOSF — StallGuard Auto-Tuner (Flexible Baseline Loader)

**Design Philosophy:**
This tool was developed to relax buffer syncing during RELOAD follow and tune StallGuard
as the primary filament pressure sensor. During normal operation, the buffer arm is the
"canary" — it signals when the filament is running low. During SG tuning, we want to let
SG itself drive the pressure maintenance instead, so we can characterize how well SG alone
can keep the filament tip in contact without relying on buffer feedback oscillations.

**Workflow:**
The tuner sweeps SGTHRS (StallGuard threshold) across a range while collecting real-time
SG raw values, motor speed, and buffer state. The sweep helps identify the optimal SGTHRS
where SG maintains gentle contact pressure without false triggers.

**Safety Fallbacks:**
If the buffer remains in ADVANCE or TRAILING state for too long, it means the SG tuner
has not converged (can't maintain filament contact with current SGTHRS). In such cases:
  - Data collection continues but marks the event
  - Operator should pause (Ctrl+C), review CSV for divergence patterns, then adjust
    --sgthrs-offset-min/max to narrow the search window and retry
  - Before resuming with new SGTHRS range, manual stabilization (run UL+UL_MMU cycle)
    is recommended to clear any jams from failed contact attempts

**Features:**
  - AUTO-ENABLES SYNC_SG_INTERP during tuning (send SET:SYNC_SG_INTERP:1)
  - Sweeps SGTHRS in a range (configurable offset from baseline)
  - Collects real-time status (SPS, SG raw, buffer state)
  - Monitors Klipper log for test feature markers (NOSF_TUNE:FeatureName:Vvalue)
  - Thread-safe logging; can be stopped mid-run

**Usage:**
  1. Copy your baseline motor config to scripts/motors.ini
  2. Run: python3 scripts/sg_tuner.py --baseline=your_motor_name --klipper-log=~/printer_data/logs/klippy.log
  3. Start a G-code test macro that writes NOSF_TUNE markers to the Klipper log
  4. Stop manually: Ctrl+C, or write NOSF_TUNE:FINISH to the Klipper log (e.g., from a macro)
  5. Output CSV contains timestamp, speed, SG raw, buffer state, and test features for analysis

**Stop Mechanisms:**
  - Ctrl+C: immediate KeyboardInterrupt → exit gracefully
  - Log marker: Write "NOSF_TUNE:FINISH" to Klipper log → sets stop_requested flag
  Both are safe; collection will complete final CSV row and close cleanly.

**Baseline Format Support:**
  - [tuning_baseline motor_name] (space-separated)
  - [tuning_baseline_motor_name] (underscore-separated)
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
# Thread-safe context for log watcher to communicate with main collection loop
sync_context = {
    'feature': 'Unknown',      # Last NOSF_TUNE feature name from log
    'v_fil': 0,                # Last NOSF_TUNE value from log
    'stop_requested': False,   # Set to True by log_watcher or KeyboardInterrupt
    'lock': threading.Lock()   # Protects feature and v_fil updates
}

# Regex for Klipper log markers: NOSF_TUNE:FeatureName:V123.45 → ('FeatureName', 123.45)
MARKER_RE = re.compile(r"NOSF_TUNE:([^:]+):V?([\d.]+)")

def log_watcher(log_path):
    """
    Thread: Tails the Klipper log file looking for NOSF_TUNE markers.
    
    Markers supported:
      - NOSF_TUNE:FeatureName:V123.45 → updates sync_context['feature'] and sync_context['v_fil']
      - NOSF_TUNE:FINISH → sets sync_context['stop_requested'] = True (stops tuning)
    
    If log_path doesn't exist, returns immediately (no-op).
    Runs as daemon thread, so won't block program exit.
    """
    if not os.path.exists(log_path):
        print(f"[!] Klipper log not found: {log_path} — tuning will proceed without log markers")
        return
    
    print(f"[*] Monitoring log: {log_path}")
    with open(log_path, 'r', errors='ignore') as f:
        f.seek(0, 2)  # Jump to EOF
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue
            
            # Check for explicit stop marker
            if "NOSF_TUNE:FINISH" in line:
                print("[*] Log watcher saw NOSF_TUNE:FINISH → stopping tuning")
                sync_context['stop_requested'] = True
                break
            
            # Check for feature marker
            match = MARKER_RE.search(line)
            if match:
                feature, v = match.group(1), match.group(2)
                with sync_context['lock']:
                    sync_context['feature'], sync_context['v_fil'] = feature, float(v)
                print(f"    [log] {feature} = {v}")

# --- Knowledge Base Helpers ---

def load_baseline_config(args):
    """
    Loads motor baseline from motors.ini.
    
    Supports flexible section naming:
      - [tuning_baseline motor_name]      (space)
      - [tuning_baseline_motor_name]      (underscore)
    
    Baseline sections contain:
      - sgthrs_nominal: center SGTHRS value for sweep
      - driver_tbl, driver_toff, driver_hstrt, driver_hend: TMC chopper params
      - sg_current_ma: current for SG tuning (typically 800–1000 mA)
    
    Returns dict of baseline config, or {} if not found.
    """
    config = configparser.ConfigParser()
    if not os.path.exists(args.motors_db):
        print(f"[!] Motors DB not found: {args.motors_db}")
        return {}
    
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
    """Find first available NOSF controller serial port."""
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    return ports[0] if ports else None

def send_wait(ser, cmd):
    """Send a command to the controller and wait for it to settle."""
    ser.write(f"{cmd}\n".encode())
    time.sleep(0.1)

def get_status(ser):
    """
    Query controller status via '?' command.
    Returns dict of parameters (e.g., {'SPS': '2100', 'BUF': 'MID', 'SG1': '42'}).
    Returns None if status parse fails.
    """
    ser.reset_input_buffer()
    ser.write(b"?\n")
    line = ser.readline().decode('utf-8', errors='ignore').strip()
    if not line.startswith("OK:"): return None
    data = {}
    for p in line[3:].split(','):
        if ':' in p:
            k, v = p.split(':', 1)
            data[k] = v
    return data

def run_collection(args, ser, baseline=None):
    """
    Main SGTHRS tuning collection loop.
    
    Flow:
      1. Enable SYNC_SG_INTERP on the controller (so SG is primary pressure feedback)
      2. Enable sync (SM:1)
      3. Apply baseline motor params (chopper, SG current) if provided
      4. Start SGTHRS sweep: min to max, then back, following task/speed conditions
      5. Poll controller status every 50ms → collect SPS, SG raw, buffer state
      6. If log_path provided, tail it for NOSF_TUNE markers and stop signal
      7. Write CSV with timestamp, speed, SGTHRS, SG raw, buffer, feature, v_target
    
    Stop conditions:
      - KeyboardInterrupt (Ctrl+C)
      - Log watcher sees "NOSF_TUNE:FINISH"
    
    **Interpreting Results (CSV columns):**
      - buf_state: MID (neutral), ADVANCE (extruder pulling ahead), TRAILING (buffer full)
        * Good tuning: mostly MID with occasional brief ADVANCE dips (quickly recovered)
        * Bad tuning: sustained ADVANCE or TRAILING (SG can't maintain contact / SG too high)
      - sg_raw: StallGuard value (0–255, inverse to motor load; lower = more load = stronger stall signal)
        * Healthy contact: sg_raw oscillates in narrow range (e.g., 20–40)
        * No contact: sg_raw very high (>100); filament slipping through drive
        * Overconstrained: sg_raw saturated low (<5); filament pushing hard
      - sps_mm_min: motor speed (should track sync speed setpoint; drops on stall)
    
    **Safety Note - When Buffer Stays Locked:**
    If buf_state remains ADVANCE or TRAILING for >10–20 seconds, the SGTHRS is not
    converging (SG feedback is not maintaining filament contact). This indicates:
      - SGTHRS too low (not sensitive enough) → filament slips, buffer goes ADVANCE
      - SGTHRS too high (too sensitive) → false stalls, motor stops, buffer goes TRAILING
    
    When this occurs, stop tuning (Ctrl+C), review the CSV for the divergence point,
    then retry with a narrower offset range (e.g., --sgthrs-offset-min=-5 --sgthrs-offset-max=5).
    Before resuming tuning with new SGTHRS range, manually stabilize the buffer:
      - Run UL (unload) → clears filament from cartridge
      - Run UL_MMU (unload to MMU) → ensures buffer returns to MID
      - This prevents tuning from starting with filament jammed in a bad contact state.
    
    Args:
      args: command-line args (lane, output, step_interval, klipper_log, motors_db, sgthrs_offset_*)
      ser: serial.Serial object connected to controller
      baseline: dict of motor config (from load_baseline_config)
    
    Returns:
      Path to output CSV file
    """
    lane = args.lane
    output_file = args.output or f"sg_tuner_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    # 1. Enable SYNC_SG_INTERP — tells controller to use SG during normal sync
    print("[*] Enabling SYNC_SG_INTERP for tuning...")
    send_wait(ser, "SET:SYNC_SG_INTERP:1")
    send_wait(ser, "SM:1")  # Enable sync
    
    
    if baseline:
        # 2. Apply baseline motor configuration (chopper tuning, SG current)
        for k in ['driver_tbl', 'driver_toff', 'driver_hstrt', 'driver_hend']:
            if k in baseline:
                send_wait(ser, f"SET:{k.upper()}:{baseline[k]}")
        sg_ma = baseline.get('sg_current_ma', '800')
        send_wait(ser, f"SET:SG_CURRENT_MA_L{lane}:{sg_ma}")

    # 3. Configure SGTHRS sweep range (center ± offset)
    sgthrs_center = int(baseline.get('sgthrs_nominal', 0)) if baseline else 0
    sgthrs_min, sgthrs_max = sgthrs_center + args.sgthrs_offset_min, sgthrs_center + args.sgthrs_offset_max

    # 4. Start log watcher thread if Klipper log path provided
    if args.klipper_log:
        threading.Thread(target=log_watcher, args=(args.klipper_log,), daemon=True).start()

    print(f"[*] SGTHRS Sweep: [{sgthrs_min}, {sgthrs_max}] every {args.step_interval}s")
    
    # 5. Open CSV and begin collection loop
    fieldnames = ['timestamp_ms', 'sps_mm_min', 'sgthrs', 'sg_raw', 'buf_state', 'feature', 'v_target']
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        # Sweep direction: 1 = up (min→max), -1 = down (max→min)
        current_sgthrs, last_sgthrs_change, sgthrs_dir, t0 = sgthrs_center, 0, 1, time.time()
        
        try:
            while not sync_context['stop_requested']:
                status = get_status(ser)
                if not status:
                    continue
                
                now = time.time()
                speed, sg_raw = float(status.get('SPS', 0)), int(status.get(f'SG{lane}', 0))
                buf_state = status.get('BUF', 'MID')
                task = status.get(f'L{lane}T', 'IDLE')
                with sync_context['lock']:
                    feat, vt = sync_context['feature'], sync_context['v_fil']

                # Record current sample to CSV
                writer.writerow({
                    'timestamp_ms': int((now-t0)*1000),
                    'sps_mm_min': speed,
                    'sgthrs': current_sgthrs,
                    'sg_raw': sg_raw,
                    'buf_state': buf_state,
                    'feature': feat,
                    'v_target': vt
                })
                csvfile.flush()

                # Sweep control: every step_interval seconds, change SGTHRS if motor is actively feeding
                # Only sweep during active feed (task==FEED and speed>100) to collect meaningful SG data
                if (now - last_sgthrs_change) >= args.step_interval:
                    if task == 'FEED' and speed > 100:
                        # Increment/decrement SGTHRS, bounce at min/max (sawtooth sweep)
                        current_sgthrs += sgthrs_dir
                        if current_sgthrs > sgthrs_max:
                            current_sgthrs = sgthrs_max
                            sgthrs_dir = -1  # Reverse direction (sweep back down)
                        elif current_sgthrs < sgthrs_min:
                            current_sgthrs = sgthrs_min
                            sgthrs_dir = 1   # Reverse direction (sweep back up)
                        
                        # Send new SGTHRS value to controller
                        send_wait(ser, f"SET:SGTHRS_L{lane}:{current_sgthrs}")
                        last_sgthrs_change = now
                
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("[*] KeyboardInterrupt: stopping tuning.")
        
    return output_file

def main():
    """
    Parse arguments and run tuning collection.
    
    Command-line arguments:
      --lane                 Motor lane (1 or 2). Default: 1
      --baseline             Motor name or baseline ID to load from motors.ini.
                             Used to fetch sgthrs_nominal, chopper params, SG current.
      --sgthrs-offset-min    SGTHRS sweep offset from nominal (negative). Default: -10
      --sgthrs-offset-max    SGTHRS sweep offset from nominal (positive). Default: +10
      --step-interval        Seconds between SGTHRS changes. Default: 1.0
      --klipper-log          Path to Klipper log for tailing NOSF_TUNE markers.
                             Default: ~/printer_data/logs/klippy.log
                             (Use --klipper-log="" to disable log tailing.)
      --motors-db            Path to motors.ini baseline database. Default: scripts/motors.ini
      --output               Output CSV file path. Default: sg_tuner_data_<timestamp>.csv
    
    Examples:
      # Tune Lane 1 with E3D Revo baseline, save to custom CSV
      python3 scripts/sg_tuner.py --baseline=e3d_revo --output=tune_revo.csv
      
      # Tune Lane 2, no Klipper log tailing
      python3 scripts/sg_tuner.py --lane=2 --klipper-log=""
      
      # Tune with wider sweep range
      python3 scripts/sg_tuner.py --baseline=my_motor --sgthrs-offset-min=-20 --sgthrs-offset-max=20
    """
    parser = argparse.ArgumentParser(description="NOSF StallGuard Tuner")
    parser.add_argument("--lane", type=int, default=1,
                        help="Motor lane (1 or 2)")
    parser.add_argument("--baseline", 
                        help="Motor name or baseline ID from motors.ini")
    parser.add_argument("--sgthrs-offset-min", type=int, default=-10,
                        help="SGTHRS sweep min offset from nominal")
    parser.add_argument("--sgthrs-offset-max", type=int, default=10,
                        help="SGTHRS sweep max offset from nominal")
    parser.add_argument("--step-interval", type=float, default=1.0,
                        help="Seconds between SGTHRS step changes")
    parser.add_argument("--klipper-log", 
                        default=os.path.expanduser("~/printer_data/logs/klippy.log"),
                        help="Klipper log path for NOSF_TUNE markers. Default: ~/printer_data/logs/klippy.log")
    parser.add_argument("--motors-db", default="scripts/motors.ini",
                        help="Motor baselines database")
    parser.add_argument("--output",
                        help="Output CSV file (default: sg_tuner_data_<timestamp>.csv)")

    args = parser.parse_args()
    
    # Expand ~ in klipper log path if provided
    if args.klipper_log:
        args.klipper_log = os.path.expanduser(args.klipper_log)
    
    port = find_port()
    if not port:
        print("[!] No NOSF controller found on USB. Ensure it's connected.")
        sys.exit(1)
    
    try:
        ser = serial.Serial(port, 115200, timeout=0.5)
        time.sleep(2)  # Wait for controller to initialize
        
        baseline = load_baseline_config(args)
        csv_file = run_collection(args, ser, baseline)
        print(f"\n[*] Tuning complete. Data saved to: {csv_file}")
    except KeyboardInterrupt:
        print("\n[*] Tuning stopped by user.")
    finally:
        if 'ser' in locals():
            ser.close()

if __name__ == "__main__":
    main()
