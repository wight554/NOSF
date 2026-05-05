# Hardware Reference — FYSETC Enraged Rabbit Burrow (ERB) V2.0

MCU: Raspberry Pi RP2040 (dual-core ARM Cortex-M0+, 133 MHz, 264 KB SRAM)
Board: FYSETC ERB V2.0
Schematic: ERB V2.0 Release SCH.pdf (in FYSETC-ERB/V2.0/hardware/)

---

## Tested Hardware Configuration
*   **Motors**: [FYSETC G36HSY4405-6D-1200](https://github.com/FYSETC/FYSETC-MOTORS/blob/main/G36HSY4405-6D-1200/G36HSY4405-6D-1200.pdf). This motor is included in Fysetc NightOwl kits and has been verified to work well with StallGuard at 800mA.
*   **Buffer**: [QuattroSync](https://github.com/Batalhoti/QuattroSync). A dual-lane buffer that utilizes springs for tension management, offering more consistent performance for ISS than gravity-based designs like the TurtleNeck.

---

## GPIO Map (verified from schematic)

| GPIO | Net label      | Function                        |
|------|----------------|---------------------------------|
| 0    | CAN_RX         | CAN bus receive                 |
| 1    | CAN_TX         | CAN bus transmit                |
| 2    | SPI0_SCK       | SPI0 clock (also I2C1 SDA alt)  |
| 3    | SPI0_MISO      | SPI0 data in                    |
| 4    | SPI0_MOSI      | SPI0 data out                   |
| 5    | SPI0_CS        | SPI0 chip select                |
| 6    | I2C1_SDA       | I2C1 data                       |
| 7    | I2C1_SCL       | I2C1 clock                      |
| 8    | X-EN           | Motor 1 enable (active LOW)     |
| 9    | X-DIR          | Motor 1 direction               |
| 10   | X-STEP         | Motor 1 step (PWM)              |
| 11   | X-UART         | Motor 1 PDN_UART (single-wire)  |
| 12   | PRE_GATE_0     | Filament pre-gate sensor (gate 0), used by Klipper MMU |
| 13   | X-DIAG         | Motor 1 TMC2209 DIAG output     |
| 14   | Y-EN           | Motor 2 enable (active LOW)     |
| 15   | Y-DIR          | Motor 2 direction               |
| 16   | Y-STEP         | Motor 2 step (PWM)              |
| 17   | Y-UART         | Motor 2 PDN_UART (single-wire)  |
| 18   | —              | No named net (unused by ERB design); RP2040 GPIO accessible on header, adjacent to GPIO12 |
| 19   | Y-DIAG         | Motor 2 TMC2209 DIAG output     |
| 20   | —              | Status LED via R24 1 kΩ → 3.3 V |
| 21   | NEO_PIXEL      | WS2812 NeoPixel data            |
| 22   | ENCODER        | Encoder signal                  |
| 23   | SERVO          | Servo PWM (cutter)              |
| 24   | SELECTOR_STOP  | Selector stop endstop           |
| 25   | HALL_SENSOR    | Hall sensor input               |
| 26   | GPIO_26_ADC0   | ADC channel 0                   |
| 27   | GPIO_27_ADC1   | ADC channel 1                   |
| 28   | ADC2           | ADC channel 2                   |
| 29   | ADC3           | ADC channel 3                   |

---

## Motor Drivers — TMC2209 (U7 = Motor 1 / X, U8 = Motor 2 / Y)

### U7 TMC2209 — Motor 1 (X)

| TMC pin | Pin # | Connected to        | Note                                    |
|---------|-------|---------------------|-----------------------------------------|
| ENN     | 2     | GPIO8 (X-EN)        | Active LOW enable                       |
| DIR     | 19    | GPIO9 (X-DIR)       |                                         |
| STEP    | 16    | GPIO10 (X-STEP)     |                                         |
| MS1_AD0 | 9     | X-MS1               | Sets UART address bit 0                 |
| MS2_AD1 | 10    | X-MS2               | Sets UART address bit 1                 |
| DIAG    | 11    | GPIO13 (X-DIAG)     | Push-pull, LOW=normal, HIGH=stall       |
| INDEX   | 12    | X-INDEX             |                                         |
| SPREAD  | 7     | X-SPREAD            |                                         |
| VREF    | 17    | X-VREF              |                                         |
| PDN_UART| 14    | GPIO11 (X-UART)     | Single-wire UART, direct trace, no resistors |
| STDBY   | 20    | NC                  | No-connect marker on schematic          |
| CLK     | —     | GND                 | Internal 12 MHz oscillator used         |
| BRA     | 23    | R46 (0.11 Ω → GND) | Current sense A                         |
| BRB     | 27    | R47 (0.11 Ω → GND) | Current sense B                         |
| VCC_IO  | 15    | 3.3 V               |                                         |
| OUTB2/B1/A1/A2 | 1/26/24/21 | J3 connector | Motor coil outputs              |
| R44     | —     | NC (unpopulated)    | Optional pull-down on PDN_UART, not installed |

### U8 TMC2209 — Motor 2 (Y)

Same topology as U7. Net names are Y-* instead of X-*.

| Signal       | GPIO  |
|--------------|-------|
| Y-EN         | 14    |
| Y-DIR        | 15    |
| Y-STEP       | 16    |
| Y-UART (PDN) | 17    |
| Y-DIAG       | 19    |

Sense resistors: R48, R49 (0.11 Ω each).
Optional 0 Ω jumpers R38, R39 near DIR/STEP (unpopulated = NC).

### TMC2209 UART Address

Address is set by MS1/MS2 pin state at reset. Default (both LOW) = address 0.
Firmware uses addr=0 for both motors. MS1/MS2 are not driven by RP2040 GPIOs
in the current schematic — they float or are hardwired; behavior is addr=0.

---

## UART Topology — Critical Hardware Notes

**Single-wire, direct connection. No isolation resistors, no level shifters,
no buffers, no external pull-ups between RP2040 GPIO and TMC PDN_UART.**

```
RP2040 GPIO11 ──────────────── TMC U7 PDN_UART (pin 14)
RP2040 GPIO17 ──────────────── TMC U8 PDN_UART (pin 14)
```

**GPIO13 (X-DIAG) and GPIO11 (X-UART) are SEPARATE PINS on both the RP2040
and the TMC.** The ERB V2.0 pinout diagram labels gpio13 as "UART" which is
misleading — it is wired to TMC DIAG only, not to PDN_UART.

**Consequence:** There is no loopback path for two-wire UART (TX→RX via separate
GPIO). Only single-wire bit-bang (switch GPIO11 between OUTPUT and INPUT) can
work for reads.

### PDN_UART electrical behaviour

| pdn_disable bit | GPIO idle state    | Cause                              |
|-----------------|--------------------|------------------------------------|
| 0 (TMC default) | LOW                | TMC internal PDN pull-down (~few kΩ) overcomes RP2040 50 kΩ pull-up |
| 1 (UART mode)   | HIGH               | PDN pull-down deactivated; TMC UART pull-up (~22 kΩ) + RP2040 pull-up hold HIGH |
| Standby mode    | HIGH               | PDN pull-down also deactivated in standby — **indistinguishable from pdn_disable=1 by voltage alone** |

**TMC standby is triggered by holding PDN_UART HIGH for ≥ tST ≈ 1 ms**
(when pdn_disable=0). Previously tmc_init() drove tx_pin HIGH for 10 ms,
reliably triggering standby. In standby the line reads HIGH (like UART mode)
but UART reads return no response. Fixed: preconditioning reduced to 100 µs.

**TX writes work despite PDN pull-down** because line_drive_high() (OUTPUT HIGH)
overcomes the TMC pull-down for '1' bits and stop bits. Before this fix, using
line_idle() (INPUT/PULL_UP) for '1' bits caused the TMC pull-down to corrupt
every '1' bit — all writes were silently corrupted.

### DIAG pin behaviour

| State           | GPIO13 / GPIO19 level |
|-----------------|-----------------------|
| Normal (no stall)| LOW (push-pull output) |
| Stall detected  | HIGH (push-pull output) |
| Motor disabled  | LOW                    |

DIAG is a push-pull output. In normal operation it permanently holds the GPIO
LOW. This is why earlier attempts to receive UART on GPIO13 always returned
all-zero bytes — DIAG was holding the line LOW throughout, not PDN_UART.

---

## Power Architecture

| Rail | Source chip     | Input  | Output |
|------|-----------------|--------|--------|
| 5 V  | RY9330BP8 (buck)| 24 V   | 5 V / 3 A max |
| 3.3 V| ME6217C33M5G (LDO)| 5 V | 3.3 V |
| 24 V | External supply | —      | Stepper motor power |

- JP2 (solder jumper): bridges USB 5 V to board 5 V rail for firmware upload
  without 24 V. **Do not close JP2 while 24 V is connected.**
- Stepper VCC_IO (3.3 V) decoupled with C43 (4.7 µF/6.3 V) + C44 (100 nF).
- Motor power decoupled with C29 (100 µF/35 V), C38/C39 (100 nF), C36 (100 nF).

---

## Connectors and Jumpers

| Ref | Function |
|-----|----------|
| J3  | Motor 1 coil output (4-pin: OUTB2, OUTB1, OUTA1, OUTA2) |
| J5  | Motor 2 coil output (4-pin: same order) |
| J8  | USB header (USB_D_N, USB_D_P) |
| JP1 | CAN 120 Ω termination resistor (R27). Close to enable. |
| JP2 | USB 5 V → board 5 V enable. Close only when no 24 V present. |
| JP3/JP4 | 3-position jumpers: State 1 = USB on green 4-pin connector; State 2 = CAN on green 4-pin connector |
| Header 5 | SWD debug (RUN, SWCLK, SWD) |

---

## CAN Bus

- Controller: RP2040 GPIO0 (RX) / GPIO1 (TX)
- Transceiver: on-board (net CANH, CANL)
- Termination: R27 (120 Ω), enabled by closing JP1
- R52 (10 kΩ): pull resistor on CAN

---

## NeoPixel

- GPIO21, single WS2812B
- Pull-up R18/R19 (NC/10 K) on signal line

---

## Firmware Pin Assignments (as used in main.c)

```c
// Lane sensors
PIN_L1_IN   = 2    PIN_L1_OUT  = 3
PIN_L2_IN   = 4    PIN_L2_OUT  = 5
PIN_Y_SPLIT = 6

// Buffer (adjacent pins on header)
PIN_BUF_ADVANCE  = 18   // GPIO18: no named ERB net, accessible on header (row above GPIO12)
PIN_BUF_TRAILING = 12   // GPIO12: PRE_GATE_0 connector

// Motor 1
PIN_M1_EN   = 8    PIN_M1_DIR  = 9    PIN_M1_STEP = 10
PIN_M1_UART_TX = 11
PIN_M1_DIAG    = 13   // same GPIO as PIN_M1_UART_RX (DIAG only, not UART RX)

// Motor 2
PIN_M2_EN   = 14   PIN_M2_DIR  = 15   PIN_M2_STEP = 16
PIN_M2_UART_TX = 17
PIN_M2_DIAG    = 19   // same GPIO as PIN_M2_UART_RX (DIAG only, not UART RX)

// Peripherals
PIN_SERVO    = 23
PIN_NEOPIXEL = 21
```

Note: GPIO12 is confirmed connected (PRE_GATE_0 in the Klipper MMU config, filament
pre-gate sensor). The schematic page for GPIO12 may not show the net label clearly but
the Klipper mmu.cfg and board layout confirm it is routed to a sensor header.
