
# NightOwl Standalone Controller – Git Workflow

This repo is firmware for real hardware. The goal of this workflow is simple:
- `main` is always buildable/flashable
- experiments never break the baseline
- known-good restore points are easy to reach

---

## Branch policy

### `main`
- Always **buildable**
- Always **flashable**
- Only merge into `main` when it is tested on hardware

### `dev`
- Integration branch
- Features/fixes land here first
- When `dev` is stable on hardware, merge to `main`

### Short-lived branches
Use one of these patterns:
- `feature/<name>` – new functionality
- `fix/<name>` – bugfixes
- `hw/<name>` – hardware-specific changes (pins, display quirks, timing)

Examples:
- `feature/manual-menu`
- `fix/swap-y-empty`
- `hw/sh1106-i2c-timing`

---

## Daily workflow

### 1) Start from `main`
```bash
git switch main
git pull
```

### 2) Create a branch

Feature:
```bash
git switch -c feature/<name>
```

Bugfix:
```bash
git switch -c fix/<name>
```

Hardware tweak:
```bash
git switch -c hw/<name>
```

### 3) Work + commit in small steps

```bash
git add firmware/src/main.c firmware/CMakeLists.txt
git commit -m "Short, specific message"
```

Examples of good commit messages:
- `Fix swap: keep feeding until Y empty`
- `Manual: allow live speed adjust while running`
- `Runout: add 12s cooldown setting`

---

## Build / Flash quick reference

Clean build:
```bash
cd ~/dev/nightowl-standalone-controller
rm -rf build
mkdir build && cd build
export PICO_SDK_PATH=~/dev/pico-sdk
cmake -G Ninja ../firmware
ninja
```

Flash:
```bash
sudo ~/dev/picotool/build/picotool load ~/dev/nightowl-standalone-controller/build/nightowl_controller.elf -f
sudo ~/dev/picotool/build/picotool reboot
```

---

## Merge flow

### Merge feature/fix into `dev` when it works
```bash
git switch dev || git switch -c dev
git merge --no-ff feature/<name>
git push
```

### Merge `dev` into `main` only when tested on hardware
```bash
git switch main
git merge --no-ff dev
git push
```

(Optional) delete the finished branch:
```bash
git branch -d feature/<name>
```

---

## Tags (restore points)

Use tags for firmware states that are verified on real hardware.

Create a baseline tag:
```bash
git tag -a baseline_$(date +%Y%m%d)_v1 -m "Tested on ERB v2: stable firmware baseline"
git push --tags
```

List tags:
```bash
git tag
```

Restore a baseline:
```bash
git fetch --tags
git checkout baseline_swap_yempty_v1
```

Continue development from that point:
```bash
git switch -c rescue/from-baseline
```

---

## Hard rule

**Never hack directly on `main`.**

If it’s an experiment, create a branch first.

---

## Quick diagnostics

Current branch:
```bash
git branch --show-current
```

Recent commits:
```bash
git log -5 --oneline --decorate
```

Working tree status:
```bash
git status
```
