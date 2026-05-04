#pragma once

// =====================================================================
// NOSF ERB V2.0 — Compile-time configuration
// Edit this file to tune hardware parameters before building.
// Runtime tunables are available via SET:/GET: USB commands.
// =====================================================================

// Motor parameters are generated from config.ini.
// Run: python3 scripts/gen_motor_config.py
#include "tune.h"

// ----- Sense resistor -----
// ERB V2.0 onboard Rsense (R46/R47, R48/R49) — hardware constant, not user-tunable
#define CONF_RSENSE_OHM         0.110f  // Ohms — confirmed from schematic

// ----- Speeds (steps per second) -----
// At MM_PER_STEP=0.001417: 25000 SPS ≈ 35 mm/s, 50000 SPS ≈ 71 mm/s
#define CONF_FEED_SPS           25000
#define CONF_REV_SPS            25000
#define CONF_AUTO_SPS           25000
#define CONF_SYNC_MAX_SPS       30000
#define CONF_SYNC_MIN_SPS       0

// ----- Motion -----
#define CONF_MOTION_STARTUP_MS  10000   // TUNE: bowden length dependent

// ----- Ramp -----
#define CONF_RAMP_STEP_SPS      200
#define CONF_RAMP_TICK_MS       5

// ----- Buffer sync -----
#define CONF_BUF_HALF_TRAVEL_MM 5.0f    // TUNE: measure from printed TN-Pro/QuattroSync
#define CONF_BUF_HYST_MS        30
#define CONF_SYNC_RATIO         1.0f    // TUNE: MMU gear / extruder gear ratio
#define CONF_SYNC_RAMP_UP_SPS   300
#define CONF_SYNC_RAMP_DN_SPS   150
#define CONF_SYNC_TICK_MS       20
#define CONF_PRE_RAMP_SPS       400
#define CONF_BASELINE_ALPHA     0.15f
#define CONF_BUF_PREDICT_THR_MS 250

// ----- Cutter / servo -----
// TUNE: calibrate by jogging servo manually
#define CONF_SERVO_OPEN_US      500
#define CONF_SERVO_CLOSE_US     1400
#define CONF_SERVO_BLOCK_US     950
#define CONF_SERVO_SETTLE_MS    500
#define CONF_CUT_FEED_MM        48      // TUNE: distance from park to encoder exit
#define CONF_CUT_LENGTH_MM      10
#define CONF_CUT_AMOUNT         1

// ----- Toolchange timeouts -----
#define CONF_TC_TIMEOUT_CUT_MS      5000
#define CONF_TC_TIMEOUT_UNLOAD_MS   60000
#define CONF_TC_TIMEOUT_TH_MS       3000  // 0 = don't wait for TS: from host
#define CONF_TC_TIMEOUT_LOAD_MS     60000
#define CONF_TC_TIMEOUT_Y_MS        5000  // 0 = skip Y-splitter wait on unload

// ----- Stall recovery (sync mode) -----
// During buffer sync, a stall most likely means high filament tension rather
// than a jam. The firmware lets sync_tick ramp back up and clears the state
// after this window. If the motor stalls again within the window it's a real
// jam and a hard stop is issued. Set to 0 to hard-stop on first stall always.
#define CONF_STALL_RECOVERY_MS      3000

// ----- Safety / swap -----
#define CONF_LOW_DELAY_MS           400
#define CONF_SWAP_COOLDOWN_MS       500
#define CONF_RUNOUT_COOLDOWN_MS     12000
#define CONF_REQUIRE_Y_EMPTY_SWAP   true

// ----- StallGuard -----
// SGTHRS=0 disables stall detection (DIAG never fires). Tune before enabling:
// run motor loaded, read SG: value, set SGTHRS ≈ SG_RESULT/2.
// See BEHAVIOR.md for full procedure.
#define CONF_SGT_L1             0
#define CONF_SGT_L2             0
// TCOOLTHRS: StallGuard active when TSTEP <= TCOOLTHRS. TSTEP = CLK/SPS
// (~12.5 MHz / 25000 SPS ≈ 500). Set above max operating TSTEP so SG is
// active across the full speed range when SGTHRS > 0.
#define CONF_TCOOLTHRS          1000

// ----- StallGuard buffer sync -----
// SG_SYNC_THR: SG_RESULT below this = under tension → apply speed trim.
// 0 disables SG-based sync trim (safe default; calibrate before enabling).
// Typical starting point: read SG: during steady sync, set to ~80% of that.
#define CONF_SG_SYNC_THR        0
#define CONF_SG_SYNC_TRIM_SPS   200     // extra SPS added when under tension
#define CONF_SG_ALPHA           0.20f   // EMA weight for SG filter (higher = faster response)

// ----- Analog buffer sensor (PSF / Hall-effect) -----
// Set BUF_SENSOR_TYPE=1 to enable; wire signal pin to GP26–GP29.
#define CONF_BUF_SENSOR_TYPE    0       // 0=dual-endstop, 1=analog PSF
#define CONF_BUF_NEUTRAL        0.5f    // TUNE: ADC fraction at mechanical neutral
#define CONF_BUF_RANGE          0.45f   // TUNE: ADC fraction from neutral to full deflection
#define CONF_BUF_THR            0.30f   // TUNE: normalised threshold for ADVANCE/TRAILING
#define CONF_BUF_ANALOG_ALPHA   0.20f   // EMA weight for analog pos filter

// ----- Sync proportional gain -----
// Total speed offset (SPS) applied when buffer is at full deflection (±1).
// In mm/min ≈ CONF_SYNC_KP_SPS * MM_PER_STEP * 60.
#define CONF_SYNC_KP_SPS        10000

// ----- TS:1 buffer fallback -----
// During FL:/TC load: if buffer holds TRAILING for this many ms after OUT seen,
// treat as filament-at-toolhead (tip pressed against extruder gears). 0 = disabled.
#define CONF_TS_BUF_FALLBACK_MS 2000

// ----- Direction invert -----
// Set to 1 if motor runs backward on LO: command
#define CONF_M1_DIR_INVERT      0   // VERIFY: check physically
#define CONF_M2_DIR_INVERT      0   // VERIFY: check physically

// ----- ISS mode -----
#define CONF_ISS_MODE           0       // 0 = MMU, 1 = Infinite Spool System
#define CONF_ISS_Y_TIMEOUT_MS   10000   // max wait for Y-splitter to clear after runout (ms)

// --- State 1: Fast Approach (TC_ISS_APPROACH) ---
// ISS_JOIN_SPS: approach speed; must exceed max print speed so we catch up quickly.
#define CONF_ISS_JOIN_SPS       25000
// ISS_SG_MA_LEN: moving-average window for SG during approach (samples at SYNC_TICK_MS).
#define CONF_ISS_SG_MA_LEN      5
// ISS_SG_DERIV_THR: |drop in filtered SG per tick| that signals tip-to-tail contact.
// Tune: free-air SG ≈ 14, full crash ≈ 0 → threshold ≈ 3–5 SG units/tick.
#define CONF_ISS_SG_DERIV_THR   3

// --- State 2: Follow Sync (TC_ISS_FOLLOW) ---
// ISS_PRESS_SPS: top speed in follow sync (ADVANCE or MID, SG near free-air).
// Keep above max print speed so the buffer stays TRAILING-biased.
// SG active in SpeedCycle when SPS >= CLK/TCOOLTHRS (~12500 at default TCOOLTHRS=1000).
#define CONF_ISS_PRESS_SPS      15000
// ISS_TRAILING_SPS: coasting speed when buffer is TRAILING.
// Set well below print speed: extruder pulling faster creates SG-detectable tension.
#define CONF_ISS_TRAILING_SPS   8000
// ISS_SG_TARGET: desired filtered SG value in follow sync.
// Must be > 0 (crash) and < free-air SG (~14). Typical starting point: ~7.
// Motor speed is proportionally interpolated between 0 (SG=0) and ISS_PRESS_SPS (SG≥target).
#define CONF_ISS_SG_TARGET      7

// ----- Firmware version -----
#define CONF_FW_VERSION         "NOSF_ERB_0.2.0"

