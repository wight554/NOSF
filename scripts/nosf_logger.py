#!/usr/bin/env python3
"""
nosf_logger.py — async CSV capture of NOSF status + M118 markers.
Usage: nosf_logger.py --port /dev/ttyACM0 --out ~/nosf-logs/run.csv
"""

import argparse
import csv
import re
import sys
import time
import serial

# Updated regex to be more inclusive of status fields
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
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', required=True)
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--out',  required=True)
    ap.add_argument('--rate-hz', type=float, default=10.0)
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as e:
        print(f"Error: Could not open {args.port}. Is Klipper running? ({e})")
        sys.exit(1)

    try:
        f_out = open(args.out, 'w', newline='')
        writer = csv.DictWriter(f_out, fieldnames=CSV_FIELDS)
        writer.writeheader()
        f_out.flush()
    except IOError as e:
        print(f"Error opening output file: {e}")
        sys.exit(1)

    last_feature = {'feature': '', 'vfil': '', 'w': '', 'h': ''}
    interval = 1.0 / args.rate_hz
    next_poll = time.monotonic()
    rows_written = 0
    
    print(f"[*] Logging to {args.out}. Press Ctrl+C to stop.")

    try:
        while True:
            # 1. Non-blocking read
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='replace').strip()
                if line:
                    m118 = M118_RE.search(line)
                    if m118:
                        last_feature = m118.groupdict()
                    elif 'LN:' in line:
                        fields = parse_status(line)
                        if 'LN' in fields:
                            writer.writerow(build_row(fields, last_feature))
                            rows_written += 1
                            if rows_written % 10 == 0:
                                f_out.flush()
                                sys.stdout.write('.')
                                sys.stdout.flush()

            # 2. Poll on schedule
            now = time.monotonic()
            if now >= next_poll:
                ser.write(b'?:\n')
                next_poll = now + interval
            
            time.sleep(0.005) # Prevent high CPU
            
    except KeyboardInterrupt:
        print(f"\n[*] Stopped. Wrote {rows_written} rows.")
    finally:
        ser.close()
        f_out.close()

if __name__ == '__main__':
    main()
