#!/usr/bin/env python3
import argparse
import serial
import time
import sys
import glob

def find_serial_port():
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    if not ports:
        print("Error: No serial port found. Please specify with --port.")
        sys.exit(1)
    return ports[0]

def send_cmd(ser, cmd):
    ser.write(f"{cmd}\n".encode('utf-8'))
    # We don't block on OK here, as we mainly use this for SET/FD/ST
    time.sleep(0.05)

def read_sg(ser, lane):
    # Flush input buffer to clear old events
    ser.reset_input_buffer()
    ser.write(f"SG:{lane}\n".encode('utf-8'))
    
    timeout = time.time() + 1.0
    while time.time() < timeout:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith(f"OK:{lane}:"):
                try:
                    return int(line.split(':')[2])
                except ValueError:
                    pass
    return -1

def run_neutral_profiling(ser, lane):
    print("\n--- PHASE 1: Neutral Profiling (Mid-Air) ---")
    print("Ensure filament is loaded past the MMU, but NOT in the extruder.")
    input("Press Enter to begin...")

    send_cmd(ser, f"T:{lane}")
    send_cmd(ser, "SM:0") # Disable sync

    # Test speeds from 600 mm/min (10mm/s) to 3000 mm/min (50mm/s)
    speeds = range(600, 3001, 600)
    results = {}

    for speed in speeds:
        print(f"Testing speed {speed} mm/min...", end="", flush=True)
        send_cmd(ser, f"SET:FEED:{speed}")
        send_cmd(ser, "FD:") # Start continuous feed
        
        time.sleep(1.0) # Let speed stabilize and ramp up
        
        sgs = []
        for _ in range(10):
            val = read_sg(ser, lane)
            if val != -1:
                sgs.append(val)
            time.sleep(0.1)
            
        send_cmd(ser, "ST:") # Stop
        time.sleep(0.5)
        
        if sgs:
            avg_sg = sum(sgs) / len(sgs)
            results[speed] = avg_sg
            print(f" Avg SG: {avg_sg:.1f}")
        else:
            print(" Failed to read SG")

    print("\nNeutral Profile Results:")
    min_steady = 511
    for spd, sg in results.items():
        print(f"  {spd} mm/min: {sg:.1f}")
        if sg < min_steady:
            min_steady = sg
            
    recommended_thr = int(min_steady * 0.90)
    print(f"\nRecommended SG_SYNC_THR (90% of lowest steady): {recommended_thr}")
    print(f"To apply: SET:SG_SYNC_THR:{recommended_thr}")


def run_trailing_profiling(ser, lane):
    print("\n--- PHASE 2: Trailing Profiling (Compression/Jam) ---")
    print("Ensure filament is resting against STOPPED extruder gears.")
    input("Press Enter to begin...")

    send_cmd(ser, f"T:{lane}")
    send_cmd(ser, "SM:0")
    
    # Use a safe low speed for compression testing (300 mm/min = 5 mm/s)
    test_speed = 300
    send_cmd(ser, f"SET:FEED:{test_speed}")
    
    print("Pushing filament... (monitoring SG for 5 seconds)")
    # We use MV to limit the maximum travel just in case
    send_cmd(ser, f"MV:20:{test_speed}")
    
    lowest_sg = 511
    timeout = time.time() + 5.0
    
    while time.time() < timeout:
        val = read_sg(ser, lane)
        if val != -1:
            print(f"Current SG: {val}")
            if val < lowest_sg:
                lowest_sg = val
        time.sleep(0.1)
        
    send_cmd(ser, "ST:") # Force stop
    
    print(f"\nLowest SG recorded during compression: {lowest_sg}")
    recommended_comp_min = int(lowest_sg * 1.1)
    print(f"Recommended SG_COMPRESSION_MIN: {recommended_comp_min}")

def main():
    parser = argparse.ArgumentParser(description="StallGuard Tuning Script for NightOwl")
    parser.add_argument("--port", help="Serial port to connect to")
    parser.add_argument("--lane", type=int, choices=[1, 2], default=1, help="Lane to tune (1 or 2)")
    parser.add_argument("--neutral", action="store_true", help="Run Phase 1: Neutral Profiling")
    parser.add_argument("--trailing", action="store_true", help="Run Phase 2: Trailing Profiling")
    
    args = parser.parse_args()
    
    if not (args.neutral or args.trailing):
        print("Please specify a tuning phase to run (--neutral and/or --trailing)")
        sys.exit(1)
        
    port = args.port if args.port else find_serial_port()
    print(f"Connecting to {port}...")
    
    try:
        ser = serial.Serial(port, 115200, timeout=1)
        time.sleep(2) # Wait for potential reboot/init
    except Exception as e:
        print(f"Failed to connect: {e}")
        sys.exit(1)
        
    try:
        if args.neutral:
            run_neutral_profiling(ser, args.lane)
            
        if args.trailing:
            run_trailing_profiling(ser, args.lane)
            
    finally:
        send_cmd(ser, "ST:") # Ensure motors are stopped
        ser.close()

if __name__ == "__main__":
    main()
