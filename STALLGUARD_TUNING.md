# NOSF — StallGuard Auto-Tuning Guide

This guide covers the automated workflow for calibrating StallGuard thresholds (`SGT`) using the ML-backed tuning suite. This workflow correlates real-time motor load with slicer geometry and volumetric flow.

---

## Tool Overview

| Script | Purpose |
|--------|---------|
| `scripts/gcode_marker.py` | Injects flow-aware sync markers into G-code files. |
| `scripts/sg_tuner.py` | Live-sweeps `SGT`, records data, and fits a physics-based model. |
| `scripts/motors.ini` | Database of motor constants and tuning baselines. |

---

## Step 1: Klipper Setup

To allow the tuner to "see" what the printer is doing, you must enable the `M118` (Respond) command in Klipper.

Add to your `printer.cfg`:

```ini
[respond]
# Enables the M118 command used for sync markers
```

---

## Step 2: Prepare the G-code

Standard linear speed is not enough for accurate tuning because back-pressure depends on line width and height. Use the marker script to calculate the effective filament speed.

```bash
# Process your sliced G-code
python3 scripts/gcode_marker.py my_model.gcode
```

---

## Step 3: Run the Live Tuner

The tuner uses an **Automated Sync Workflow**. You don't need to manually enable sync on the MMU.

1.  **Load Filament**: Run the `FL` command on the MMU. It will push filament until it hits the extruder gears and stops automatically.
2.  **Start Print**: Start the marked G-code in Klipper.
3.  **Start Tuner**: Run the tuner script on your PC/Pi.

```bash
# Example: Fine-tuning a Fysetc kit motor
python3 scripts/sg_tuner.py \
  --baseline fysetc-g36hsy4405-6d-1200 \
  --fine-tune \
  --klipper-log /tmp/printer
```

### How Synchronization Happens:
*   **Pull-to-Sync**: When Klipper starts the print, the extruder pulls the filament.
*   **Auto-Trigger**: The MMU detects this pull (Buffer `ADVANCE`) and immediately starts syncing.
*   **Proactive Setup**: The `sg_tuner.py` script automatically configures the controller's TMC settings and `SYNC_SG_INTERP` mode for the duration of the run.

---

## Step 4: Analysis & Recommendation

The tuner script monitors for a `FINISH` marker in the G-code and will automatically stop and analyze the data when the print ends.

### Recommendation Output Example:
```
--- Analysis Results (Flow-Aware) ---
Noise Floor (σ): 12.42 SG units
Recommended SGT values:
 Speed (mm/min) | Flow (mm3/s) |   Rec SGT
---------------------------------------------
            500 |          2.0 |         18
           1000 |          4.0 |         14
           2000 |          8.0 |          8
```

### How to apply:
1.  Update your `config.ini` with the new `sgt` value.
2.  Run `scripts/gen_config.py` and rebuild/flash.
