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

POLL MODE:
    python3 scripts/nosf_cmd.py [--port PORT] --poll MS

    Repeatedly sends the status command (?:) at the specified interval.
    Useful for live debugging of the control loop.

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
    'RL': (['EV:RELOAD:LOADED'], ['EV:TC:ERROR']),
}

# ---------------------------------------------------------------------------
# Parameters to dump, grouped by config snapshot section.
# Each entry: (serial_cmd, output_key, lane_aware)
# lane_aware=True  → fetched as CMD_L1 and CMD_L2, emitted as "key: L1, L2"
# lane_aware=False → fetched once, emitted as "key: VALUE"
# ---------------------------------------------------------------------------
DUMP_PARAMS = [
    # (serial_cmd,        config.ini key,           lane_aware)
    # --- Motor / TMC ---
    ("RUN_CURRENT_MA",    "run_current",             True),
    ("HOLD_CURRENT_MA",   "hold_current",            True),
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
    # --- Speeds ---
    ("FEED_RATE",         "feed_rate",               False),
    ("REV_RATE",          "rev_rate",                False),
    ("AUTO_RATE",         "auto_rate",               False),
    ("BUF_STAB_RATE",     "buf_stab_rate",           False),
    ("GLOBAL_MAX_RATE",   "global_max_rate",         False),
    ("SYNC_MAX_RATE",     "sync_max_rate",           False),
    ("SYNC_MIN_RATE",     "sync_min_rate",           False),
    ("JOIN_RATE",         "join_rate",               False),
    ("PRESS_RATE",        "press_rate",              False),
    ("TRAILING_RATE",     "trailing_rate",           False),
    # --- Motion / Ramp ---
    ("STARTUP_MS",        "motion_startup_ms",       False),
    ("RAMP_STEP_RATE",    "ramp_step_rate",          False),
    ("RAMP_TICK_MS",      "ramp_tick_ms",            False),
    ("PRE_RAMP_RATE",     "pre_ramp_rate",           False),
    # --- Buffer sync ---
    ("BUF_HALF_TRAVEL",   "buf_half_travel_mm",      False),
    ("BUF_HYST",          "buf_hyst_ms",             False),
    ("SYNC_UP_RATE",      "sync_ramp_up_rate",       False),
    ("SYNC_DN_RATE",      "sync_ramp_dn_rate",       False),
    ("SYNC_TICK_MS",      "sync_tick_ms",            False),
    ("BASELINE_RATE",     "baseline_rate",           False),
    ("BASELINE_ALPHA",    "baseline_alpha",          False),
    ("BUF_PREDICT_THR_MS", "buf_predict_thr_ms",     False),
    ("SYNC_KP_RATE",      "sync_kp_rate",            False),
    ("SYNC_OVERSHOOT_PCT", "sync_overshoot_pct",     False),
    ("SYNC_RESERVE_PCT",  "sync_reserve_pct",        False),
    ("TRAIL_BIAS_FRAC",   "sync_trailing_bias_frac", False),
    ("MID_CREEP_TIMEOUT_MS", "mid_creep_timeout_ms", False),
    ("MID_CREEP_RATE",    "mid_creep_rate_sps_per_s", False),
    ("MID_CREEP_CAP",     "mid_creep_cap_frac",      False),
    ("VAR_BLEND_FRAC",    "buf_variance_blend_frac", False),
    ("VAR_BLEND_REF_MM",  "buf_variance_blend_ref_mm", False),
    ("SYNC_AUTO_STOP",    "sync_auto_stop_ms",       False),
    ("POST_PRINT_STAB_MS", "post_print_stab_delay_ms", False),
    # --- Phase 1 — Advance Hardening ---
    ("SYNC_ADV_STOP_MS",  "sync_advance_dwell_stop_ms", False),
    ("SYNC_ADV_RAMP_MS",  "sync_advance_ramp_delay_ms", False),
    ("SYNC_OVERSHOOT_MID_EXT", "sync_overshoot_mid_extend", False),
    # --- Phase 2.5 — Integral Centering & Confidence ---
    ("SYNC_INT_GAIN",     "sync_reserve_integral_gain", False),
    ("SYNC_INT_CLAMP",    "sync_reserve_integral_clamp_mm", False),
    ("SYNC_INT_DECAY_MS", "sync_reserve_integral_decay_ms", False),
    ("EST_SIGMA_CAP",     "est_sigma_hard_cap_mm",     False),
    ("EST_LOW_CF_THR",    "est_low_cf_warn_threshold", False),
    ("EST_FALLBACK_THR",  "est_fallback_cf_threshold", False),
    # --- Smarter Sync ---
    ("EST_ALPHA_MIN",     "est_alpha_min",           False),
    ("EST_ALPHA_MAX",     "est_alpha_max",           False),
    ("ZONE_BIAS_BASE",    "zone_bias_base_rate",     False),
    ("ZONE_BIAS_RAMP",    "zone_bias_ramp_rate",     False),
    ("ZONE_BIAS_MAX",     "zone_bias_max_rate",      False),
    ("RELOAD_LEAN",       "reload_lean_factor",      False),
    # --- Physical Model ---
    ("DIST_IN_OUT",       "dist_in_out",             False),
    ("DIST_OUT_Y",        "dist_out_y",              False),
    ("DIST_Y_BUF",        "dist_y_buf",              False),
    ("BUF_BODY_LEN",      "buf_body_len",            False),
    ("BUF_SIZE",          "buf_size_mm",             False),
    # --- Analog Buffer Sensor ---
    ("BUF_SENSOR",        "buf_sensor_type",         False),
    ("BUF_NEUTRAL",       "buf_neutral",             False),
    ("BUF_RANGE",         "buf_range",               False),
    ("BUF_THR",           "buf_thr",                 False),
    ("BUF_ALPHA",         "buf_analog_alpha",        False),
    ("TS_BUF_MS",         "ts_buf_fallback_ms",      False),
    # --- Flow / Reload ---
    ("AUTO_MODE",         "auto_mode",               False),
    ("RELOAD_MODE",       "reload_mode",             False),
    ("RELOAD_Y_MS",       "reload_y_timeout_ms",     False),
    ("RELOAD_JOIN_MS",    "reload_join_delay_ms",    False),
    ("AUTO_PRELOAD",      "auto_preload",            False),
    # --- Safety ---
    ("AUTOLOAD_MAX",      "autoload_max_mm",         False),
    ("LOAD_MAX",          "load_max_mm",             False),
    ("UNLOAD_MAX",        "unload_max_mm",           False),
    ("RETRACT_MM",        "autoload_retract_mm",     False),
    ("RUNOUT_COOLDOWN_MS", "runout_cooldown_ms",     False),
    ("FOLLOW_MS",         "follow_timeout_ms",       True),
    # --- Timeouts ---
    ("TC_CUT_MS",         "tc_timeout_cut_ms",       False),
    ("TC_TH_MS",          "tc_timeout_th_ms",        False),
    ("TC_Y_MS",           "tc_timeout_y_ms",         False),
    # --- Cutter ---
    ("CUTTER",            "enable_cutter",           False),
    ("SERVO_OPEN",        "servo_open_us",           False),
    ("SERVO_CLOSE",       "servo_close_us",          False),
    ("SERVO_BLOCK",       "servo_block_us",          False),
    ("SERVO_SETTLE",      "servo_settle_ms",         False),
    ("CUT_FEED",          "cut_feed_mm",             False),
    ("CUT_LEN",           "cut_length_mm",           False),
    ("CUT_AMT",           "cut_amount",              False),
]

# Section boundaries for pretty-printing (config_key → section header)
SECTION_BREAKS = {
    "run_current":         "# ─── Motor / TMC (per-lane) ────────────────────────────────────────────────",
    "driver_tbl":          "# ─── TMC Chopper (per-lane) ────────────────────────────────────────────────",
    "feed_rate":           "# ─── Speeds (mm/min) ───────────────────────────────────────────────────────",
    "motion_startup_ms":   "# ─── Motion / Ramp ─────────────────────────────────────────────────────────",
    "buf_half_travel_mm":  "# ─── Buffer Sync ───────────────────────────────────────────────────────────",
    "sync_advance_dwell_stop_ms": "# ─── Phase 1 — Advance Hardening ───────────────────────────────────────────",
    "sync_reserve_integral_gain": "# ─── Phase 2.5 — Integral Centering & Confidence ───────────────────────────",
    "est_alpha_min":       "# ─── Smarter Sync (Estimator) ─────────────────────────────────────────────",
    "dist_in_out":         "# ─── Physical Model (mm) ─────────────────────────────────────────────────",
    "buf_sensor_type":     "# ─── Analog Buffer Sensor ─────────────────────────────────────────────────",
    "auto_mode":           "# ─── Flow / Reload ────────────────────────────────────────────────────────",
    "autoload_max_mm":     "# ─── Safety ────────────────────────────────────────────────────────────────",
    "tc_timeout_cut_ms":   "# ─── Toolchange Timeouts ───────────────────────────────────────────────────",
    "enable_cutter":       "# ─── Cutter / Servo ────────────────────────────────────────────────────────",
}

BOOL_KEYS = {
    "interpolate",
    "auto_mode",
    "auto_preload",
    "enable_cutter",
    "sync_overshoot_mid_extend",
}


def format_dump_value(key, value):
    if value == "?":
        return value

    if key in {"run_current", "hold_current"}:
        try:
            return f"{int(value) / 1000.0:.3f}"
        except ValueError:
            return value

    if key in BOOL_KEYS:
        return "True" if value not in ("0", "False", "false") else "False"

    return value


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
                    raw_val = parts[2] if len(parts) >= 3 else "?"
                    vals.append(format_dump_value(key, raw_val))
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
                raw_val = parts[2] if len(parts) >= 3 else "?"
                results[key] = format_dump_value(key, raw_val)
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
# Poll mode
# ---------------------------------------------------------------------------
def run_poll(args):
    port = args.port or find_serial_port()
    ser  = open_port(port)
    interval = args.poll / 1000.0

    print(f"# Polling status every {args.poll} ms on {port}. Press Ctrl+C to stop.", flush=True)
    try:
        while True:
            start_time = time.time()
            ser.write(b"?:\n")

            # Read until OK: response or ER:
            while True:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line:
                    break
                if line:
                    print(line, flush=True)
                if line.startswith('OK:') or line == 'OK' or line.startswith('ER:'):
                    break

            elapsed = time.time() - start_time
            sleep_time = max(0, interval - elapsed)
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\n# Stopped by user.")
    finally:
        ser.close()


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
    parser.add_argument('--poll',    type=int, metavar='MS',
                        help='Repeatedly poll status (?:) at specified interval in ms')
    parser.add_argument('cmd', nargs='*', help='NOSF command(s) to send')
    args = parser.parse_args()

    if args.dump:
        run_dump(args)
    elif args.poll:
        run_poll(args)
    elif args.cmd:
        run_send(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
