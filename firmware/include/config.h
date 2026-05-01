#pragma once

// =====================================================================
// NightOwl ERB V2.0 — Compile-time configuration
// Edit this file to tune hardware parameters before building.
// Runtime tunables are available via SET:/GET: USB commands.
// =====================================================================

// ----- Motor current -----
// Rated current of motor: 1A (G36HSY4405-6D-80)
// Run at 80-85% of rated for thermal margin
// NOTE: at 800mA with Rsense=0.110, firmware auto-selects VSENSE=1 (CS=25)
// If you lower current below ~580mA, VSENSE=0 may be selected but CHOPCONF
// still writes VSENSE=1 — update CONF_CHOPCONF_VSENSE if changing current significantly
#define CONF_RUN_CURRENT_MA     800     // RMS mA
#define CONF_HOLD_CURRENT_MA    300     // RMS mA (hold)

// ----- Sense resistor -----
// ERB V2.0 onboard Rsense (R46/R47, R48/R49)
#define CONF_RSENSE_OHM         0.110f  // Ohms — confirmed from schematic

// ----- CHOPCONF -----
// Values from Trinamic spreadsheet calculator for this motor
// (G36HSY4405-6D-80, 12V, 0.8A RMS)
#define CONF_TOFF               3       // Off time (1-15)
#define CONF_TBL                1       // Blank time (0=16, 1=24, 2=36, 3=54)
#define CONF_HSTRT              8       // Hysteresis start (1 to 8)
#define CONF_HEND               7       // Hysteresis end (-3 to 12)

// ----- Microsteps -----
// Hardware-fixed by MS1/MS2 on ERB (both pulled low = 8 microsteps)
// UART write of MRES is ignored when MS1/MS2 set hardware mode
// Set to match actual hardware microstep setting
#define CONF_MICROSTEPS         8

// ----- MM per step -----
// Depends on drive gear fitted to motor shaft
// Formula: (pi * gear_diameter_mm) / (200 * microsteps)
// TUNE: measure empirically — command 100mm, measure actual movement
#define CONF_MM_PER_STEP        0.0125f // TUNE: placeholder

// ----- Speeds (steps per second) -----
#define CONF_FEED_SPS           5000
#define CONF_REV_SPS            4000
#define CONF_AUTO_SPS           6000
#define CONF_SYNC_MAX_SPS       8000
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
#define CONF_TC_TIMEOUT_UNLOAD_MS   8000
#define CONF_TC_TIMEOUT_TH_MS       3000  // 0 = don't wait for TS: from host
#define CONF_TC_TIMEOUT_LOAD_MS     15000

// ----- Safety / swap -----
#define CONF_LOW_DELAY_MS           400
#define CONF_SWAP_COOLDOWN_MS       500
#define CONF_RUNOUT_COOLDOWN_MS     12000
#define CONF_REQUIRE_Y_EMPTY_SWAP   true

// ----- StallGuard -----
// TUNE: run motor at load, read SG: value, set threshold to ~50% of that
#define CONF_SGT_L1             80
#define CONF_SGT_L2             80
#define CONF_TCOOLTHRS          400

// ----- Direction invert -----
// Set to 1 if motor runs backward on LO: command
#define CONF_M1_DIR_INVERT      0   // VERIFY: check physically
#define CONF_M2_DIR_INVERT      1   // VERIFY: check physically

// ----- Firmware version -----
#define CONF_FW_VERSION         "NIGHTOWL_ERB_0.2.0"

