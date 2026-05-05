#!/usr/bin/env python3
"""
Generate firmware/include/tune.h from config.ini.

Usage:
  python3 scripts/gen_motor_config.py [config_path [output_path]]

Defaults:
  config_path  config.ini  (repo root)
  output_path  firmware/include/tune.h
"""

import configparser
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

DEFAULT_CONFIG = os.path.join(REPO_ROOT, "config.ini")
DEFAULT_OUTPUT = os.path.join(REPO_ROOT, "firmware", "include", "tune.h")

MANDATORY = ("microsteps", "rotation_distance", "run_current")

KLIPPER_DEFAULTS = {
    "full_steps_per_rotation": "200",
    "gear_ratio": "1:1",
    "hold_current": "",
    "sense_resistor": "0.110",
    "interpolate": "True",
    "driver_tbl": "2",
    "driver_toff": "3",
    "driver_hstrt": "5",
    "driver_hend": "0",
    "stealthchop_threshold": "0",
    "iss_sg_target": "0",
    "iss_sg_deriv_thr": "0",
    "iss_current_ma": "400",
    "iss_trailing_sps": "1000",
    "iss_join_sps": "25000",
    "iss_press_sps": "15000",
    "iss_follow_timeout_ms": "10000",
}


def read_flat_ini(path):
    with open(path, "r") as f:
        content = f.read()
    if not any(line.strip().startswith("[") for line in content.splitlines()):
        content = "[DEFAULT]\n" + content
    cfg = configparser.ConfigParser(strict=False)  # tolerate duplicate keys (last wins)
    cfg.read_string(content)
    params = dict(cfg.defaults())
    for section in cfg.sections():
        for key, val in cfg.items(section):
            params[key] = val
    return params


def parse_gear_ratio(s):
    ratio = 1.0
    for part in s.split(","):
        nums = part.strip().split(":")
        if len(nums) == 2:
            ratio *= float(nums[0]) / float(nums[1])
    return ratio


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    output_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT

    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found.")
        print(f"  Copy config.ini.example to config.ini and fill in your values.")
        sys.exit(1)

    raw = read_flat_ini(config_path)
    params = {**KLIPPER_DEFAULTS, **raw}

    def get(key):
        return params.get(key, "").strip()

    missing = [k for k in MANDATORY if not get(k)]
    if missing:
        print(f"Error: mandatory fields not set in {config_path}: {', '.join(missing)}")
        print(f"  See config.ini.example for required fields.")
        sys.exit(1)

    microsteps = int(get("microsteps"))
    rotation_distance = float(get("rotation_distance"))
    run_current = float(get("run_current"))
    full_steps = int(get("full_steps_per_rotation") or "200")
    gear_ratio = parse_gear_ratio(get("gear_ratio") or "1:1")
    hold_str = get("hold_current")
    hold_current = float(hold_str) if hold_str else run_current / 2.0
    interpolate = get("interpolate").lower() in ("true", "1", "yes", "on")
    tbl = int(get("driver_tbl") or "2")
    toff = int(get("driver_toff") or "3")
    hstrt = int(get("driver_hstrt") or "5")
    hend = int(get("driver_hend") or "0")
    stealthchop = int(get("stealthchop_threshold") or "0")
    spreadcycle = (stealthchop == 0)
    iss_sg_target = float(get("iss_sg_target") or "0")
    iss_sg_deriv_thr = int(get("iss_sg_deriv_thr") or "0")
    iss_current_ma = int(get("iss_current_ma") or "400")
    iss_trailing_sps = int(get("iss_trailing_sps") or "1000")
    iss_join_sps = int(get("iss_join_sps") or "25000")
    iss_press_sps = int(get("iss_press_sps") or "15000")
    iss_follow_timeout_ms = int(get("iss_follow_timeout_ms") or "10000")

    mm_per_step = rotation_distance / (full_steps * microsteps * gear_ratio)
    run_ma = int(round(run_current * 1000))
    hold_ma = int(round(hold_current * 1000))
    intpol_str = "true" if interpolate else "false"
    spreadcycle_str = "true" if spreadcycle else "false"

    rel_config = os.path.relpath(config_path, REPO_ROOT)
    lines = [
        "#pragma once",
        "// AUTO-GENERATED — do not edit. Re-run: python3 scripts/gen_motor_config.py",
        f"// Source: {rel_config}",
        "",
        f"#define CONF_RUN_CURRENT_MA     {run_ma}",
        f"#define CONF_HOLD_CURRENT_MA    {hold_ma}",
        f"#define CONF_MICROSTEPS         {microsteps}",
        f"#define CONF_MM_PER_STEP        {mm_per_step:.7f}f",
        f"#define CONF_TOFF               {toff}",
        f"#define CONF_TBL                {tbl}",
        f"#define CONF_HSTRT              {hstrt}",
        f"#define CONF_HEND               {hend}",
        f"#define CONF_INTPOL             {intpol_str}",
        f"#define CONF_SPREADCYCLE        {spreadcycle_str}",
        f"#define CONF_ISS_CURRENT_MA     {iss_current_ma}",
        f"#define CONF_ISS_JOIN_SPS       {iss_join_sps}",
        f"#define CONF_ISS_PRESS_SPS      {iss_press_sps}",
        f"#define CONF_ISS_SG_TARGET      {iss_sg_target:.1f}f",
        f"#define CONF_ISS_SG_DERIV_THR   {iss_sg_deriv_thr}",
        f"#define CONF_ISS_TRAILING_SPS   {iss_trailing_sps}",
        f"#define CONF_ISS_FOLLOW_TIMEOUT_MS {iss_follow_timeout_ms}",
        "",
    ]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    rel_output = os.path.relpath(output_path, REPO_ROOT)
    print(f"Generated {rel_output}")
    print(f"  microsteps={microsteps}, rotation_distance={rotation_distance}, gear_ratio={gear_ratio:.4f}")
    print(f"  mm_per_step={mm_per_step:.7f}")
    print(f"  run={run_current}A ({run_ma}mA), hold={hold_current:.3f}A ({hold_ma}mA)")
    print(f"  TOFF={toff}, TBL={tbl}, HSTRT={hstrt}, HEND={hend}, INTPOL={intpol_str}")
    print(f"  spreadcycle={spreadcycle_str}")


if __name__ == "__main__":
    main()
