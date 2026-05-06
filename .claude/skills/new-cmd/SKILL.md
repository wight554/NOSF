---
name: new-cmd
description: Scaffold a new USB serial command handler in protocol.c following the NOSF CMD:PAYLOAD protocol pattern
---

The user provides a command name (e.g. "XY") and a description of what it should do.

Add a new `else if (!strcmp(cmd, "XY"))` branch to `cmd_execute()` in `firmware/src/protocol.c`, following the existing pattern: parse the payload from `p`, validate args, do the work through the appropriate module API, then call `cmd_reply("OK", ...)` or `cmd_reply("ER", "REASON")`.

Then add the command to `MANUAL.md` under the appropriate section with format:
```
CMD:PAYLOAD → OK:DATA or ER:REASON
  Description of what it does.
```
