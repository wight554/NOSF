#!/usr/bin/env python3

import argparse
import serial
import sys
import time

from serial_utils import find_port


def main():
    parser = argparse.ArgumentParser(description="Quick serial command tester for NOSF firmware.")
    parser.add_argument("commands", nargs="*", default=["?:"], help="Commands to send (default: '?:')")
    parser.add_argument("--port", type=str, help="Serial port (auto-detected if omitted)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--delay", type=float, default=0.3, help="Per-command wait in seconds (default: 0.3)")
    args = parser.parse_args()

    port = args.port or find_port()
    if not port:
        print("Error: Could not automatically detect serial port.")
        sys.exit(1)

    try:
        s = serial.Serial(port, args.baud, timeout=2)
    except Exception as e:
        print(f"Failed to open port {port}: {e}")
        sys.exit(1)

    time.sleep(0.5)
    s.read(s.in_waiting)

    for cmd in args.commands:
        s.write((cmd + "\n").encode())
        time.sleep(args.delay)
        try:
            resp = s.read(s.in_waiting)
            print(f"{cmd} -> {resp.decode(errors='replace').strip()}")
        except OSError:
            print(f"{cmd} -> (port closed - reboot triggered)")
            break

    s.close()


if __name__ == "__main__":
    main()
