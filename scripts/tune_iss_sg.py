#!/usr/bin/env python3
"""
ISS StallGuard Tuning — NOSF
Calibrates ISS_SG_TARGET and ISS_SG_DERIV for Endless Spool contact detection.

StallGuard (SG) in NOSF is used ONLY in ISS (Endless Spool) mode:
  - TC_ISS_APPROACH: SG moving-average derivative detects tip-to-tail contact at
                     high approach speed, before the buffer arm moves.
  - TC_ISS_FOLLOW:   SG interpolation maintains gentle contact pressure during
                     the ~1 m bowden journey to the extruder (2-endstop mode only;
                     analog buffer uses arm position directly).

SG is NOT used during normal buffer sync. Buffer arm position alone drives sync.
"""
import argparse
import serial
import time
import sys
import glob
import statistics

# ── Serial helpers ────────────────────────────────────────────────────────────

def find_port():
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    if not ports:
        print("No serial port found. Specify with --port.")
        sys.exit(1)
    return ports[0]

def send(ser, cmd):
    ser.write(f"{cmd}\n".encode())
    time.sleep(0.05)

def send_wait(ser, cmd, timeout=3.0):
    ser.reset_input_buffer()
    ser.write(f"{cmd}\n".encode())
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode(errors='ignore').strip()
            if line.startswith("OK:") or line.startswith("ER:"):
                return line
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

def get_value(ser, key):
    ser.reset_input_buffer()
    ser.write(f"GET:{key}\n".encode())
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode(errors='ignore').strip()
            if line.startswith(f"OK:{key}:"):
                try:
                    # Handle both int and float response
                    val_str = line.split(':', 2)[2]
                    return float(val_str)
                except (ValueError, IndexError):
                    pass
    return None

def sample_sg(ser, lane, duration=3.0, interval=0.02):
    samples = []
    deadline = time.time() + duration
    while time.time() < deadline:
        v = read_sg(ser, lane)
        if v is not None:
            samples.append(v)
        time.sleep(interval)
    return samples

# ── Tuning phases ─────────────────────────────────────────────────────────────

def phase_free_air(ser, lane, join_rate):
    print(f"\n{'='*60}")
    print("Phase 1 — Free-Air Baseline")
    print(f"{'='*60}")
    print(f"Motor will run at ISS_JOIN_RATE = {join_rate:.0f} mm/min.")
    print("Make sure the filament tip for this lane is free:")
    print("  hanging in air, inside PTFE, or parked before any contact point.")
    input("Press Enter to start measurement...")

    send_wait(ser, f"T:{lane}")
    send_wait(ser, f"SET:FEED_RATE:{join_rate:.0f}")
    send_wait(ser, "FD:")
    time.sleep(0.8)  # let ramp settle

    print("Sampling SG for 3 s ...", end=' ', flush=True)
    samples = sample_sg(ser, lane, duration=3.0)
    send_wait(ser, "ST:")
    time.sleep(0.3)

    if len(samples) < 5:
        print(f"\nERROR: Only {len(samples)} SG readings received.")
        print("Check that SGT_L1 / SGT_L2 is non-zero (SGTHRS > 0 in config.ini)")
        print("and that TCOOLTHRS is high enough to enable SG at this speed.")
        return None

    mean_sg = statistics.mean(samples)
    stdev_sg = statistics.stdev(samples) if len(samples) > 1 else 0.0
    print(f"done  (n={len(samples)})")
    print(f"\n  Free-air SG:  mean = {mean_sg:.1f},  σ = {stdev_sg:.1f}")

    if stdev_sg > mean_sg * 0.3:
        print("  WARNING: high variability (σ > 30% of mean).")
        print("           Noisy SG can cause false approach triggers.")
        print("           Consider increasing iss_sg_ma_len in config.ini.")

    return mean_sg, stdev_sg

def phase_contact(ser, lane, join_rate, free_air_sg):
    print(f"\n{'='*60}")
    print("Phase 2 — Contact Calibration (optional)")
    print(f"{'='*60}")
    print("Grip the moving filament firmly between thumb and index finger")
    print("~10-20 cm from the NOSF exit. Squeeze as hard as you can sustain")
    print("for 3-5 s — firm enough that the motor strains, but release before")
    print("it fully stops. This calibrates SGT for filament-to-filament jams,")
    print("not a rigid-wall crash (which gives too low a floor).")
    print("Watch the SG plateau, then press Ctrl+C.")
    input("Press Enter to start motor, then apply pressure...")

    send_wait(ser, f"T:{lane}")
    send_wait(ser, f"SET:FEED_RATE:{join_rate:.0f}")
    send_wait(ser, "FD:")
    time.sleep(0.8)

    floor_sg = free_air_sg
    try:
        while True:
            v = read_sg(ser, lane)
            if v is not None:
                if v < floor_sg:
                    floor_sg = v
                pct = int(v / max(free_air_sg, 1) * 40)
                bar = "#" * pct + "." * (40 - pct)
                print(f"\r  SG: {v:4d}   floor: {int(floor_sg):4d}   [{bar}]  ", end='', flush=True)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass

    send_wait(ser, "ST:")
    time.sleep(0.3)
    print(f"\n\n  Contact floor:  {int(floor_sg)}")
    return int(floor_sg)

# ── Recommendation logic ──────────────────────────────────────────────────────

def compute_recommendations(free_air_sg, contact_floor=None):
    # ISS_SG_TARGET: midpoint between free-air and zero.
    # At the target the motor runs at half ISS_PRESS_RATE — gentle push.
    # Below it speed scales toward 0 (hard contact → stop).
    target = max(1, round(free_air_sg * 0.5))

    # ISS_SG_DERIV: per-tick MA derivative that signals approach contact.
    # Approach runs at ISS_JOIN_RATE; contact is abrupt (SG drops in 1–2 ticks).
    # Threshold = ~40 % of total observable drop per tick.
    if contact_floor is not None:
        total_drop = max(1.0, free_air_sg - contact_floor)
        deriv_thr = max(1, round(total_drop * 0.40))
    else:
        # Without contact data: estimate using 25% floor assumption.
        deriv_thr = max(1, round(free_air_sg * 0.30))

    # SGT (SGTHRS): hard-contact DIAG fallback in ISS approach.
    # TMC fires DIAG when SG_RESULT ≤ 2 × SGTHRS.
    if contact_floor is not None:
        sgt = max(1, round(contact_floor / 2))
    else:
        # Without contact data: rough estimate — assume 70 % drop at hard crash.
        sgt = max(1, round(free_air_sg * 0.15))

    return {'ISS_SG_TARGET': target, 'ISS_SG_DERIV': deriv_thr, 'SGT': sgt}

def print_recommendations(recs, free_air_sg, contact_floor, lane):
    print(f"\n{'='*60}")
    print(f"Recommendations  (lane {lane})")
    print(f"{'='*60}")
    print(f"  Free-air SG baseline : {free_air_sg:.1f}")
    if contact_floor is not None:
        print(f"  Contact SG floor     : {contact_floor}")
    print()

    tgt = recs['ISS_SG_TARGET']
    dth = recs['ISS_SG_DERIV']
    sgt = recs['SGT']

    print(f"  ISS_SG_TARGET    = {tgt}")
    print(f"    Follow sync gentle-pressure setpoint.")
    print(f"    Motor speed scales linearly from ISS_PRESS_RATE (SG ≥ {tgt})")
    print(f"    down to 0 (SG = 0). Adjust if pressure feels too light or too heavy.")
    print()
    print(f"  ISS_SG_DERIV     = {dth}")
    print(f"    Approach contact sensitivity: SG drop > {dth}/tick triggers handoff")
    print(f"    from fast approach to follow sync.")
    print(f"    Lower → more sensitive (catches softer contacts).")
    print(f"    Higher → less sensitive (ignores brief variation).")
    print()
    print(f"  SGT_L{lane}          = {sgt}  (per-lane SGTHRS)")
    print(f"    Hard-contact DIAG fallback for lane {lane}.")
    print(f"    TMC fires DIAG when SG_RESULT ≤ {2*sgt}  (= 2 × {sgt}).")
    print()
    print("NOTE: ISS_SG_TARGET and ISS_SG_DERIV are global (both lanes).")
    print(f"  SGT_L{lane} is per-lane — run this script for each lane separately.")
    print()
    print("Apply commands:")
    for k, v in recs.items():
        key = f"SGT_L{lane}" if k == 'SGT' else k
        print(f"  SET:{key}:{v}")
    print("  SV:")
    print()
    print("Fine-tuning:")
    print("  ISS_SG_TARGET too high → motor backs off too early (weak pressure)")
    print("  ISS_SG_TARGET too low  → motor pushes too hard    (jam risk)")
    print("  ISS_SG_DERIV too high  → approach misses contacts (rare false-no)")
    print("  ISS_SG_DERIV too low   → approach triggers on noise (early handoff)")

def apply_settings(ser, recs, lane):
    print("\nApplying...")
    for k, v in recs.items():
        key = f"SGT_L{lane}" if k == 'SGT' else k
        resp = send_wait(ser, f"SET:{key}:{v}") or "timeout"
        print(f"  SET:{key}:{v}  →  {resp}")
    resp = send_wait(ser, "SV:") or "timeout"
    print(f"  SV:  →  {resp}")

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ISS StallGuard tuning — calibrates ISS_SG_TARGET and ISS_SG_DERIV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Workflow:
  Step 1 — free-air baseline (always required):
    python3 scripts/tune_iss_sg.py --lane 1

  Step 2 — contact calibration (improves ISS_SG_DERIV accuracy):
    python3 scripts/tune_iss_sg.py --lane 1 --contact

  Step 3 — apply and save:
    python3 scripts/tune_iss_sg.py --lane 1 --contact --apply

SG is only active in ISS mode.
""")
    parser.add_argument("--port", help="Serial port (auto-detected if omitted)")
    parser.add_argument("--lane", type=int, choices=[1, 2], default=1,
                        help="Lane to tune (default: 1)")
    parser.add_argument("--contact", action="store_true",
                        help="Run Phase 2: contact calibration (requires manual filament press)")
    parser.add_argument("--apply", action="store_true",
                        help="Automatically apply and save recommended settings after tuning")
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
        join_rate = get_value(ser, "ISS_JOIN_RATE")
        if join_rate is None:
            # Fallback for old firmware key
            join_rate = get_value(ser, "ISS_JOIN")
            
        if join_rate is None:
            print("Could not read ISS_JOIN_RATE from device. Is NOSF firmware running?")
            sys.exit(1)

        result = phase_free_air(ser, args.lane, join_rate)
        if result is None:
            sys.exit(1)
        free_air_sg, _ = result

        contact_floor = None
        if args.contact:
            contact_floor = phase_contact(ser, args.lane, join_rate, free_air_sg)

        recs = compute_recommendations(free_air_sg, contact_floor)
        print_recommendations(recs, free_air_sg, contact_floor, args.lane)

        if args.apply:
            apply_settings(ser, recs, args.lane)
        else:
            print("Re-run with --apply to apply and save automatically.")

    finally:
        send_wait(ser, "ST:")
        ser.close()

if __name__ == "__main__":
    main()
