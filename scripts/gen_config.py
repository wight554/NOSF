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
    "gear_ratio": "50:17",
    "hold_current": "0.980",
    "interpolate": "False",
    "driver_tbl": "1",
    "driver_toff": "4",
    "driver_hstrt": "5",
    "driver_hend": "3",
    "stealthchop_threshold": "500",
    "dir_invert": "0",

    # Speeds (mm/min)
    "feed_rate": "3000",
    "rev_rate": "3000",
    "auto_rate": "3000",
    "buf_stab_rate": "600",
    "sync_max_rate": "4000",
    "global_max_rate": "4000",
    "sync_min_rate": "100",
    "pre_ramp_rate": "90",

    # Motion / Ramp
    "motion_startup_ms": "1000",
    "ramp_step_rate": "17.1",
    "ramp_tick_ms": "5",

    # Buffer Sync
    "buf_half_travel_mm": "7.8",
    "buf_hyst_ms": "30",
    "sync_ramp_up_rate": "40",
    "sync_ramp_dn_rate": "12",
    "sync_tick_ms": "20",
    "baseline_rate": "1600",
    "baseline_alpha": "0.02",
    "buf_predict_thr_ms": "250",
    "sync_kp_rate": "900",
    "sync_overshoot_pct": "25",
    "sync_reserve_pct": "35",
    "sync_auto_stop_ms": "5000",
    "post_print_stab_delay_ms": "0",
    "sync_advance_dwell_stop_ms": "6000",
    "sync_advance_ramp_delay_ms": "400",
    "sync_overshoot_mid_extend": "0",
    "sync_reserve_integral_gain": "0.0",
    "sync_reserve_integral_clamp_mm": "0.6",
    "sync_reserve_integral_decay_ms": "0",
    "est_sigma_hard_cap_mm": "1.5",
    "est_low_cf_warn_threshold": "0.5",
    "est_fallback_cf_threshold": "0.2",
    # Phase 2.6
    "buf_drift_ewma_tau_ms": "60000",
    "buf_drift_min_samples": "3",
    "buf_drift_apply_thr_mm": "2.0",
    "buf_drift_clamp_mm": "3.0",
    "buf_drift_apply_min_cf": "0.5",
    "adv_risk_window_ms": "60000",
    "adv_risk_threshold": "4",
    # Phase 2.7
    "sync_trailing_bias_frac": "0.4",
    "mid_creep_timeout_ms": "4000",
    "mid_creep_rate_sps_per_s": "5",
    "mid_creep_cap_frac": "10",
    "buf_variance_blend_frac": "0.5",
    "buf_variance_blend_ref_mm": "1.0",
    "est_alpha_min": "0.12",
    "est_alpha_max": "0.65",
    "zone_bias_base_rate": "90",
    "zone_bias_ramp_rate": "30",
    "zone_bias_max_rate": "600",
    "reload_lean_factor": "1.15",

    # Cutter / Servo
    "servo_open_us": "500",
    "servo_close_us": "1400",
    "servo_block_us": "950",
    "servo_settle_ms": "500",
    "cut_feed_mm": "48",
    "cut_length_mm": "10",
    "cut_amount": "1",

    # Toolchange / Safety
    "tc_timeout_cut_ms": "5000",
    "tc_timeout_th_ms": "3000",
    "tc_timeout_y_ms": "5000",

    # Safety / Swap
    "runout_cooldown_ms": "12000",
    "load_max_mm": "3000",
    "unload_max_mm": "3000",
    "autoload_max_mm": "600",
    "reload_y_timeout_ms": "10000",
    "auto_mode": "1",
    "auto_preload": "True",

    # Analog Buffer Sensor
    "buf_sensor_type": "0",
    "buf_neutral": "0.5",
    "buf_range": "0.45",
    "buf_thr": "0.30",
    "buf_analog_alpha": "0.20",

    # TS Fallback
    "ts_buf_fallback_ms": "2000",

    # Reload Mode
    "reload_mode": "1",
    "reload_y_timeout_ms": "10000",
    "reload_join_delay_ms": "10000",
    "trailing_rate": "90",
    "join_rate": "1600",
    "press_rate": "1200",
    "reload_touch_settle_ms": "120",
    "reload_touch_boost_ms": "900",
    "reload_touch_floor_pct": "90",
    "follow_timeout_ms": "10000",
    "dist_in_out": "150",
    "dist_out_y": "100",
    "dist_y_buf": "300",
    "buf_body_len": "200",
    "buf_size_mm": "22",
}


def read_flat_ini(path):
    with open(path, "r") as f:
        content = f.read()
    if not any(line.strip().startswith("[") for line in content.splitlines()):
        content = "[DEFAULT]\n" + content
    cfg = configparser.ConfigParser(strict=False, inline_comment_prefixes=('#', ';'))
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

    def get_list(key, default_val=""):
        val = get(key) or default_val
        return [p.strip() for p in val.split(",")]

    def get_motor_params(lane_idx):
        def gm(key, default=None):
            # 1. Check for suffixed override (e.g. run_current_l1)
            suffix = f"_l{lane_idx+1}"
            v = get(f"{key}{suffix}")
            if v: return v

            # 2. Check for global comma-separated list (e.g. run_current: 0.8, 0.9)
            #    Resolution order:
            #      a) parts[lane_idx]          — exact lane entry
            #      b) parts[0]                 — short list: first value covers all remaining lanes
            #      c) g_val (single value)     — no comma: value applies to every lane
            #      d) default
            g_val = get(key)
            if "," in g_val:
                parts = [p.strip() for p in g_val.split(",")]
                if lane_idx < len(parts):
                    return parts[lane_idx]
                # Fewer list entries than lanes → reuse first value for any extra lane
                return parts[0]
            # 3. Single value — apply to all lanes
            return g_val or default

        microsteps = int(gm("microsteps", "16"))
        rotation_distance = float(gm("rotation_distance", "0"))
        run_current = float(gm("run_current", "0.8"))
        full_steps = int(gm("full_steps_per_rotation", "200"))
        gear_ratio = parse_gear_ratio(gm("gear_ratio", "1:1"))
        hold_str = gm("hold_current")
        hold_current = float(hold_str) if hold_str else run_current / 2.0
        interpolate = (gm("interpolate", "True").lower() in ("true", "1", "yes", "on"))
        toff = int(gm("driver_toff", "3"))
        tbl = int(gm("driver_tbl", "2"))
        hstrt = int(gm("driver_hstrt", "5"))
        hend = int(gm("driver_hend", "0"))
        mm_per_step = rotation_distance / (full_steps * microsteps * gear_ratio) if rotation_distance > 0 else 0.0125
        stealthchop_threshold_mm_min = float(gm("stealthchop_threshold", "0"))
        stealthchop_sps = int(round(stealthchop_threshold_mm_min / 60.0 / mm_per_step)) if stealthchop_threshold_mm_min > 0 else 0
        
        # Direction
        dir_invert = int(gm("dir_invert", "0"))
        follow_timeout_ms = int(gm("follow_timeout_ms", "10000"))

        run_ma = int(round(run_current * 1000))
        hold_ma = int(round(hold_current * 1000))

        return {
            "microsteps": microsteps,
            "rotation_distance": rotation_distance,
            "full_steps": full_steps,
            "gear_ratio": gear_ratio,
            "run_ma": run_ma,
            "hold_ma": hold_ma,
            "interpolate": interpolate,
            "toff": toff,
            "tbl": tbl,
            "hstrt": hstrt,
            "hend": hend,
            "mm_per_step": mm_per_step,
            "stealthchop_sps": stealthchop_sps,
            "dir_invert": dir_invert,
            "follow_timeout_ms": follow_timeout_ms
        }

    # Generate for 2 lanes
    lanes = [get_motor_params(i) for i in range(2)]
    l1, l2 = lanes[0], lanes[1]

    def mm_min_to_sps(mm_min_str, m_params):
        mm_min = float(mm_min_str)
        if mm_min <= 0: return 0
        return int(round(mm_min / 60.0 / m_params["mm_per_step"]))

    rel_config = os.path.relpath(config_path, REPO_ROOT)
    lines = [
        "#pragma once",
        "// AUTO-GENERATED — do not edit. Re-run: python3 scripts/gen_config.py",
        f"// Source: {rel_config}",
        "",
        "// --- Lane 1 parameters ---",
        f"#define CONF_L1_RUN_CURRENT_MA     {l1['run_ma']}",
        f"#define CONF_L1_HOLD_CURRENT_MA    {l1['hold_ma']}",
        f"#define CONF_L1_MICROSTEPS         {l1['microsteps']}",
        f"#define CONF_L1_ROTATION_DISTANCE  {l1['rotation_distance']:.7f}f",
        f"#define CONF_L1_GEAR_RATIO         {l1['gear_ratio']:.7f}f",
        f"#define CONF_L1_FULL_STEPS         {l1['full_steps']}",
        f"#define CONF_L1_MM_PER_STEP        {l1['mm_per_step']:.7f}f",
        f"#define CONF_L1_TOFF               {l1['toff']}",
        f"#define CONF_L1_TBL                {l1['tbl']}",
        f"#define CONF_L1_HSTRT              {l1['hstrt']}",
        f"#define CONF_L1_HEND               {l1['hend']}",
        f"#define CONF_L1_INTPOL             {'true' if l1['interpolate'] else 'false'}",
        f"#define CONF_L1_STEALTHCHOP_THRESHOLD {l1['stealthchop_sps']}",
        f"#define CONF_L1_FOLLOW_TIMEOUT_MS  {l1['follow_timeout_ms']}",
        "",
        "// --- Lane 2 parameters ---",
        f"#define CONF_L2_RUN_CURRENT_MA     {l2['run_ma']}",
        f"#define CONF_L2_HOLD_CURRENT_MA    {l2['hold_ma']}",
        f"#define CONF_L2_MICROSTEPS         {l2['microsteps']}",
        f"#define CONF_L2_ROTATION_DISTANCE  {l2['rotation_distance']:.7f}f",
        f"#define CONF_L2_GEAR_RATIO         {l2['gear_ratio']:.7f}f",
        f"#define CONF_L2_FULL_STEPS         {l2['full_steps']}",
        f"#define CONF_L2_MM_PER_STEP        {l2['mm_per_step']:.7f}f",
        f"#define CONF_L2_TOFF               {l2['toff']}",
        f"#define CONF_L2_TBL                {l2['tbl']}",
        f"#define CONF_L2_HSTRT              {l2['hstrt']}",
        f"#define CONF_L2_HEND               {l2['hend']}",
        f"#define CONF_L2_INTPOL             {'true' if l2['interpolate'] else 'false'}",
        f"#define CONF_L2_STEALTHCHOP_THRESHOLD {l2['stealthchop_sps']}",
        f"#define CONF_L2_FOLLOW_TIMEOUT_MS  {l2['follow_timeout_ms']}",
        "",
        "// --- Direction Inverts ---",
        f"#define CONF_L1_DIR_INVERT      {l1['dir_invert']}",
        f"#define CONF_L2_DIR_INVERT      {l2['dir_invert']}",
        "",
        "// --- Speeds (converted to SPS using Lane 1 baseline) ---",
        f"#define CONF_FEED_SPS           {mm_min_to_sps(get('feed_rate'), l1)}",
        f"#define CONF_REV_SPS            {mm_min_to_sps(get('rev_rate'), l1)}",
        f"#define CONF_AUTO_SPS           {mm_min_to_sps(get('auto_rate'), l1)}",
        f"#define CONF_BUF_STAB_SPS       {mm_min_to_sps(get('buf_stab_rate'), l1)}",
        f"#define CONF_SYNC_MAX_SPS       {mm_min_to_sps(get('sync_max_rate'), l1)}",
        f"#define CONF_SYNC_MIN_SPS       {mm_min_to_sps(get('sync_min_rate'), l1)}",
        f"#define CONF_PRE_RAMP_SPS       {mm_min_to_sps(get('pre_ramp_rate'), l1)}",
        "",
        "// --- Motion / Ramp ---",
        f"#define CONF_MOTION_STARTUP_MS  {get('motion_startup_ms')}",
        f"#define CONF_RAMP_STEP_SPS      {mm_min_to_sps(get('ramp_step_rate'), l1)}",
        f"#define CONF_RAMP_TICK_MS       {get('ramp_tick_ms')}",
        "",
        "// --- Buffer Sync ---",
        f"#define CONF_BUF_HALF_TRAVEL_MM {get_float('buf_half_travel_mm')}f",
        f"#define CONF_BUF_HYST_MS        {get('buf_hyst_ms')}",
        f"#define CONF_SYNC_RAMP_UP_SPS   {mm_min_to_sps(get('sync_ramp_up_rate'), l1)}",
        f"#define CONF_SYNC_RAMP_DN_SPS   {mm_min_to_sps(get('sync_ramp_dn_rate'), l1)}",
        f"#define CONF_SYNC_TICK_MS       {get('sync_tick_ms')}",
        f"#define CONF_BASELINE_SPS       {mm_min_to_sps(get('baseline_rate'), l1)}",
        f"#define CONF_BASELINE_ALPHA     {get_float('baseline_alpha')}f",
        f"#define CONF_BUF_PREDICT_THR_MS {get('buf_predict_thr_ms')}",
        f"#define CONF_GLOBAL_MAX_SPS      {mm_min_to_sps(get('global_max_rate'), l1)}",
        f"#define CONF_SYNC_KP_SPS        {mm_min_to_sps(get('sync_kp_rate'), l1)}",
        f"#define CONF_SYNC_OVERSHOOT_PCT {get('sync_overshoot_pct')}",
        f"#define CONF_SYNC_RESERVE_PCT   {get('sync_reserve_pct')}",
        f"#define CONF_SYNC_AUTO_STOP_MS {get('sync_auto_stop_ms')}",
        f"#define CONF_POST_PRINT_STAB_DELAY_MS {get('post_print_stab_delay_ms')}",
        f"#define CONF_SYNC_ADVANCE_DWELL_STOP_MS {get('sync_advance_dwell_stop_ms')}",
        f"#define CONF_SYNC_ADVANCE_RAMP_DELAY_MS {get('sync_advance_ramp_delay_ms')}",
        f"#define CONF_SYNC_OVERSHOOT_MID_EXTEND {get('sync_overshoot_mid_extend')}",
        f"#define CONF_SYNC_RESERVE_INTEGRAL_GAIN {get_float('sync_reserve_integral_gain')}f",
        f"#define CONF_SYNC_TRAILING_BIAS_FRAC {get_float('sync_trailing_bias_frac')}f",
        f"#define CONF_MID_CREEP_TIMEOUT_MS {get('mid_creep_timeout_ms')}",
        f"#define CONF_MID_CREEP_RATE_SPS_PER_S {get('mid_creep_rate_sps_per_s')}",
        f"#define CONF_MID_CREEP_CAP_FRAC {get('mid_creep_cap_frac')}",
        f"#define CONF_BUF_VARIANCE_BLEND_FRAC {get_float('buf_variance_blend_frac')}f",
        f"#define CONF_BUF_VARIANCE_BLEND_REF_MM {get_float('buf_variance_blend_ref_mm')}f",
        f"#define CONF_SYNC_RESERVE_INTEGRAL_CLAMP_MM {get_float('sync_reserve_integral_clamp_mm')}f",
        f"#define CONF_SYNC_RESERVE_INTEGRAL_DECAY_MS {get('sync_reserve_integral_decay_ms')}",
        f"#define CONF_EST_SIGMA_HARD_CAP_MM {get_float('est_sigma_hard_cap_mm')}f",
        f"#define CONF_EST_LOW_CF_WARN_THRESHOLD {get_float('est_low_cf_warn_threshold')}f",
        f"#define CONF_EST_FALLBACK_CF_THRESHOLD {get_float('est_fallback_cf_threshold')}f",
        f"#define CONF_BUF_DRIFT_EWMA_TAU_MS {get('buf_drift_ewma_tau_ms')}",
        f"#define CONF_BUF_DRIFT_MIN_SAMPLES {get('buf_drift_min_samples')}",
        f"#define CONF_BUF_DRIFT_APPLY_THR_MM {get_float('buf_drift_apply_thr_mm')}f",
        f"#define CONF_BUF_DRIFT_CLAMP_MM {get_float('buf_drift_clamp_mm')}f",
        f"#define CONF_BUF_DRIFT_APPLY_MIN_CF {get_float('buf_drift_apply_min_cf')}f",
        f"#define CONF_ADV_RISK_WINDOW_MS {get('adv_risk_window_ms')}",
        f"#define CONF_ADV_RISK_THRESHOLD {get('adv_risk_threshold')}",
        f"#define CONF_EST_ALPHA_MIN        {get_float('est_alpha_min')}f",
        f"#define CONF_EST_ALPHA_MAX        {get_float('est_alpha_max')}f",
        f"#define CONF_ZONE_BIAS_BASE_SPS   {mm_min_to_sps(get('zone_bias_base_rate'), l1)}",
        f"#define CONF_ZONE_BIAS_RAMP_SPS_S {mm_min_to_sps(get('zone_bias_ramp_rate'), l1)}",
        f"#define CONF_ZONE_BIAS_MAX_SPS    {mm_min_to_sps(get('zone_bias_max_rate'), l1)}",
        f"#define CONF_RELOAD_LEAN_FACTOR   {get_float('reload_lean_factor')}f",
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
        f"#define CONF_TC_TIMEOUT_TH_MS       {get('tc_timeout_th_ms')}",
        f"#define CONF_TC_TIMEOUT_Y_MS        {get('tc_timeout_y_ms')}",
        f"#define CONF_LOAD_MAX_MM            {get('load_max_mm')}",
        f"#define CONF_UNLOAD_MAX_MM          {get('unload_max_mm')}",
        f"#define CONF_AUTOLOAD_MAX_MM        {get('autoload_max_mm')}",
        f"#define CONF_RELOAD_Y_TIMEOUT_MS   {get('reload_y_timeout_ms')}",
        f"#define CONF_RELOAD_JOIN_DELAY_MS  {get('reload_join_delay_ms')}",
        f"#define CONF_AUTO_MODE              {1 if get_bool('auto_mode') else 0}",
        f"#define CONF_AUTO_PRELOAD           {1 if get_bool('auto_preload') else 0}",
        "",
        "// --- Safety / Swap ---",
        f"#define CONF_RUNOUT_COOLDOWN_MS     {get('runout_cooldown_ms')}",
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
        "// --- Reload Mode ---",
        f"#define CONF_RELOAD_MODE           {get('reload_mode')}",
        f"#define CONF_JOIN_SPS           {mm_min_to_sps(get('join_rate'), l1)}",
        f"#define CONF_PRESS_SPS          {mm_min_to_sps(get('press_rate'), l1)}",
        f"#define CONF_TRAILING_SPS       {mm_min_to_sps(get('trailing_rate'), l1)}",
        f"#define CONF_RELOAD_TOUCH_SETTLE_MS {get('reload_touch_settle_ms')}",
        f"#define CONF_RELOAD_TOUCH_BOOST_MS  {get('reload_touch_boost_ms')}",
        f"#define CONF_RELOAD_TOUCH_FLOOR_PCT {get('reload_touch_floor_pct')}",
        f"#define CONF_FOLLOW_TIMEOUT_MS  {get('follow_timeout_ms')}",
        "",
        "// --- Physical Model ---",
        f"#define CONF_DIST_IN_OUT            {get('dist_in_out')}",
        f"#define CONF_DIST_OUT_Y             {get('dist_out_y')}",
        f"#define CONF_DIST_Y_BUF             {get('dist_y_buf')}",
        f"#define CONF_BUF_BODY_LEN           {get('buf_body_len')}",
        f"#define CONF_BUF_SIZE_MM            {get('buf_size_mm')}",
        "",
    ]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"Generated {os.path.relpath(output_path, REPO_ROOT)}")


if __name__ == "__main__":
    main()
