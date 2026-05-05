#!/usr/bin/env python3
"""
nosf_cmd.py — send one NOSF command and wait for completion.

Usage:
    python3 scripts/nosf_cmd.py [--port PORT] [--timeout S] CMD:PAYLOAD

For simple commands (SET:, GET:, T:, SM:, TS:, SG:, FD:, ST:, ...):
    waits for the first OK:/ER: line, then exits.

For long-running commands (TC:, FL:, UL:, UM:):
    waits for the completion event (EV:TC:DONE, EV:LOADED, EV:UNLOADED, etc.)
    or the corresponding timeout/error event, then exits.

All received lines are printed to stdout so Klipper's gcode_shell_command
verbose output shows them in the Mainsail / Fluidd console.

Exit codes: 0 = success, 1 = error or timeout.
"""
import argparse
import glob
import serial
import sys
import time

# Commands that must wait for a completion event rather than just OK:
COMPLETION_EVENTS = {
    'TC':  (['EV:TC:DONE'],    ['EV:TC:ERROR']),
}

def find_serial_port():
    ports = glob.glob('/dev/tty.usbmodem*') + glob.glob('/dev/ttyACM*')
    if not ports:
        print("nosf_cmd: no serial port found", file=sys.stderr)
        sys.exit(1)
    return ports[0]

def main():
    parser = argparse.ArgumentParser(
        description='Send a NOSF command and wait for completion.',
        add_help=False,
    )
    parser.add_argument('--port',    help='Serial port (auto-detected if omitted)')
    parser.add_argument('--timeout', type=float, default=130.0,
                        help='Seconds before giving up (default: 130)')
    parser.add_argument('--help', '-h', action='store_true')
    parser.add_argument('cmd', nargs='?', help='NOSF command, e.g. TC:2 or SET:FEED:2100 (speeds in mm/min)')
    args = parser.parse_args()

    if args.help or not args.cmd:
        parser.print_help()
        sys.exit(0 if args.help else 1)

    verb   = args.cmd.split(':')[0].upper()
    events = COMPLETION_EVENTS.get(verb)
    port   = args.port or find_serial_port()

    try:
        ser = serial.Serial(port, 115200, timeout=0.1)
    except Exception as e:
        print(f"nosf_cmd: {e}", file=sys.stderr)
        sys.exit(1)

    ser.write(f"{args.cmd}\n".encode())
    deadline    = time.time() + args.timeout
    got_ok      = False

    try:
        while time.time() < deadline:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue
            print(line, flush=True)

            if line.startswith('ER:'):
                sys.exit(1)

            if not got_ok and (line == 'OK' or line.startswith('OK:')):
                got_ok = True
                if events is None:
                    sys.exit(0)     # simple command — done on first OK:
                continue

            if events and got_ok:
                ok_evs, err_evs = events
                if any(line.startswith(ev) for ev in err_evs):
                    sys.exit(1)
                if any(line.startswith(ev) for ev in ok_evs):
                    sys.exit(0)
    finally:
        ser.close()

    print("nosf_cmd: timeout", file=sys.stderr)
    sys.exit(1)

if __name__ == '__main__':
    main()
