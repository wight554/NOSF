#pragma once

// =====================================================================
// NOSF ERB V2.0 — Hardware/Board Configuration
// Parameters in this file are board-specific and usually fixed.
// All user-tunable parameters are in config.ini (via tune.h).
// =====================================================================

// User/Motor parameters generated from config.ini
#include "tune.h"

// ----- Sense resistor -----
// ERB V2.0 onboard Rsense (R46/R47, R48/R49) — hardware constant
#define CONF_RSENSE_OHM         0.110f

// ----- ISS Internal -----
#define CONF_ISS_SG_MA_LEN      5

// ----- Firmware version -----
#define CONF_FW_VERSION         "NOSF_0.2.0"

// ----- Hardware pins (Board specific - ERB V2.0) -----
#define PIN_L1_IN        2
#define PIN_L1_OUT       3
#define PIN_L2_IN        4
#define PIN_L2_OUT       5
#define PIN_Y_SPLIT      6

#define PIN_BUF_ADVANCE  18
#define PIN_BUF_TRAILING 12
#define PIN_BUF_ANALOG   26  // GP26 = ADC0; change to 27/28/29 if needed

#define PIN_M1_EN        8
#define PIN_M1_DIR       9
#define PIN_M1_STEP      10
#define PIN_M1_UART_TX   11
#define PIN_M1_UART_RX   13
#define PIN_M1_DIAG      13  // same as UART_RX on ERB

#define PIN_M2_EN        14
#define PIN_M2_DIR       15
#define PIN_M2_STEP      16
#define PIN_M2_UART_TX   17
#define PIN_M2_UART_RX   19
#define PIN_M2_DIAG      19  // same as UART_RX on ERB

#define PIN_SERVO        23
#define PIN_NEOPIXEL     21
