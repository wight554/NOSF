
# NightOwl Standalone Controller

Standalone filament controller for dual-lane automatic filament switching.

This firmware runs on an **RP2040 (Raspberry Pi Pico)** based controller and manages:

- Dual filament drive motors
- Automatic lane switching
- Buffer control
- Filament motion monitoring
- Runout signalling
- OLED status display
- Rotary encoder UI
- Manual feed / reverse control

The system is designed to keep printing when a filament spool runs out by automatically switching to the second lane.

---

# Features

- Automatic filament lane switching
- Swap only when Y-splitter is empty
- Live feed speed adjustment
- Motion sensor based jam detection
- Runout output signal
- Manual control menu
- OLED status interface
- Encoder navigation

---

# Hardware

Main components:

- RP2040 controller
- 2x stepper drivers
- SH1106 OLED display (I2C)
- Rotary encoder with push buttons
- Filament sensors
- Motion sensor
- Buffer sensors
- Y splitter sensor

Full pinout available in:

**HARDWARE.md**

---

# Firmware Architecture

Core subsystems:

- Lane state machine
- Buffer latch logic
- Swap controller
- Motion monitoring
- Motor ramp controller
- UI menu system

Key behaviour:

1. Buffer LOW triggers feeding
2. Active lane feeds filament
3. If active lane becomes empty → swap is armed
4. Swap happens only when Y splitter becomes empty
5. Second lane continues feeding

This prevents filament collision inside the Y splitter.

---

# Display

The OLED shows:

- Active lane
- Lane sensor states
- Buffer status
- Y splitter state
- Feed speed
- Motion detection status

---

# Manual Mode

Manual mode allows testing motors.

Menu:

Manual →

Options:

- Lane select (L1 / L2)
- Feed or reverse
- Start / stop

While running:

Rotate encoder → change speed  
Back button → stop motor

---

# Motion Detection

The motion sensor monitors filament movement.

If no movement is detected while motors are running:

- Motion fault triggers
- Runout output activates

Startup delay prevents false alarms during loading.

---

# Build

Requirements:

- pico-sdk
- cmake
- ninja
- gcc-arm-none-eabi
- picotool

Build:

```
cd ~/dev/nightowl-standalone-controller
rm -rf build
mkdir build
cd build

export PICO_SDK_PATH=~/dev/pico-sdk

cmake -G Ninja ../firmware
ninja
```

---

# Flash

```
sudo ~/dev/picotool/build/picotool load build/nightowl_controller.elf -f
sudo ~/dev/picotool/build/picotool reboot
```

---

# Documentation

Additional documentation:

- **MANUAL.md** – operation manual
- **HARDWARE.md** – wiring and pinout
- **BUILD_FLASH.md** – build instructions
- **WORKFLOW.md** – development workflow

---

# Repository Structure

```
firmware/
    src/
        main.c

build/
docs/
```

---

# Safety

Always test firmware changes at low speed.

Verify sensor polarity before enabling automatic swap.

Never run motors if the filament path is blocked.

---

# License

Open hardware / open firmware project.
