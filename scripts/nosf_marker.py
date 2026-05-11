#!/usr/bin/env python3
"""Append NOSF live-tuning marker tags to a local marker file.

Used from Klipper gcode_shell_command so marker delivery does not open the
NOSF USB serial port while nosf_live_tuner.py owns it.
"""

import argparse
import os
import sys
import time


def main():
    print(
        "Warning: nosf_marker.py is deprecated; prefer gcode_marker.py --emit sidecar with Klipper API motion tracking.",
        file=sys.stderr,
    )
    ap = argparse.ArgumentParser(description="Append a NOSF marker to a local marker file.")
    ap.add_argument("--file", default="/tmp/nosf-markers.log", help="Marker file path")
    ap.add_argument("tag", nargs=argparse.REMAINDER, help="Marker tag, e.g. NT:Outer_wall:V335")
    args = ap.parse_args()

    tag = " ".join(args.tag).strip()
    if not tag:
        print("nosf_marker: missing tag", file=sys.stderr)
        return 1

    parent = os.path.dirname(os.path.abspath(args.file))
    os.makedirs(parent, exist_ok=True)
    with open(args.file, "a") as fh:
        fh.write(f"{time.time():.3f} {tag}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
