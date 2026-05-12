# Cutter Config Parity

## Problem
The `ENABLE_CUTTER` flag is a runtime tunable (persisted in flash, modified via `SET:CUTTER:1`), but it is completely missing from `config.ini.example` and `gen_config.py`. This violates the `persistence-contract` spec and makes it difficult for users to enable the servo cutter on boot without relying on saved flash state.

## Solution
1. Add `enable_cutter: False` to `config.ini.example` under the Cutter section.
2. Update `scripts/gen_config.py` to parse `enable_cutter` and generate `#define CONF_ENABLE_CUTTER`.
3. Update `firmware/src/main.c` to consume `CONF_ENABLE_CUTTER` as the boot default for `ENABLE_CUTTER`.
