---
name: flash-ready
description: Pre-commit checklist for NOSF firmware — summarizes changed files, flags TUNE:/VERIFY: markers in the diff, confirms branch is not main, then prints the three flash steps
---

Check the current git branch (warn if on main). Run `git diff --stat HEAD` to summarize what changed in firmware/. Run `git diff HEAD -- firmware/` and flag any lines containing TUNE:, VERIFY:, or TODO added in this diff. Then print the three steps to ship:

1. git commit && git push
2. ~/flash_nosf.sh on Pi
3. python3 ~/nosf_test.py "CMD:" to verify
