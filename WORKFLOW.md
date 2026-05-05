
# NOSF – Git Workflow

This repo is firmware for real hardware. The goal of this workflow is simple:
- `main` is always buildable/flashable
- known-good restore points are easy to reach

---

## Branch policy

### `main`
- The primary development branch.
- Always **buildable**
- Always **flashable**

### Short-lived branches (Optional)
For larger experiments, use one of these patterns before merging back to `main`:
- `feature/<name>` – new functionality
- `fix/<name>` – bugfixes
- `hw/<name>` – hardware-specific changes (pins, display quirks, timing)

---

## Daily workflow

### 1) Sync and work on `main`
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
cmake -S firmware -B build_local -G Ninja -DPICO_SDK_PATH=/path/to/pico-sdk
ninja -C build_local
```

Flash:
```bash
bash scripts/flash_nosf.sh
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
