#!/usr/bin/env python3
"""
nosf_logger.py — async CSV capture of NOSF status + M118 markers.

Usage: nosf_logger.py --port /dev/ttyACM0 --out /var/log/nosf/run.csv
"""

import argparse
import csv
import re
import sys
import time
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
    # Regex to find all KEY:VAL pairs
    m = dict(STATUS_RE.findall(line))
    mk = MARK_RE.search(line)
    if mk:
        m['MK_SEQ'] = mk.group('seq')
        m['MK_TAG'] = mk.group('tag')
    return m

def build_row(m, last_f):
    row = {
        'ts_ms': int(time.time() * 1000), # host-side TS
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
    # Convert BPV:int/100 to float
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
        ser = serial.Serial(args.port, args.baud, timeout=0.05)
    except serial.SerialException as e:
        print(f"Error opening serial port {args.port}: {e}")
        sys.exit(1)

    try:
        f_out = open(args.out, 'w', newline='')
        writer = csv.DictWriter(f_out, fieldnames=CSV_FIELDS)
        writer.writeheader()
    except IOError as e:
        print(f"Error opening output file {args.out}: {e}")
        sys.exit(1)

    last_feature = {'feature': '', 'vfil': '', 'w': '', 'h': ''}
    interval = 1.0 / args.rate_hz
    next_t = time.monotonic()
    
    print(f"[*] Logging to {args.out} at {args.rate_hz} Hz. Press Ctrl+C to stop.")
    
    try:
        while True:
            # 1. Drain incoming lines
            while ser.in_waiting > 0:
                try:
                    line = ser.readline().decode('utf-8', errors='replace').strip()
                except Exception:
                    continue
                if not line: break
                
                m118 = M118_RE.search(line)
                if m118:
                    last_feature = m118.groupdict()
                    continue
                
                if 'LN:' in line:
                    fields = parse_status(line)
                    if 'LN' in fields:
                        row = build_row(fields, last_feature)
                        writer.writerow(row)
                        f_out.flush()

            # 2. Send the next STATUS poll on schedule
            now = time.monotonic()
            if now >= next_t:
                ser.write(b'STATUS\n')
                next_t = now + interval
            
            time.sleep(0.01) # Avoid tight loop
            
    except KeyboardInterrupt:
        print("\n[*] Stopping logger.")
    finally:
        ser.close()
        f_out.close()

if __name__ == '__main__':
    main()
