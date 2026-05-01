import argparse
import serial
import time
import sys
import glob

def find_port():
    ports = glob.glob('/dev/ttyACM*') + glob.glob('/dev/cu.usbmodem*')
    if not ports:
        return None
    return ports[0]

def send_cmd(s, cmd):
    s.write((cmd + '\n').encode())
    time.sleep(0.1)
    resp = s.read(s.in_waiting).decode().strip()
    return resp

def decode_chopconf(val):
    """Decodes a 32-bit CHOPCONF register value into actual spreadsheet values."""
    toff = val & 0x0F
    hstrt_reg = (val >> 4) & 0x07
    hend_reg = (val >> 7) & 0x0F
    tbl = (val >> 15) & 0x03
    vsense = (val >> 17) & 0x01
    mres = (val >> 24) & 0x0F

    hstrt_actual = hstrt_reg + 1
    hend_actual = hend_reg - 3
    microsteps = 256 >> mres if mres <= 8 else 0

    return {
        "TOFF": toff,
        "HSTRT": hstrt_actual,
        "HEND": hend_actual,
        "TBL": tbl,
        "VSENSE": vsense,
        "MRES": mres,
        "microsteps": microsteps
    }

def encode_chopconf(toff, hstrt, hend, tbl, vsense, mres):
    """Encodes actual spreadsheet values into a 32-bit CHOPCONF register value."""
    reg_toff = toff & 0x0F
    reg_hstrt = (hstrt - 1) & 0x07
    reg_hend = (hend + 3) & 0x0F
    reg_tbl = tbl & 0x03
    reg_vsense = vsense & 0x01
    reg_mres = mres & 0x0F

    val = 0
    val |= (reg_toff << 0)
    val |= (reg_hstrt << 4)
    val |= (reg_hend << 7)
    val |= (reg_tbl << 15)
    val |= (reg_vsense << 17)
    val |= (reg_mres << 24)
    return val

def format_chopconf(decoded):
    return (f"TOFF={decoded['TOFF']}, HSTRT={decoded['HSTRT']} (1 to 8), "
            f"HEND={decoded['HEND']} (-3 to 12), TBL={decoded['TBL']}, "
            f"VSENSE={decoded['VSENSE']}, MRES={decoded['MRES']} ({decoded['microsteps']} microsteps)")

def main():
    parser = argparse.ArgumentParser(description="Read and write actual TMC2209 CHOPCONF values.")
    parser.add_argument("--port", type=str, help="Serial port (auto-detected if omitted)")
    parser.add_argument("--lane", type=int, choices=[1, 2], default=1, help="Motor lane (1 or 2, default: 1)")
    
    subparsers = parser.add_subparsers(dest="action", required=True)
    
    # Read command
    read_parser = subparsers.add_parser("read", help="Read CHOPCONF from the lane")
    
    # Write command
    write_parser = subparsers.add_parser("write", help="Write CHOPCONF to the lane")
    write_parser.add_argument("--toff", type=int, required=True, help="TOFF (1-15)")
    write_parser.add_argument("--hstrt", type=int, required=True, help="HSTRT (1-8)")
    write_parser.add_argument("--hend", type=int, required=True, help="HEND (-3 to 12)")
    write_parser.add_argument("--tbl", type=int, required=True, choices=[0, 1, 2, 3], help="TBL (0=16, 1=24, 2=36, 3=54)")
    write_parser.add_argument("--vsense", type=int, required=True, choices=[0, 1], help="VSENSE (0 or 1)")
    write_parser.add_argument("--mres", type=int, required=True, choices=range(0, 9), help="MRES (0=256 to 8=1)")

    args = parser.parse_args()

    port = args.port or find_port()
    if not port:
        print("Error: Could not automatically detect serial port.")
        sys.exit(1)

    try:
        s = serial.Serial(port, 115200, timeout=1)
        time.sleep(0.5)
    except Exception as e:
        print(f"Failed to open port: {e}")
        sys.exit(1)

    # Clean buffer
    s.read(s.in_waiting)

    if args.action == "read":
        resp = send_cmd(s, f"TR:{args.lane}:108")
        if resp.startswith("OK:"):
            parts = resp.split(":")
            if len(parts) >= 4:
                val_hex = parts[3]
                val = int(val_hex, 16)
                decoded = decode_chopconf(val)
                print(f"Lane {args.lane} CHOPCONF: 0x{val:08X}")
                print(f"Values: {format_chopconf(decoded)}")
            else:
                print(f"Unexpected response format: {resp}")
        else:
            print(f"Failed to read: {resp}")

    elif args.action == "write":
        # Encode the actual values
        val = encode_chopconf(args.toff, args.hstrt, args.hend, args.tbl, args.vsense, args.mres)
        
        print(f"Writing CHOPCONF to Lane {args.lane}...")
        print(f"New values: TOFF={args.toff}, HSTRT={args.hstrt}, HEND={args.hend}, TBL={args.tbl}, VSENSE={args.vsense}, MRES={args.mres}")
        print(f"Encoded as: {val} (0x{val:08X})")
        
        resp = send_cmd(s, f"TW:{args.lane}:108:{val}")
        if resp == "OK":
            print("Write successful. Reading back to verify...")
            verify_resp = send_cmd(s, f"TR:{args.lane}:108")
            if verify_resp.startswith("OK:"):
                val_hex = verify_resp.split(":")[3]
                read_val = int(val_hex, 16)
                decoded = decode_chopconf(read_val)
                print(f"Verified values: {format_chopconf(decoded)}")
            else:
                print(f"Verification read failed: {verify_resp}")
        else:
            print(f"Failed to write: {resp}")

    s.close()

if __name__ == '__main__':
    main()
