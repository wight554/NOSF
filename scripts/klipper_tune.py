import argparse
import configparser
import glob
import math
import serial
import sys
import time

def find_port():
    ports = glob.glob('/dev/ttyACM*') + glob.glob('/dev/cu.usbmodem*')
    if not ports:
        return None
    return ports[0]

def send_cmd(s, cmd):
    s.write((cmd + '\n').encode())
    time.sleep(0.05)
    resp = s.read(s.in_waiting).decode().strip()
    return resp

def calc_cs(irms, rsense, vsense):
    reff = rsense + 0.020
    vref = 0.18 if vsense else 0.32
    cs = int((32.0 * irms * reff * math.sqrt(2.0)) / vref - 1.0 + 0.5)
    return max(0, min(31, cs))

def get_current_reg(run_current, hold_current, rsense):
    # Try VSENSE = 0
    vsense = 0
    cs_run = calc_cs(run_current, rsense, vsense)
    if cs_run < 16:
        vsense = 1
        cs_run = calc_cs(run_current, rsense, vsense)
    
    cs_hold = calc_cs(hold_current, rsense, vsense)
    
    # IHOLD_DELAY is typically 8
    ihold_irun = (8 << 16) | (cs_run << 8) | cs_hold
    return ihold_irun, vsense

def encode_chopconf(toff, hstrt, hend, tbl, vsense, mres, intpol):
    reg_toff = toff & 0x0F
    reg_hstrt = hstrt & 0x07
    reg_hend = hend & 0x0F
    reg_tbl = tbl & 0x03
    reg_vsense = vsense & 0x01
    reg_mres = mres & 0x0F
    reg_intpol = 1 if intpol else 0

    val = 0
    val |= (reg_toff << 0)
    val |= (reg_hstrt << 4)
    val |= (reg_hend << 7)
    val |= (reg_tbl << 15)
    val |= (reg_vsense << 17)
    val |= (reg_mres << 24)
    val |= (reg_intpol << 28)
    return val

def decode_chopconf(val):
    toff = val & 0x0F
    hstrt_reg = (val >> 4) & 0x07
    hend_reg = (val >> 7) & 0x0F
    tbl = (val >> 15) & 0x03
    vsense = (val >> 17) & 0x01
    mres = (val >> 24) & 0x0F
    intpol = (val >> 28) & 0x01

    hstrt_actual = hstrt_reg
    hend_actual = hend_reg
    microsteps = 256 >> mres if mres <= 8 else 0

    return {
        "TOFF": toff,
        "HSTRT": hstrt_actual,
        "HEND": hend_actual,
        "TBL": tbl,
        "VSENSE": vsense,
        "MRES": mres,
        "microsteps": microsteps,
        "interpolate": bool(intpol)
    }

def decode_ihold_irun(val, rsense, vsense):
    ihold = val & 0x1F
    irun = (val >> 8) & 0x1F
    
    # Reverse CS to Irms
    reff = rsense + 0.020
    vref = 0.18 if vsense else 0.32
    
    def cs_to_irms(cs):
        return (cs + 1.0) * vref / (32.0 * reff * math.sqrt(2.0))
        
    return cs_to_irms(irun), cs_to_irms(ihold)

def main():
    parser = argparse.ArgumentParser(description="Klipper-style tuner for NightOwl standalone controller.")
    parser.add_argument("--port", type=str, help="Serial port (auto-detected if omitted)")
    parser.add_argument("--lane", type=int, choices=[1, 2], default=1, help="Motor lane (1 or 2, default: 1)")
    
    subparsers = parser.add_subparsers(dest="action", required=True)
    
    # Read command
    subparsers.add_parser("read", help="Read configuration from the lane")
    
    # Apply command
    apply_parser = subparsers.add_parser("apply", help="Apply configuration from an INI file")
    apply_parser.add_argument("config", type=str, help="Path to the .ini config file")

    args = parser.parse_args()

    port = args.port or find_port()
    if not port:
        print("Error: Could not automatically detect serial port.")
        sys.exit(1)

    try:
        s = serial.Serial(port, 115200, timeout=1)
        time.sleep(0.5)
        s.read(s.in_waiting)
    except Exception as e:
        print(f"Failed to open port: {e}")
        sys.exit(1)

    if args.action == "read":
        print(f"Reading from Lane {args.lane}...")
        
        # Read CHOPCONF
        resp = send_cmd(s, f"TR:{args.lane}:108")  # 0x6C = 108
        if not resp.startswith("OK:"):
            print("Failed to read CHOPCONF")
            sys.exit(1)
        val_hex = resp.split(":")[3]
        chopconf_val = int(val_hex, 16)
        chop_dec = decode_chopconf(chopconf_val)
        
        # Prefer firmware-backed current values (works for write-only IHOLD_IRUN).
        irun = None
        ihold = None
        resp_run = send_cmd(s, f"GET:RUN_CURRENT_MA:{args.lane}")
        resp_hold = send_cmd(s, f"GET:HOLD_CURRENT_MA:{args.lane}")
        if resp_run.startswith("OK:") and resp_hold.startswith("OK:"):
            try:
                run_parts = resp_run.split(":")
                hold_parts = resp_hold.split(":")
                run_ma = int(run_parts[3] if len(run_parts) >= 4 else run_parts[2])
                hold_ma = int(hold_parts[3] if len(hold_parts) >= 4 else hold_parts[2])
                irun = run_ma / 1000.0
                ihold = hold_ma / 1000.0
            except (IndexError, ValueError):
                irun = None
                ihold = None

        # Backward-compatible fallback for firmware that only supports non-lane GET.
        if irun is None or ihold is None:
            resp_run = send_cmd(s, "GET:RUN_CURRENT_MA")
            resp_hold = send_cmd(s, "GET:HOLD_CURRENT_MA")
            if resp_run.startswith("OK:") and resp_hold.startswith("OK:"):
                try:
                    run_ma = int(resp_run.split(":")[2])
                    hold_ma = int(resp_hold.split(":")[2])
                    irun = run_ma / 1000.0
                    ihold = hold_ma / 1000.0
                except (IndexError, ValueError):
                    irun = None
                    ihold = None

        # Backward-compatible fallback for older firmware.
        if irun is None or ihold is None:
            resp = send_cmd(s, f"TR:{args.lane}:16")   # 0x10 = 16
            if not resp.startswith("OK:"):
                print("Failed to read current settings (GET and TR fallback both failed)")
                sys.exit(1)
            val_hex = resp.split(":")[3]
            ihold_val = int(val_hex, 16)
            # We need rsense to decode current. Default to 0.110.
            irun, ihold = decode_ihold_irun(ihold_val, 0.110, chop_dec["VSENSE"])
        
        # Read MM_PER_STEP from firmware
        resp = send_cmd(s, "GET:MM_PER_STEP")
        mm_per_step = 0.0
        if resp.startswith("OK:"):
            try:
                mm_per_step = float(resp.split(":")[2])
            except (IndexError, ValueError):
                print(f"Warning: Could not parse MM_PER_STEP from response: {resp}")

        print("\n[tmc2209 extruder]")
        print(f"run_current: {irun:.3f}")
        print(f"hold_current: {ihold:.3f}")
        print(f"interpolate: {chop_dec['interpolate']}")
        print(f"sense_resistor: 0.110 (assumed for read)")
        print(f"driver_TBL: {chop_dec['TBL']}")
        print(f"driver_TOFF: {chop_dec['TOFF']}")
        print(f"driver_HSTRT: {chop_dec['HSTRT']}")
        print(f"driver_HEND: {chop_dec['HEND']}")
        
        print("\n[extruder]")
        print(f"microsteps: {chop_dec['microsteps']}")
        print(f"# Firmare MM_PER_STEP (internal): {mm_per_step:.5f}")

    elif args.action == "apply":
        config = configparser.ConfigParser()
        # To allow files without any section headers at all, we can prepend a dummy section
        with open(args.config, 'r') as f:
            content = f.read()
        
        if not any(line.strip().startswith('[') for line in content.splitlines()):
            content = "[DEFAULT]\n" + content
            
        config.read_string(content)
        
        # Flatten all sections into a single dictionary so we don't care about section names
        # (e.g. [tmc2209 extruder] vs [tmc2209 extruder1] vs [extruder])
        params = {}
        for section in config.sections() + (['DEFAULT'] if config.default_section else []):
            for key, val in config.items(section):
                params[key] = val

        def get_float(key, default_val):
            return float(params.get(key, default_val))
            
        def get_int(key, default_val):
            return int(params.get(key, default_val))
            
        def get_bool(key, default_val):
            val = params.get(key, str(default_val)).lower()
            return val in ('true', '1', 'yes', 'on')

        run_current = get_float('run_current', 0.8)
        hold_current = get_float('hold_current', run_current / 2.0)
        interpolate = get_bool('interpolate', True)
        stealthchop = get_int('stealthchop_threshold', 0)
        rsense = get_float('sense_resistor', 0.110)
        
        tbl = get_int('driver_tbl', 1)
        toff = get_int('driver_toff', 3)
        hstrt = get_int('driver_hstrt', 7)
        hend = get_int('driver_hend', 10)
        
        microsteps = get_int('microsteps', 16)
        rotation_dist = get_float('rotation_distance', 0)
        full_steps = get_int('full_steps_per_rotation', 200)
        
        gear_ratio_str = params.get('gear_ratio', '1:1')
        parts = gear_ratio_str.split(':')
        if len(parts) == 2:
            gear_ratio = float(parts[0]) / float(parts[1])
        else:
            gear_ratio = 1.0

        # Calculate MM_PER_STEP
        if rotation_dist > 0:
            mm_per_step = rotation_dist / (full_steps * microsteps * gear_ratio)
        else:
            mm_per_step = 0.0125

        mres = 0
        ms = 256
        while ms > microsteps and mres < 8:
            mres += 1
            ms >>= 1

        ihold_irun, vsense = get_current_reg(run_current, hold_current, rsense)
        chopconf = encode_chopconf(toff, hstrt, hend, tbl, vsense, mres, interpolate)

        print(f"Applying config to Lane {args.lane}...")
        
        # Write IHOLD_IRUN
        print(f"Setting currents (Run: {run_current}A, Hold: {hold_current}A)")
        send_cmd(s, f"TW:{args.lane}:16:{ihold_irun}")
        
        # Write CHOPCONF
        print(f"Setting CHOPCONF (TOFF={toff}, HSTRT={hstrt}, HEND={hend}, MRES={mres}, INTPOL={interpolate})")
        send_cmd(s, f"TW:{args.lane}:108:{chopconf}")
        
        # Write StealthChop (GCONF en_spreadcycle is bit 2. 0=StealthChop, 1=SpreadCycle)
        # For simplicity, NightOwl hardcodes standard GCONF bits: bit 6 (pdn_disable) and bit 7 (mstep_reg_select)
        gconf = (1 << 6) | (1 << 7)
        if stealthchop == 0:
            gconf |= (1 << 2) # SpreadCycle ON
        print(f"Setting GCONF (SpreadCycle = {stealthchop == 0})")
        send_cmd(s, f"TW:{args.lane}:0:{gconf}")
        
        # Write MM_PER_STEP to firmware
        print(f"Setting firmware MM_PER_STEP = {mm_per_step:.5f}")
        send_cmd(s, f"SET:MM_PER_STEP:{mm_per_step:.5f}")
        
        # Save to firmware EEPROM
        print("Saving settings to firmware flash...")
        send_cmd(s, "SV:")
        
        print("Done!")

    s.close()

if __name__ == '__main__':
    main()
