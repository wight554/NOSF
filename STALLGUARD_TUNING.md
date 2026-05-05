# NOSF — StallGuard Auto-Tuning Guide

This guide covers the automated workflow for calibrating StallGuard thresholds (`SGT`) using the ML-backed tuning suite. This workflow correlates real-time motor load with slicer geometry and volumetric flow.

---

## Tool Overview

| Script | Purpose |
|--------|---------|
| `scripts/gcode_marker.py` | Injects flow-aware sync markers into G-code files. |
| `scripts/sg_tuner.py` | Live-sweeps `SGT`, records data, and fits a physics-based model. |
| `scripts/motors.ini` | Database of motor constants used for normalization and $K_e$ calculation. |

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

This creates `my_model_geo.gcode` containing markers like:
`M118 NOSF_TUNE:Inner wall:V249.5:W0.50:H0.25 (Q:10.00)`

---

## Step 3: Run the Live Tuner

1.  **Start the print** (`my_model_geo.gcode`) in Klipper.
2.  **Start the tuner script** on your PC/Pi:

```bash
python3 scripts/sg_tuner.py \
  --lane 1 \
  --klipper-log /tmp/printer \
  --motor fysetc-g36hsy4405-6d-1200
```

### What happens during the run:
*   **Sync**: The script tails the Klipper log and matches StallGuard readings to the current feature (Infill, Wall, etc.).
*   **Sweep**: Every 2 seconds, the script adjusts `SGT` by ±1 to explore the sensitivity of your motor at that specific flow rate.
*   **Recording**: Data is saved to a CSV file (e.g., `sg_tuner_data_20260505.csv`).

---

## Step 4: Analysis & Recommendation

Once the print (or a representative section) is finished, stop the script with `Ctrl+C`. It will automatically perform a non-linear regression analysis.

### Recommendation Output Example:
```
--- Analysis Results (Flow-Aware) ---
Noise Floor (σ): 12.42 SG units
Features mapped: Inner wall, Outer wall, Solid infill

Recommended SGT values (Sensitivity Target: 200-400):
 Speed (mm/min) | Flow (mm3/s) |   Rec SGT
---------------------------------------------
            500 |          2.0 |         18
           1000 |          4.0 |         14
           2000 |          8.0 |          8
           3000 |         12.0 |          2
```

### How to apply:
1.  Look at the `Rec SGT` for your typical printing speeds.
2.  Update your `config.ini` with the new `sgt` value.
3.  Run `scripts/gen_config.py` and reflash/rebuild.

---

## Motor Normalization (Optional)

If you are using a new motor, add it to `scripts/motors.ini`. The tuner uses `holding_torque` and `max_current` to calculate the Back-EMF constant ($K_e$), which allows it to predict StallGuard behavior even at speeds you haven't tested yet.

```ini
[my-new-motor]
resistance: 2.0
inductance: 0.003
holding_torque: 0.45
max_current: 1.5
steps_per_revolution: 200
```
