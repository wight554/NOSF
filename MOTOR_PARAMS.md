# Motor Parameter Reference

This file records known motor and TMC parameter sets that have been verified or
used successfully with NOSF.

Use these values as a starting point for `config.ini`, then tune from hardware
behavior if needed.

## Verified Profiles

### FYSETC G36HSY4405-6D-1200

Status: stock NightOwl kit motor / known working baseline

`config.ini` snippet:

```ini
gear_ratio: 50:10
run_current: 0.800
hold_current: 0.800
full_steps_per_rotation: 200
driver_tbl: 1
driver_toff: 3
driver_hstrt: 7
driver_hend: 10
```

Reference values:

| Key | Value |
|-----|-------|
| `gear_ratio` | `50:10` |
| `run_current` | `0.800` |
| `hold_current` | `0.800` |
| `full_steps_per_rotation` | `200` |
| `driver_tbl` | `1` |
| `driver_toff` | `3` |
| `driver_hstrt` | `7` |
| `driver_hend` | `10` |
