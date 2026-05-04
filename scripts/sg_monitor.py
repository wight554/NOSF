#!/usr/bin/env python3
"""
NOSF — SG Monitor
Feeds a lane at a fixed speed and continuously prints StallGuard values.
Use this to characterise free-air SG, observe contact drops, and verify
ISS_SG_TARGET / ISS_SG_DERIV_THR / SGT_L1 / SGT_L2 before or after tuning.
"""
import argparse
import serial
import time
import sys
import glob

# ── Serial helpers ────────────────────────────────────────────────────────────

def find_port():
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    if not ports:
        print("No serial port found. Specify with --port.")
        sys.exit(1)
    return ports[0]

def send_wait(ser, cmd, timeout=3.0):
    ser.reset_input_buffer()
    ser.write(f"{cmd}\n".encode())
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode(errors='ignore').strip()
            if line.startswith("OK:") or line.startswith("ER:"):
                return line
    return "timeout"

def get_value(ser, key, timeout=2.0):
    ser.reset_input_buffer()
    ser.write(f"GET:{key}\n".encode())
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode(errors='ignore').strip()
            if line.startswith(f"OK:{key}:"):
                return line.split(':', 2)[2]
    return None

def read_sg(ser, lane, timeout=1.0):
    ser.reset_input_buffer()
    ser.write(f"SG:{lane}\n".encode())
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode(errors='ignore').strip()
            if line.startswith(f"OK:{lane}:"):
                try:
                    return int(line.split(':')[2])
                except (ValueError, IndexError):
                    pass
    return None

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="NOSF SG monitor — feed lane at fixed speed and print StallGuard values",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Speed selection:
  --iss       Read ISS_JOIN_SPS from device and use it as feed speed.
              This is the recommended mode for SGT calibration — it matches
              the exact speed used during ISS approach.
  --speed S   Explicit feed speed in mm/min (use if --iss is unavailable).

Examples:
  # SGT calibration at ISS approach speed (auto-read from device):
  python3 scripts/sg_monitor.py --lane 1 --iss

  # Free-air observation at explicit speed:
  python3 scripts/sg_monitor.py --lane 1 --speed 2120

  # Bowden friction characterisation at slow speed:
  python3 scripts/sg_monitor.py --lane 1 --speed 600

Tip: the bar scales to the highest SG seen since start.  Let the motor settle
for a few seconds before touching the filament so the bar is calibrated to
the free-air baseline.

Note: SG_RESULT is computed by the TMC2209 whenever TSTEP <= TCOOLTHRS —
independent of SGTHRS.  If values are stuck at 0, verify TCOOLTHRS covers
your operating speed (default CONF_TCOOLTHRS=1000 covers the full speed
range at typical SPS values).
""")
    parser.add_argument("--port",     help="Serial port (auto-detected if omitted)")
    parser.add_argument("--lane",     type=int, choices=[1, 2], default=1,
                        help="Lane to monitor (default: 1)")
    parser.add_argument("--interval", type=float, default=0.05,
                        help="Poll interval in seconds (default: 0.05)")

    speed_group = parser.add_mutually_exclusive_group(required=True)
    speed_group.add_argument("--speed", type=float,
                             help="Feed speed in mm/min")
    speed_group.add_argument("--iss",   action="store_true",
                             help="Read ISS_JOIN_SPS from device and derive mm/min")
    args = parser.parse_args()

    port = args.port or find_port()
    print(f"Connecting to {port} ...")
    try:
        ser = serial.Serial(port, 115200, timeout=1)
        time.sleep(2)
    except Exception as e:
        print(f"Failed: {e}")
        sys.exit(1)

    try:
        if args.iss:
            sps_str = get_value(ser, "ISS_JOIN_SPS")
            mps_str = get_value(ser, "MM_PER_STEP")
            if sps_str is None:
                print("Could not read ISS_JOIN_SPS from device. Use --speed instead.")
                sys.exit(1)
            iss_join_sps = int(sps_str)
            mm_per_step  = float(mps_str) if mps_str else 0.001417
            speed = iss_join_sps * mm_per_step * 60.0
            print(f"ISS_JOIN_SPS = {iss_join_sps} SPS  →  {speed:.0f} mm/min")
        else:
            speed = args.speed

        print(f"Lane {args.lane}  |  {speed:.0f} mm/min  |  Ctrl+C to stop\n")
        resp = send_wait(ser, f"T:{args.lane}")
        print(f"  T:{args.lane}        → {resp}")
        resp = send_wait(ser, f"SET:FEED:{speed:.0f}")
        print(f"  SET:FEED:{speed:.0f} → {resp}")
        resp = send_wait(ser, "FD:")
        print(f"  FD:        → {resp}")
        time.sleep(0.5)  # let ramp settle

        BAR = 40
        print(f"\n  {'Time':>8}   {'SG':>5}   {'%free':>6}   bar")
        print(f"  {'─'*8}   {'─'*5}   {'─'*6}   {'─'*BAR}")

        t0 = time.time()
        sg_peak  = None   # highest reading seen (free-air estimate)
        sg_floor = None   # lowest reading seen

        while True:
            v = read_sg(ser, args.lane)
            if v is None:
                time.sleep(args.interval)
                continue

            if sg_peak is None or v > sg_peak:
                sg_peak = v
            if sg_floor is None or v < sg_floor:
                sg_floor = v

            elapsed = time.time() - t0
            pct_free = int(v / max(sg_peak, 1) * 100)
            bar_len  = int(v / max(sg_peak, 1) * BAR)
            bar      = "#" * bar_len + "." * (BAR - bar_len)

            print(f"  {elapsed:8.1f}s   {v:5d}   {pct_free:5d}%   [{bar}]")
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n\nStopping motor...")
        if sg_peak is not None:
            print(f"\nSession summary  (lane {args.lane}, {speed:.0f} mm/min):")
            print(f"  SG peak  (free-air estimate) : {sg_peak}")
            print(f"  SG floor (min observed)      : {sg_floor}")
            if sg_peak > 0:
                drop_pct = int((sg_peak - sg_floor) / sg_peak * 100)
                suggested_sgt = max(1, sg_floor // 2)
                print(f"  Observed drop                : {sg_peak - sg_floor}  ({drop_pct}%)")
                print(f"  Suggested SGT_L{args.lane}           : {suggested_sgt}"
                      f"  (DIAG fires at SG ≤ {suggested_sgt * 2})")
                print(f"  Note: suggestion uses absolute floor (full-stall territory).")
                print(f"  If you observed a stable jam plateau above {sg_floor},")
                print(f"  use that value instead: SGT_L{args.lane} = <plateau> / 2")
    finally:
        send_wait(ser, "ST:")
        ser.close()

if __name__ == "__main__":
    main()
