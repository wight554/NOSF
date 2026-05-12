# Manual Cutter Trigger

## Problem
The cutter sequence is currently only fully supported as an automated step inside the `TC:` (toolchange) orchestration. While the serial command `CU:` exists in firmware to start the cutter on the active lane, it immediately returns `OK` before the cutter physical sequence finishes. If a host tool (Klipper) calls `CU:`, it will not block and wait for completion, leading to race conditions where Klipper pulls the filament before the cut finishes.

## Solution
1. **Firmware (`toolchange.c`)**: Make the cutter state machine emit `EV:CUT:DONE` when the cut successfully completes and parks the servo.
2. **Firmware (`toolchange.c`)**: Make `cutter_abort()` emit `EV:CUT:ERROR`.
3. **Host Tool (`nosf_cmd.py`)**: Add `CU` to the `COMPLETION_EVENTS` dictionary so that `nosf_cmd.py CU:` will block and wait for `EV:CUT:DONE` or `EV:CUT:ERROR` instead of exiting immediately upon receiving the `OK` acknowledgment.
