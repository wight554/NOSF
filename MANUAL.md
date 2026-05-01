
# NightOwl Standalone Controller – Manual

This document explains how to operate the standalone NightOwl controller.

The controller manages:
- two filament lanes
- automatic lane switching
- buffer control
- motion monitoring
- manual feed/reverse

The firmware is designed to keep printing even when a filament spool runs out.

---

# Home Screen

The home screen shows:

- Active lane
- Lane sensor states
- Buffer state
- Y splitter state
- Feed speed
- Motion status

Example:

A:L1  L1:Y/Y L2:Y/-
Buf L:Y H:-  Y:Y
State:AUTO  Feed:5000
Mot:OK

Meaning:

L1:Y/Y  
Lane 1 has filament at input and output.

L2:Y/-  
Lane 2 has filament at input but not yet at output.

Buf L:Y  
Buffer low triggered.

Buf H:-  
Buffer high not triggered.

Y:Y  
Filament present at Y splitter.

---

# Automatic Mode

Automatic mode runs continuously when filament is available.

Behaviour:

1. When the buffer becomes LOW the system starts feeding.
2. The active lane feeds filament.
3. If the active lane becomes empty the controller prepares a swap.
4. The system waits until the Y splitter becomes empty.
5. Then the other lane starts feeding.

This prevents jams and filament collisions inside the Y.

---

# Lane Swap Logic

Swap happens when:

- active lane input sensor becomes empty
- the other lane still has filament
- Y splitter is empty

This guarantees a clean transition.

---

# Manual Mode

Manual mode allows testing motors.

Menu:

Manual →

Options:

Lane  
Select L1 or L2

Action  
Feed or Reverse

Run/Stop  
Start motor

While running:

Rotate encoder → change speed

Back button → stop motor

---

# Error Conditions

The controller stops feeding when:

- no filament detected on both lanes
- motion sensor detects no movement
- hardware error occurs

Runout signal is triggered via GPIO18.

---

# Motion Detection

The motion sensor monitors filament movement.

If no movement is detected while motors are running:

- motion fault triggers
- runout output activates

Startup delay prevents false alarms during loading.

---

# Runout Cooldown

Runout events have a cooldown period.

Default:
12 seconds

This prevents oscillating runout signals.

Adjustable in Settings.

---

# Settings Menu

Settings allow adjusting:

Feed speed  
Reverse speed  
Auto speed  
Motion timeout  
Motion startup delay  
Motion fault enable/disable  
Runout cooldown

All values are applied immediately.

---

# USB Runtime Toggles

The controller supports runtime toggles over USB serial (`SET:/GET:`):

- `SM` (`0/1`) - sync mode enable
- `BI` (`0/1`) - buffer sensor invert
- `AUTO_PRELOAD` (`0/1`) - auto-start preload on IN sensor rising edge

Related runtime boolean state:

- `TS:<0|1>` - host-reported toolhead filament presence
- `AP:<0|1>` - appears in `?:` status and shows AUTO_PRELOAD state

Examples:

```bash
python3 scripts/nightowl_test.py "SET:AUTO_PRELOAD:1" "GET:AUTO_PRELOAD"
python3 scripts/nightowl_test.py "SET:SM:1" "GET:SM"
python3 scripts/nightowl_test.py "SET:BI:0" "GET:BI"
```

---

# Encoder Controls

Rotate:
change values / navigate menus

Confirm short:
enter menu

Confirm long:
secondary action (context dependent)

Back:
exit / stop manual movement

---

# Safety Notes

Never run the motors without filament path clear.

Always test swaps at low speed when modifying firmware.

Verify sensor logic before enabling automatic swap.

