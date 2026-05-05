# StallGuard & ISS Tuning Guide

This document provides a deep dive into tuning the **Infinite Spool System (ISS)** using StallGuard on the TMC2209. It is based on real-world calibration of the **FYSETC G36HSY4405-6D-1200** motor at **800mA**.

---

## The Core Concept: Interpolated Follow

Unlike a standard MMU, the ISS must match the speed of the printer's extruder. We use StallGuard as a "pressure sensor" to achieve this.

1.  **Free-Air Baseline**: When the motor is spinning with no resistance, StallGuard reports a high value (the `SG_RESULT`).
2.  **Contact/Tension**: As the extruder pulls the filament, the `SG_RESULT` drops.
3.  **Speed Adjustment**: The firmware calculates the ratio: `sg_frac = SG_RESULT / ISS_SG_TARGET`.
    *   If `sg_frac >= 1.0`: Motor runs at full speed (`ISS_PRESS_RATE`) to catch up.
    *   If `sg_frac < 1.0`: Motor slows down proportionally to match the tension.

---

## Critical Parameters

### 1. `ISS_SG_TARGET` (The "Zero-Force" Reference)
This is the most important value. It defines the point where the motor *begins* to slow down.
*   **Too Low**: You have to pull very hard before it slows down. The motor feels "stiff."
*   **Too High**: The motor slows down even when there is no load. It feels "weak."
*   **Tuning Rule**: Set this to **~10% below your Free-Air SG reading**.
    *   Example: If `EV:BS` shows a ratio of `1.5` at idle with a target of `200`, your actual SG is `300`. Set `ISS_SG_TARGET` to `270`.

### 2. `ISS_TRAILING_RATE` (The "Quiet Stop" Speed)
When the buffer arm is fully deflected (trailing), the motor slows to this speed.
*   **High (100+)**: Motor may grind or "beep" loudly when it hits the physical limit.
*   **Low (30–50)**: Motor crawls silently and gently when stalled. This is much more pleasant.
*   **Unit**: mm/min.

### 3. `ISS_CURRENT_MA` (Sensitivity vs. Torque)
*   Higher current (800mA+) makes the motor stronger but can make the StallGuard signal "noisier."
*   We recommend **800mA** for a good balance of force and sensing reliability.

### 4. `ISS_SG_DERIV` (The "Soft Contact" Sensor)
This watches the *rate of change* of the StallGuard signal.
*   When the new filament tip hits the old tail during a fast approach (`ISS_JOIN_RATE`), the load jumps instantly.
*   `ISS_SG_DERIV` catches this jump *before* the motor grinds or stalls.
*   **Tuning Rule**: Use `scripts/tune_iss_sg.py` to calibrate this. A typical value is **3 to 10**.

### 5. `SGT_L1 / SGT_L2` (The Hard Safety Net)
This sets the sensitivity of the `DIAG` pin (the hard-stop).
*   **Tuning Rule**: We found **50** to be a robust "Golden State" for NEMA 14 motors. It prevents false-positives while still catching real jams.

---

## Debugging with `EV:BS`

Use a serial monitor to watch the `BS` (Buffer Status) events during an ISS follow:
`EV:BS:MID,1275.0,1.10`

*   **`MID`**: Buffer zone.
*   **`1275.0`**: Current speed in mm/min.
*   **`1.10`**: The **StallGuard Ratio**. 
    *   **Goal**: This should be **1.05 to 1.15** when running in free air.
    *   **Reaction**: It should drop smoothly to **0.50** when medium tension is applied.

---

## Troubleshooting

### "My motor is beeping at stall"
This happens when the motor is forced to a halt but the firmware is still trying to drive it at a speed higher than its "slip" frequency. 
*   **Fix**: Lower `ISS_TRAILING_RATE` to **42** (equivalent to ~500 SPS) or lower.

### "The motor doesn't slow down until I pull really hard"
*   **Fix**: Your `ISS_SG_TARGET` is too low. Increase it until the idle `EV:BS` ratio is around **1.1**.

### "The motor slows down for no reason"
*   **Fix**: Your `ISS_SG_TARGET` is too high (ratio is below 1.0 at idle). Decrease it.

---

## The "Golden State" Reference
For **FYSETC G36HSY4405-6D-1200** at **800mA**:
```bash
python3 scripts/nosf_cmd.py "SET:ISS_SG_TARGET:320"
python3 scripts/nosf_cmd.py "SET:ISS_TRAILING_RATE:42"
python3 scripts/nosf_cmd.py "SET:ISS_CURRENT_MA:800"
python3 scripts/nosf_cmd.py "SET:SGT_L1:50"
python3 scripts/nosf_cmd.py "SET:SGT_L2:50"
python3 scripts/nosf_cmd.py "SET:ISS_SG_DERIV:3"

# Safety timeout (10s)
python3 scripts/nosf_cmd.py "SET:ISS_FOLLOW_MS:10000"
python3 scripts/nosf_cmd.py "SV:"
```
