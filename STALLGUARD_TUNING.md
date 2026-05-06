# NOSF — StallGuard Auto-Tuning Guide

This guide covers the automated workflow for calibrating StallGuard thresholds (`SGTHRS`) starting from an empty MMU.

---

## Tool Overview

| Script | Purpose |
|--------|---------|
| `scripts/gcode_marker.py` | Injects flow-aware sync markers into G-code files. |
| `scripts/sg_tuner.py` | Live-sweeps `SGTHRS`, records data, and fits a physics-based model. |
| `scripts/motors.ini` | Database of motor constants and tuning baselines. |

---

## Step 1: Klipper Setup

Ensure `M118` (Respond) is enabled in your `printer.cfg`:

```ini
[respond]
```

---

## Step 2: Prepare the G-code

Calculate the effective filament speed based on line geometry:

```bash
# Process your sliced G-code
python3 scripts/gcode_marker.py my_model.gcode
```

---

## Step 3: Zero-to-Tuned Workflow

Follow these steps to prepare your MMU and start the tuning session:

### 1. Preload Filament (`LO`)
Insert your filament into the desired MMU lane. The MMU will detect the insertion (if `AUTO_PRELOAD` is on) or you can trigger it manually:
*   **Action**: Insert filament.
*   **Result**: MMU pushes filament to the output sensor/Y-splitter. It is now "Preloaded."

### 2. Load to Toolhead (`FL`)
Send the **Full Load** command to push the filament from the MMU to the printer's extruder:
*   **Command**: `FL:1` (for Lane 1).
*   **Result**: The MMU pushes until it hits the extruder gears. The firmware detects the resistance (Buffer `TRAILING` or `ADVANCE`) and stops automatically.

### 3. Start the Tuning Session
1.  **Start Print**: Start the `my_model_geo.gcode` file in Klipper.
2.  **Run Tuner**: Immediately start the tuner script on your PC/Pi.

```bash
python3 scripts/sg_tuner.py --baseline <motor_name> --fine-tune
```

### 4. Automatic Synchronization
*   **Pull-to-Sync**: As Klipper begins the first extrusion move, the extruder pulls the filament.
*   **Handshake**: The MMU detects the pull (Buffer `ADVANCE`) and instantly enables sync mode.
*   **Collection**: The `sg_tuner.py` script proactively sets all TMC parameters and begins the `SGTHRS` sweep.

---

## Step 4: Analysis & Recommendation

The tuner monitors for a `FINISH` marker and will automatically stop and analyze the data when the print ends.

### How to apply:
1.  Update your `config.ini` with the recommended `sgthrs` value.
2.  Run `scripts/gen_config.py` and rebuild/flash.
