#!/usr/bin/env python3
"""
nosf_logger.py — captures NOSF status + M118 markers.
Usage: nosf_logger.py --port /dev/ttyACM0 --out ~/nosf-logs/run.csv --klipper-log ~/printer_data/logs/klippy.log
"""

import argparse
import csv
import re
import sys
import time
import os
import serial

STATUS_RE = re.compile(r'(?P<key>[A-Z1-2]+):(?P<val>-?\d+(?:\.\d+)?|[A-Z_]+|[^,]*)')
MARK_RE   = re.compile(r'MK:(?P<seq>\d+):(?P<tag>[^,]*)')
M118_RE   = re.compile(r'NOSF_TUNE:(?P<feature>[^:]+):V(?P<vfil>[^:]+):W(?P<w>[^:]+):H(?P<h>[^:]+)')

CSV_FIELDS = [
    'ts_ms', 'lane', 'zone', 'bp_mm', 'sigma_mm', 'est_sps', 'current_sps',
    'reserve_err_mm', 'rt_mm', 'ri_mm', 'ec', 'cf', 'bpd_mm', 'bpn',
    'apx', 'adv_dwell_ms', 'tb', 'mc', 'vb', 'bpv_mm',
    'marker_seq', 'marker_tag', 'feature', 'v_fil', 'width', 'height',
]

def parse_status(line):
    m = dict(STATUS_RE.findall(line))
    mk = MARK_RE.search(line)
    if mk:
        m['MK_SEQ'] = mk.group('seq')
        m['MK_TAG'] = mk.group('tag')
    return m

def build_row(m, last_f):
    row = {
        'ts_ms': int(time.time() * 1000),
        'lane': m.get('LN', ''),
        'zone': m.get('BUF', ''),
        'bp_mm': m.get('BP', ''),
        'sigma_mm': m.get('ES', ''),
        'est_sps': m.get('EST', ''),
        'current_sps': m.get('MM', ''),
        'reserve_err_mm': m.get('RE', ''),
        'rt_mm': m.get('RT', ''),
        'ri_mm': m.get('RI', ''),
        'ec': m.get('EC', ''),
        'cf': m.get('CF', ''),
        'bpd_mm': m.get('BPD', ''),
        'bpn': m.get('BPN', ''),
        'apx': m.get('APX', ''),
        'adv_dwell_ms': m.get('AD', ''),
        'tb': m.get('TB', ''),
        'mc': m.get('MC', ''),
        'vb': m.get('VB', ''),
        'bpv_mm': m.get('BPV', ''),
        'marker_seq': m.get('MK_SEQ', ''),
        'marker_tag': m.get('MK_TAG', ''),
        'feature': last_f.get('feature', ''),
        'v_fil': last_f.get('vfil', ''),
        'width': last_f.get('w', ''),
        'height': last_f.get('h', ''),
    }
    if row['bpv_mm']:
        try:
            row['bpv_mm'] = float(row['bpv_mm']) / 100.0
        except ValueError:
            pass
    return row

def main():
    print("Warning: nosf_logger.py is deprecated. Use nosf_live_tuner.py --csv-out instead.", file=sys.stderr)
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', required=True)
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--out',  required=True)
    ap.add_argument('--rate-hz', type=float, default=10.0)
    ap.add_argument('--klipper-log', help="Path to klippy.log to tail for markers")
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.05)
    except serial.SerialException as e:
        print(f"Error: Could not open {args.port}. ({e})")
        sys.exit(1)

    try:
        f_out = open(args.out, 'w', newline='')
        writer = csv.DictWriter(f_out, fieldnames=CSV_FIELDS)
        writer.writeheader()
        f_out.flush()
    except IOError as e:
        print(f"Error opening output file: {e}")
        sys.exit(1)

    # Optional Klipper log tailing
    f_log = None
    if args.klipper_log:
        try:
            f_log = open(args.klipper_log, 'r')
            f_log.seek(0, os.SEEK_END) # Go to end
            print(f"[*] Tailing Klipper log: {args.klipper_log}")
        except Exception as e:
            print(f"Warning: Could not open klipper log: {e}")

    last_feature = {'feature': '', 'vfil': '', 'w': '', 'h': ''}
    interval = 1.0 / args.rate_hz
    next_poll = time.monotonic()
    rows_written = 0
    
    print(f"[*] Logging to {args.out} at {args.rate_hz}Hz. Press Ctrl+C to stop.")

    try:
        while True:
            # 1. Read NOSF serial
            while ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='replace').strip()
                if not line: break
                
                # Markers might still come via serial if echoes are on
                m118 = M118_RE.search(line)
                if m118:
                    last_feature = m118.groupdict()
                
                if 'LN:' in line:
                    fields = parse_status(line)
                    if 'LN' in fields:
                        writer.writerow(build_row(fields, last_feature))
                        rows_written += 1
                        if rows_written % 10 == 0:
                            f_out.flush()
                            sys.stdout.write('.')
                            sys.stdout.flush()

            # 2. Read Klipper log (if enabled)
            if f_log:
                log_lines = f_log.readlines()
                for l in log_lines:
                    m118 = M118_RE.search(l)
                    if m118:
                        last_feature = m118.groupdict()
                        sys.stdout.write(f"\n[Marker] {last_feature['feature']} V:{last_feature['vfil']}\n")
                        sys.stdout.flush()

            # 3. Poll NOSF
            now = time.monotonic()
            if now >= next_poll:
                ser.write(b'?:\n')
                next_poll = now + interval
            
            time.sleep(0.01)
            
    except KeyboardInterrupt:
        print(f"\n[*] Stopped. Wrote {rows_written} rows.")
    finally:
        ser.close()
        if f_log: f_log.close()
        f_out.close()

if __name__ == '__main__':
    main()
