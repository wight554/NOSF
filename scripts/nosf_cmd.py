#!/usr/bin/env python3
"""
nosf_cmd.py — send NOSF commands and/or dump the live device configuration.

SEND MODE (default):
    python3 scripts/nosf_cmd.py [--port PORT] [--timeout S] CMD:PAYLOAD [CMD2 ...]

    Sends one or more commands in sequence.  Each command waits for OK:/ER:.
    Long-running commands (TC:) wait for the corresponding completion event.

DUMP MODE:
    python3 scripts/nosf_cmd.py [--port PORT] --dump [--raw]

    Reads all GET-able parameters from the device and prints them in
    config.ini format (copy-paste ready).  Use --raw for a terse list.

Exit codes: 0 = success, 1 = error or timeout.
"""
import argparse
import glob
import sys
import time

try:
    import serial
except ImportError:
    print("nosf_cmd: 'pyserial' not installed. Run: pip install pyserial", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Commands that must wait for a completion event rather than just OK:
# ---------------------------------------------------------------------------
COMPLETION_EVENTS = {
    'TC': (['EV:TC:DONE'], ['EV:TC:ERROR']),
}

# ---------------------------------------------------------------------------
# Parameters to dump, grouped by config.ini section.
# Each entry: (serial_cmd, config_key, lane_aware)
# lane_aware=True  → fetched as CMD_L1 and CMD_L2, emitted as "key: L1, L2"
# lane_aware=False → fetched once, emitted as "key: VALUE"
# ---------------------------------------------------------------------------
DUMP_PARAMS = [
    # (serial_cmd,        config.ini key,           lane_aware)
    # --- Motor / TMC ---
    ("RUN_CURRENT_MA",    "run_current_ma",          True),
    ("HOLD_CURRENT_MA",   "hold_current_ma",         True),
    ("MICROSTEPS",        "microsteps",              True),
    ("ROTATION_DIST",     "rotation_distance",       True),
    ("FULL_STEPS",        "full_steps_per_rotation", True),
    ("GEAR_RATIO",        "gear_ratio",              True),
    ("INTERPOLATE",       "interpolate",             True),
    ("STEALTHCHOP",       "stealthchop_threshold",   True),
    # --- Chopper ---
    ("DRIVER_TBL",        "driver_tbl",              True),
    ("DRIVER_TOFF",       "driver_toff",             True),
    ("DRIVER_HSTRT",      "driver_hstrt",            True),
    ("DRIVER_HEND",       "driver_hend",             True),
    # --- StallGuard ---
    ("SGT",               "sgt",                     True),
    ("TCOOLTHRS",         "tcoolthrs",               True),
    ("SG_CURRENT_MA",     "sg_current_ma",           True),
    # --- Speeds ---
    ("FEED_RATE",         "feed_rate",               False),
    ("REV_RATE",          "rev_rate",                False),
    ("AUTO_RATE",         "auto_rate",               False),
    ("SYNC_MAX_RATE",     "sync_max_rate",           False),
    ("SYNC_MIN_RATE",     "sync_min_rate",           False),
    ("PRE_RAMP_RATE",     "pre_ramp_rate",           False),
    ("JOIN_RATE",         "join_rate",               False),
    ("PRESS_RATE",        "press_rate",              False),
    ("TRAILING_RATE",     "trailing_rate",           False),
    # --- Motion ---
    ("STARTUP_MS",        "motion_startup_ms",       False),
    ("STALL_MS",          "stall_recovery_ms",       False),
    ("RAMP_STEP_RATE",    "ramp_step_rate",          False),
    ("FOLLOW_MS",         "follow_timeout_ms",       True),
    # --- Buffer sync ---
    ("BUF_TRAVEL",        "buf_half_travel_mm",      False),
    ("BUF_HYST",          "buf_hyst_ms",             False),
    ("SYNC_UP_RATE",      "sync_ramp_up_rate",       False),
    ("SYNC_DN_RATE",      "sync_ramp_dn_rate",       False),
    ("SYNC_KP_RATE",      "sync_kp_rate",            False),
    ("BUF_SENSOR",        "buf_sensor_type",         False),
    ("BUF_NEUTRAL",       "buf_neutral",             False),
    ("BUF_RANGE",         "buf_range",               False),
    ("BUF_THR",           "buf_thr",                 False),
    ("BUF_ALPHA",         "buf_analog_alpha",        False),
    ("TS_BUF_MS",         "ts_buf_fallback_ms",      False),
    # --- SG tuning ---
    ("SG_TARGET",         "sg_target",               True),
    ("SG_DERIV",          "sg_deriv",                True),
    ("SYNC_SG_INTERP",    "sync_sg_interp",          False),
    ("RELOAD_SG_INTERP",  "reload_sg_interp",        False),
    # --- Reload ---
    ("RELOAD_MODE",       "reload_mode",             False),
    ("RELOAD_Y_MS",       "reload_y_timeout_ms",     False),
    # --- Safety ---
    ("AUTO_PRELOAD",      "auto_preload",            False),
    ("RETRACT_MM",        "autoload_retract_mm",     False),
    # --- Timeouts ---
    ("TC_CUT_MS",         "tc_timeout_cut_ms",       False),
    ("TC_UNLOAD_MS",      "tc_timeout_unload_ms",    False),
    ("TC_TH_MS",          "tc_timeout_th_ms",        False),
    ("TC_LOAD_MS",        "tc_timeout_load_ms",      False),
    ("TC_Y_MS",           "tc_timeout_y_ms",         False),
    # --- Cutter ---
    ("CUTTER",            "enable_cutter",           False),
    ("SERVO_OPEN",        "servo_open_us",           False),
    ("SERVO_CLOSE",       "servo_close_us",          False),
    ("SERVO_SETTLE",      "servo_settle_ms",         False),
    ("CUT_FEED",          "cut_feed_mm",             False),
    ("CUT_LEN",           "cut_length_mm",           False),
    ("CUT_AMT",           "cut_amount",              False),
]

# Section boundaries for pretty-printing (config_key → section header)
SECTION_BREAKS = {
    "run_current_ma":      "# ─── Motor / TMC (per-lane) ────────────────────────────────────────────────",
    "driver_tbl":          "# ─── TMC Chopper (per-lane) ────────────────────────────────────────────────",
    "sgt":                 "# ─── StallGuard / CoolStep (per-lane) ──────────────────────────────────────",
    "feed_rate":           "# ─── Speeds (mm/min) ───────────────────────────────────────────────────────",
    "motion_startup_ms":   "# ─── Motion / Ramp ─────────────────────────────────────────────────────────",
    "buf_half_travel_mm":  "# ─── Buffer Sync ───────────────────────────────────────────────────────────",
    "sg_target":           "# ─── RELOAD StallGuard Tuning ──────────────────────────────────────────────",
    "reload_mode":         "# ─── Reload Mode ───────────────────────────────────────────────────────────",
    "auto_preload":        "# ─── Safety ────────────────────────────────────────────────────────────────",
    "tc_timeout_cut_ms":   "# ─── Toolchange Timeouts ───────────────────────────────────────────────────",
    "enable_cutter":       "# ─── Cutter / Servo ────────────────────────────────────────────────────────",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def find_serial_port():
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    if not ports:
        print("nosf_cmd: no serial port found", file=sys.stderr)
        sys.exit(1)
    return ports[0]


def open_port(port):
    try:
        return serial.Serial(port, 115200, timeout=0.5)
    except Exception as e:
        print(f"nosf_cmd: {e}", file=sys.stderr)
        sys.exit(1)


def send_recv(ser, cmd, timeout=3.0):
    """Send a command, return the first OK:/ER: response line."""
    ser.reset_input_buffer()
    ser.write(f"{cmd}\n".encode())
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('OK:') or line.startswith('ER:') or line == 'OK':
            return line
    return None


# ---------------------------------------------------------------------------
# Send mode
# ---------------------------------------------------------------------------
def run_send(args):
    port = args.port or find_serial_port()
    ser  = open_port(port)

    for raw_cmd in args.cmd:
        verb   = raw_cmd.split(':')[0].upper()
        events = COMPLETION_EVENTS.get(verb)

        ser.write(f"{raw_cmd}\n".encode())
        deadline = time.time() + args.timeout
        got_ok   = False

        while time.time() < deadline:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue
            print(line, flush=True)

            if line.startswith('ER:'):
                ser.close()
                sys.exit(1)

            if not got_ok and (line == 'OK' or line.startswith('OK:')):
                got_ok = True
                if events is None:
                    break          # simple command done
                continue

            if events and got_ok:
                ok_evs, err_evs = events
                if any(line.startswith(ev) for ev in err_evs):
                    ser.close()
                    sys.exit(1)
                if any(line.startswith(ev) for ev in ok_evs):
                    break
        else:
            print("nosf_cmd: timeout", file=sys.stderr)
            ser.close()
            sys.exit(1)

    ser.close()


# ---------------------------------------------------------------------------
# Dump mode
# ---------------------------------------------------------------------------
def run_dump(args):
    port = args.port or find_serial_port()
    ser  = open_port(port)
    time.sleep(0.3)           # let the device settle after open

    results = {}              # config_key → value string (or "L1, L2")
    errors  = []

    for (cmd, key, lane_aware) in DUMP_PARAMS:
        if lane_aware:
            vals = []
            for suffix, label in [("_L1", "L1"), ("_L2", "L2")]:
                resp = send_recv(ser, f"GET:{cmd}{suffix}")
                if resp and resp.startswith("OK:"):
                    # Response format: OK:CMD:VALUE
                    parts = resp.split(":", 2)
                    vals.append(parts[2] if len(parts) >= 3 else "?")
                else:
                    vals.append("?")
                    errors.append(f"GET:{cmd}{suffix} → {resp}")
            # Collapse identical values: "5, 5" → "5"
            if len(set(vals)) == 1:
                results[key] = vals[0]
            else:
                results[key] = ", ".join(vals)
        else:
            resp = send_recv(ser, f"GET:{cmd}")
            if resp and resp.startswith("OK:"):
                parts = resp.split(":", 2)
                results[key] = parts[2] if len(parts) >= 3 else "?"
            else:
                results[key] = "?"
                errors.append(f"GET:{cmd} → {resp}")

    ser.close()

    # ---- Print ----
    if args.raw:
        for (_, key, _) in DUMP_PARAMS:
            if key in results:
                print(f"{key}: {results[key]}")
    else:
        print("# NOSF live config dump")
        print(f"# Generated by nosf_cmd.py --dump  (port: {port})")
        print()
        prev_section = None
        for (_, key, _) in DUMP_PARAMS:
            if key not in results:
                continue
            if key in SECTION_BREAKS:
                print()
                print(SECTION_BREAKS[key])
            print(f"{key}: {results[key]}")

    if errors:
        print("\n# Warnings — some parameters could not be read:", file=sys.stderr)
        for e in errors:
            print(f"#   {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Send NOSF commands or dump live device config.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--port',    help='Serial port (auto-detected if omitted)')
    parser.add_argument('--timeout', type=float, default=130.0,
                        help='Timeout for long-running commands (default: 130 s)')
    parser.add_argument('--dump',    action='store_true',
                        help='Read all parameters from device and print as config.ini')
    parser.add_argument('--raw',     action='store_true',
                        help='With --dump: print terse key: value lines without comments')
    parser.add_argument('cmd', nargs='*', help='NOSF command(s) to send')
    args = parser.parse_args()

    if args.dump:
        run_dump(args)
    elif args.cmd:
        run_send(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
