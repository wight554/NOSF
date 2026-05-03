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

# SpreadCycle starts at ~1063 mm/min with default TCOOLTHRS=1000
# (TSTEP = 12.5 MHz / SPS; active when TSTEP <= TCOOLTHRS)
SPREADCYCLE_MIN_MM_MIN = 1200

def run_neutral_profiling(ser, lane, target_speed=2100):
    print("\n--- PHASE 1: Neutral Profiling (Free Spin) ---")
    print("Ensure filament is loaded past the MMU but NOT touching the extruder gears.")
    print(f"StallGuard is only meaningful at >={SPREADCYCLE_MIN_MM_MIN} mm/min (SpreadCycle range).")
    input("Press Enter to begin...")

    send_cmd(ser, f"T:{lane}")
    send_cmd(ser, "SM:0")

    # Test from SPREADCYCLE_MIN_MM_MIN to 3000 mm/min — all SpreadCycle territory
    speeds = range(SPREADCYCLE_MIN_MM_MIN, 3001, 300)
    results = {}

    for speed in speeds:
        print(f"Testing speed {speed} mm/min...", end="", flush=True)
        send_cmd(ser, f"SET:FEED:{speed}")
        send_cmd(ser, "FD:")

        time.sleep(1.0)  # let ramp finish (~700 ms at 3000 mm/min)

        sgs = []
        for _ in range(10):
            val = read_sg(ser, lane)
            if val != -1:
                sgs.append(val)
            time.sleep(0.1)

        send_cmd(ser, "ST:")
        time.sleep(0.5)

        if sgs:
            avg_sg = sum(sgs) / len(sgs)
            results[speed] = avg_sg
            print(f" Avg SG: {avg_sg:.1f}")
        else:
            print(" Failed to read SG")

    print("\nNeutral Profile Results:")
    for spd, sg in results.items():
        print(f"  {spd} mm/min: {sg:.1f}")

    # Base recommendation on SG at the target print/sync speed
    if results:
        nearest = min(results.keys(), key=lambda s: abs(s - target_speed))
        sg_at_target = results[nearest]
        recommended_thr = int(sg_at_target * 0.75)
        print(f"\nTarget speed {target_speed} mm/min → nearest tested {nearest} mm/min: SG = {sg_at_target:.1f}")
        print(f"Recommended SG_SYNC_THR (75% of free-spin SG at target speed): {recommended_thr}")
        print(f"  SET:SG_SYNC_THR:{recommended_thr}")
        print("Note: SG_SYNC_THR trim is inactive by default (SG_SYNC_THR:0).")
        print("      Set SG_SYNC_TRIM to the extra speed you want added at full tension,")
        print("      then run --advance to calibrate SG_TENSION_MAX.")


def run_advance_profiling(ser, lane):
    print("\n--- PHASE 2: Advance Profiling (Tension / Extruder Pulling) ---")
    print("Prerequisites:")
    print("  - Filament loaded past the toolhead, extruder engaged")
    print("  - Hotend at print temperature")
    print("  - Mainsail / Fluidd console open in a browser tab (or a second SSH session)")
    input("Press Enter when ready...")

    send_cmd(ser, f"T:{lane}")
    send_cmd(ser, "SM:0")

    baseline_speed = 900  # 15 mm/s (MMU side)
    send_cmd(ser, f"SET:FEED:{baseline_speed}")
    send_cmd(ser, "FD:")

    print(f"\nMMU motor running at {baseline_speed} mm/min (15 mm/s).")
    print("In your Klipper console, run these commands one at a time.")
    print("Each G1 move takes 4-7 s — wait for it to finish before the next:")
    print()
    print("  M83               ; relative extrusion mode")
    print("  G1 E100 F900      ; 15 mm/s — matched speed, no tension (SG baseline)")
    print("  G1 E100 F1500     ; 25 mm/s — light tension, SG should start dropping")
    print("  G1 E100 F2400     ; 40 mm/s — maximum tension, SG should reach its floor")
    print()
    print("Watch 'Lowest SG seen' below — it should drop during F1500 and F2400 moves.")
    print("If SG stays flat, see KLIPPER.md Troubleshooting section.")
    print("Press Ctrl+C to stop and record results.")
    print()

    lowest_sg = 511
    try:
        while True:
            val = read_sg(ser, lane)
            if val != -1:
                print(f"Current SG: {val:3d} | Lowest SG seen: {lowest_sg:3d}", end="\r")
                if val < lowest_sg:
                    lowest_sg = val
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    send_cmd(ser, "ST:")
    print(f"\n\nLowest SG recorded under maximum tension: {lowest_sg}")
    print(f"Recommended SG_TENSION_MAX (100% trim applied at or below this SG): {lowest_sg}")
    print(f"  SET:SG_TENSION_MAX:{lowest_sg}")
    print("SG_TENSION_MAX is the lower bound of the proportional correction window.")
    print("Full SG_SYNC_TRIM is applied when SG_RESULT drops to SG_TENSION_MAX or below.")

def main():
    parser = argparse.ArgumentParser(description="StallGuard Tuning Script for NightOwl")
    parser.add_argument("--port", help="Serial port to connect to")
    parser.add_argument("--lane", type=int, choices=[1, 2], default=1, help="Lane to tune (1 or 2)")
    parser.add_argument("--neutral", action="store_true", help="Run Phase 1: Neutral Profiling (free-spin SG → SG_SYNC_THR)")
    parser.add_argument("--advance", action="store_true", help="Run Phase 2: Advance Profiling (max tension SG → SG_TENSION_MAX)")
    parser.add_argument("--feed-speed", type=int, default=2100,
                        help="Target print/sync speed mm/min for neutral recommendation (default: 2100)")
    args = parser.parse_args()

    if not (args.neutral or args.advance):
        print("Please specify a tuning phase: --neutral and/or --advance")
        sys.exit(1)

    port = args.port if args.port else find_serial_port()
    print(f"Connecting to {port}...")

    try:
        ser = serial.Serial(port, 115200, timeout=1)
        time.sleep(2)  # wait for potential reboot/init
    except Exception as e:
        print(f"Failed to connect: {e}")
        sys.exit(1)

    try:
        if args.neutral:
            run_neutral_profiling(ser, args.lane, args.feed_speed)

        if args.advance:
            run_advance_profiling(ser, args.lane)

    finally:
        send_cmd(ser, "ST:")
        ser.close()

if __name__ == "__main__":
    main()
