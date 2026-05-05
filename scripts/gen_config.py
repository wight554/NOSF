#!/usr/bin/env python3
"""
Generate firmware/include/tune.h from config.ini.
Includes all user-tunable firmware parameters.
Speeds are specified in mm/min.
"""

import configparser
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

DEFAULT_CONFIG = os.path.join(REPO_ROOT, "config.ini")
DEFAULT_OUTPUT = os.path.join(REPO_ROOT, "firmware", "include", "tune.h")

MANDATORY = ("microsteps", "rotation_distance", "run_current")

# --- Defaults (merged from config.h and Klipper standards) ---
DEFAULTS = {
    # Motor / TMC
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
    "m1_dir_invert": "0",
    "m2_dir_invert": "0",
    "tcoolthrs": "0xFFFFF",
    "sgt_l1": "0",
    "sgt_l2": "0",

    # Speeds (mm/min)
    "feed_rate": "2100",
    "rev_rate": "2100",
    "auto_rate": "2100",
    "sync_max_rate": "2500",
    "sync_min_rate": "0",
    "pre_ramp_rate": "35",

    # Motion / Ramp
    "motion_startup_ms": "1000",
    "ramp_step_rate": "17",
    "ramp_tick_ms": "5",
    "stall_recovery_ms": "3000",

    # Buffer Sync
    "buf_half_travel_mm": "5.0",
    "buf_hyst_ms": "30",
    "sync_ratio": "1.0",
    "sync_ramp_up_rate": "25",
    "sync_ramp_dn_rate": "13",
    "sync_tick_ms": "20",
    "baseline_alpha": "0.15",
    "buf_predict_thr_ms": "250",
    "sync_kp_rate": "850",

    # Cutter / Servo
    "servo_open_us": "500",
    "servo_close_us": "1400",
    "servo_block_us": "950",
    "servo_settle_ms": "500",
    "cut_feed_mm": "48",
    "cut_length_mm": "10",
    "cut_amount": "1",

    # Toolchange Timeouts
    "tc_timeout_cut_ms": "5000",
    "tc_timeout_unload_ms": "60000",
    "tc_timeout_th_ms": "3000",
    "tc_timeout_load_ms": "60000",
    "tc_timeout_y_ms": "5000",

    # Safety / Swap
    "low_delay_ms": "400",
    "swap_cooldown_ms": "500",
    "runout_cooldown_ms": "12000",
    "require_y_empty_swap": "True",

    # Analog Buffer Sensor
    "buf_sensor_type": "0",
    "buf_neutral": "0.5",
    "buf_range": "0.45",
    "buf_thr": "0.30",
    "buf_analog_alpha": "0.20",

    # TS Fallback
    "ts_buf_fallback_ms": "2000",

    # ISS Mode
    "iss_mode": "0",
    "iss_y_timeout_ms": "10000",
    "iss_sg_target": "0.0",
    "iss_sg_deriv_thr": "0",
    "iss_current_ma": "400",
    "iss_trailing_rate": "42",
    "iss_join_rate": "2100",
    "iss_press_rate": "1275",
    "iss_sg_ma_len": "5",
    "iss_follow_timeout_ms": "10000",
}


def read_flat_ini(path):
    with open(path, "r") as f:
        content = f.read()
    if not any(line.strip().startswith("[") for line in content.splitlines()):
        content = "[DEFAULT]\n" + content
    cfg = configparser.ConfigParser(strict=False)
    cfg.read_string(content)
    params = dict(cfg.defaults())
    for section in cfg.sections():
        for key, val in cfg.items(section):
            params[key.lower()] = val
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
    params = {**DEFAULTS, **raw}

    def get(key):
        return str(params.get(key, "")).strip()

    def get_bool(key):
        return get(key).lower() in ("true", "1", "yes", "on")

    def get_float(key):
        v = get(key)
        try:
            return float(v)
        except ValueError:
            return 0.0

    missing = [k for k in MANDATORY if not get(k)]
    if missing:
        print(f"Error: mandatory fields not set in {config_path}: {', '.join(missing)}")
        sys.exit(1)

    # Derived
    microsteps = int(get("microsteps"))
    rotation_distance = float(get("rotation_distance"))
    run_current = float(get("run_current"))
    full_steps = int(get("full_steps_per_rotation"))
    gear_ratio = parse_gear_ratio(get("gear_ratio"))
    hold_str = get("hold_current")
    hold_current = float(hold_str) if hold_str else run_current / 2.0
    interpolate = get_bool("interpolate")
    
    mm_per_step = rotation_distance / (full_steps * microsteps * gear_ratio)
    run_ma = int(round(run_current * 1000))
    hold_ma = int(round(hold_current * 1000))

    def mm_min_to_sps(mm_min_str):
        mm_min = float(mm_min_str)
        if mm_min <= 0: return 0
        return int(round(mm_min / 60.0 / mm_per_step))

    rel_config = os.path.relpath(config_path, REPO_ROOT)
    lines = [
        "#pragma once",
        "// AUTO-GENERATED — do not edit. Re-run: python3 scripts/gen_config.py",
        f"// Source: {rel_config}",
        "",
        "// --- Motor / TMC ---",
        f"#define CONF_RUN_CURRENT_MA     {run_ma}",
        f"#define CONF_HOLD_CURRENT_MA    {hold_ma}",
        f"#define CONF_MICROSTEPS         {microsteps}",
        f"#define CONF_MM_PER_STEP        {mm_per_step:.7f}f",
        f"#define CONF_TOFF               {get('driver_toff')}",
        f"#define CONF_TBL                {get('driver_tbl')}",
        f"#define CONF_HSTRT              {get('driver_hstrt')}",
        f"#define CONF_HEND               {get('driver_hend')}",
        f"#define CONF_INTPOL             {'true' if interpolate else 'false'}",
        f"#define CONF_SPREADCYCLE        {'true' if int(get('stealthchop_threshold')) == 0 else 'false'}",
        f"#define CONF_M1_DIR_INVERT      {get('m1_dir_invert')}",
        f"#define CONF_M2_DIR_INVERT      {get('m2_dir_invert')}",
        f"#define CONF_TCOOLTHRS          {get('tcoolthrs')}",
        f"#define CONF_SGT_L1             {get('sgt_l1')}",
        f"#define CONF_SGT_L2             {get('sgt_l2')}",
        "",
        "// --- Speeds (converted to SPS) ---",
        f"#define CONF_FEED_SPS           {mm_min_to_sps(get('feed_rate'))}",
        f"#define CONF_REV_SPS            {mm_min_to_sps(get('rev_rate'))}",
        f"#define CONF_AUTO_SPS           {mm_min_to_sps(get('auto_rate'))}",
        f"#define CONF_SYNC_MAX_SPS       {mm_min_to_sps(get('sync_max_rate'))}",
        f"#define CONF_SYNC_MIN_SPS       {mm_min_to_sps(get('sync_min_rate'))}",
        f"#define CONF_PRE_RAMP_SPS       {mm_min_to_sps(get('pre_ramp_rate'))}",
        "",
        "// --- Motion / Ramp ---",
        f"#define CONF_MOTION_STARTUP_MS  {get('motion_startup_ms')}",
        f"#define CONF_RAMP_STEP_SPS      {mm_min_to_sps(get('ramp_step_rate'))}",
        f"#define CONF_RAMP_TICK_MS       {get('ramp_tick_ms')}",
        f"#define CONF_STALL_RECOVERY_MS  {get('stall_recovery_ms')}",
        "",
        "// --- Buffer Sync ---",
        f"#define CONF_BUF_HALF_TRAVEL_MM {get_float('buf_half_travel_mm')}f",
        f"#define CONF_BUF_HYST_MS        {get('buf_hyst_ms')}",
        f"#define CONF_SYNC_RATIO         {get_float('sync_ratio')}f",
        f"#define CONF_SYNC_RAMP_UP_SPS   {mm_min_to_sps(get('sync_ramp_up_rate'))}",
        f"#define CONF_SYNC_RAMP_DN_SPS   {mm_min_to_sps(get('sync_ramp_dn_rate'))}",
        f"#define CONF_SYNC_TICK_MS       {get('sync_tick_ms')}",
        f"#define CONF_BASELINE_ALPHA     {get_float('baseline_alpha')}f",
        f"#define CONF_BUF_PREDICT_THR_MS {get('buf_predict_thr_ms')}",
        f"#define CONF_SYNC_KP_SPS        {mm_min_to_sps(get('sync_kp_rate'))}",
        "",
        "// --- Cutter / Servo ---",
        f"#define CONF_SERVO_OPEN_US      {get('servo_open_us')}",
        f"#define CONF_SERVO_CLOSE_US     {get('servo_close_us')}",
        f"#define CONF_SERVO_BLOCK_US     {get('servo_block_us')}",
        f"#define CONF_SERVO_SETTLE_MS    {get('servo_settle_ms')}",
        f"#define CONF_CUT_FEED_MM        {get('cut_feed_mm')}",
        f"#define CONF_CUT_LENGTH_MM      {get('cut_length_mm')}",
        f"#define CONF_CUT_AMOUNT         {get('cut_amount')}",
        "",
        "// --- Toolchange Timeouts ---",
        f"#define CONF_TC_TIMEOUT_CUT_MS      {get('tc_timeout_cut_ms')}",
        f"#define CONF_TC_TIMEOUT_UNLOAD_MS   {get('tc_timeout_unload_ms')}",
        f"#define CONF_TC_TIMEOUT_TH_MS       {get('tc_timeout_th_ms')}",
        f"#define CONF_TC_TIMEOUT_LOAD_MS     {get('tc_timeout_load_ms')}",
        f"#define CONF_TC_TIMEOUT_Y_MS        {get('tc_timeout_y_ms')}",
        "",
        "// --- Safety / Swap ---",
        f"#define CONF_LOW_DELAY_MS           {get('low_delay_ms')}",
        f"#define CONF_SWAP_COOLDOWN_MS       {get('swap_cooldown_ms')}",
        f"#define CONF_RUNOUT_COOLDOWN_MS     {get('runout_cooldown_ms')}",
        f"#define CONF_REQUIRE_Y_EMPTY_SWAP   {'true' if get_bool('require_y_empty_swap') else 'false'}",
        "",
        "// --- Analog Buffer Sensor ---",
        f"#define CONF_BUF_SENSOR_TYPE    {get('buf_sensor_type')}",
        f"#define CONF_BUF_NEUTRAL        {get_float('buf_neutral'):.3f}f",
        f"#define CONF_BUF_RANGE          {get_float('buf_range'):.3f}f",
        f"#define CONF_BUF_THR            {get_float('buf_thr'):.3f}f",
        f"#define CONF_BUF_ANALOG_ALPHA   {get_float('buf_analog_alpha'):.3f}f",
        "",
        "// --- TS Fallback ---",
        f"#define CONF_TS_BUF_FALLBACK_MS {get('ts_buf_fallback_ms')}",
        "",
        "// --- ISS Mode ---",
        f"#define CONF_ISS_MODE           {get('iss_mode')}",
        f"#define CONF_ISS_Y_TIMEOUT_MS   {get('iss_y_timeout_ms')}",
        f"#define CONF_ISS_CURRENT_MA     {get('iss_current_ma')}",
        f"#define CONF_ISS_JOIN_SPS       {mm_min_to_sps(get('iss_join_rate'))}",
        f"#define CONF_ISS_PRESS_SPS      {mm_min_to_sps(get('iss_press_rate'))}",
        f"#define CONF_ISS_SG_TARGET      {get_float('iss_sg_target'):.1f}f",
        f"#define CONF_ISS_SG_DERIV_THR   {get('iss_sg_deriv_thr')}",
        f"#define CONF_ISS_TRAILING_SPS   {mm_min_to_sps(get('iss_trailing_rate'))}",
        f"#define CONF_ISS_SG_MA_LEN      {get('iss_sg_ma_len')}",
        f"#define CONF_ISS_FOLLOW_TIMEOUT_MS {get('iss_follow_timeout_ms')}",
        "",
    ]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"Generated {os.path.relpath(output_path, REPO_ROOT)}")


if __name__ == "__main__":
    main()
