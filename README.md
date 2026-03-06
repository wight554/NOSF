
# NightOwl Standalone Controller

Standalone dual-lane filament controller for automatic filament switching.

This firmware runs on the **ERB v2.0 controller (RP2040 based)** and manages:

- Dual filament drive motors
- Automatic lane switching
- Buffer monitoring
- Filament motion detection
- Runout signalling
- OLED status display
- Rotary encoder user interface
- Manual feed / reverse control

The system allows a printer to **continue printing when a filament spool runs out** by automatically switching to a second filament lane.

---

# Hardware Platform

Controller board:

**ERB v2.0 (RP2040 based)**

The ERB v2.0 provides:

- RP2040 microcontroller
- motor driver interface
- digital sensor inputs
- I2C interface for display
- runout output signal
- GPIO expansion

The firmware is written specifically for this board.

---

# System Overview

The controller manages two filament lanes and feeds filament into a single output path.

Lane 1 ----\
            >---- Y splitter ----> Printer
Lane 2 ----/

Each lane contains:

Filament spool
     │
IN sensor
     │
Drive motor
     │
OUT sensor

The system also monitors:

Buffer LOW sensor  
Buffer HIGH sensor  
Y splitter sensor  
Filament motion sensor

---

# Main Features

### Automatic filament switching

When the active lane runs out:

1. The system detects empty filament input
2. Swap is armed
3. Controller waits until the **Y splitter becomes empty**
4. The second lane starts feeding

This prevents filament collisions inside the Y splitter.

---

### Buffer controlled feeding

Buffer LOW  → start feeding  
Buffer HIGH → stop feeding  

This keeps filament tension stable.

---

### Motion monitoring

If motors run but filament does not move:

- motion fault triggers
- runout output activates

A startup delay prevents false alarms during loading.

---

### Live feed speed adjustment

Feed speed can be adjusted directly from the home screen using the encoder.

---

### Manual control mode

Manual mode allows direct motor control.

Manual menu:

- Lane select (L1 / L2)
- Feed or reverse
- Run / Stop

While running:

Encoder rotate → change speed  
Back button → stop motor

---

# Display Interface

Example screen:

A:L1  L1:Y/Y L2:Y/-
Buf L:Y H:-  Y:Y
State:AUTO  Feed:5000
Mot:OK

Meaning:

A:L1      Active lane  
L1:Y/Y    Lane 1 IN and OUT sensors active  
L2:Y/-    Lane 2 IN active, OUT empty  
Buf L:Y   Buffer low triggered  
Buf H:-   Buffer high not triggered  
Y:Y       Filament present at Y splitter  

---

# Safety Logic

The controller stops feeding when:

- no filament detected on both lanes
- filament motion stops while motors run

Runout signal is provided on **GPIO18**.

---

# Pinout (ERB v2)

Lane sensors

Lane 1 IN   GPIO24  
Lane 1 OUT  GPIO25  

Lane 2 IN   GPIO22  
Lane 2 OUT  GPIO12  

Buffer

Buffer LOW   GPIO6  
Buffer HIGH  GPIO7  

Y splitter

Y sensor GPIO2

Motion sensor

Motion GPIO5

Runout output

Runout GPIO18

---

# Motor Drivers

Motor 1

EN   GPIO8  
DIR  GPIO9  
STEP GPIO10  

Motor 2

EN   GPIO14  
DIR  GPIO15  
STEP GPIO16  

---

# Display

OLED controller: **SH1106**

I2C Address: **0x3C**

SDA GPIO26  
SCL GPIO27  

---

# Encoder

A GPIO28  
B GPIO4  

Buttons

Back    GPIO3  
Confirm GPIO29  

---

# Build

Requirements:

- pico-sdk
- cmake
- ninja
- gcc-arm-none-eabi
- picotool

Build:

cd ~/dev/nightowl-standalone-controller
rm -rf build
mkdir build
cd build

export PICO_SDK_PATH=~/dev/pico-sdk

cmake -G Ninja ../firmware
ninja

---

# Flash

sudo ~/dev/picotool/build/picotool load build/nightowl_controller.elf -f
sudo ~/dev/picotool/build/picotool reboot

---

# Documentation

MANUAL.md  
HARDWARE.md  
BUILD_FLASH.md  
WORKFLOW.md  

---

# Development

Branches:

main      stable firmware  
dev       integration branch  
feature/* development branches  
fix/*     bug fixes  

Never develop directly on **main**.

---

# Safety

Always test firmware changes at low speed.

Verify sensor polarity before enabling automatic swap.
