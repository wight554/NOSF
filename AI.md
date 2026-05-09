# NOSF — AI Assistance & MCP Setup

This project uses a **Global-First** AI configuration. Skills and MCP servers are configured at the user level to ensure consistency across `claude-code`, `gemini-cli`, `antigravity`, and IDE-based Copilot chats.

## Global Configuration Overview

All AI tools in this repo rely on a shared environment located in your home directory:

- **Primary Source**: `~/.gemini/extensions/caveman/`
- **Claude Integration**: `~/.claude/skills/` (linked to Gemini source)
- **MCP Servers**: Managed via global `node` and `npx`
- **Memory**: Persistent cross-session memory managed by `cavemem` MCP

## Prerequisites

- **Node.js**: v22+ (v22.20.0 recommended)
- **Python**: v3.12+ (v3.14.4 recommended)
- **Anthropic / Google API Keys**: Must be exported in your shell profile

## Initial Setup (One-Time Global)

### 1. Install Caveman Extension
Follow the instructions at [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) to install the core extension.

### 2. Link Claude Skills
To ensure `claude-code` can use the same specialized skills, create global symlinks:
```bash
ln -sfn ~/.gemini/extensions/caveman/skills/caveman ~/.claude/skills/caveman
ln -sfn ~/.gemini/extensions/caveman/skills/caveman-commit ~/.claude/skills/caveman-commit
ln -sfn ~/.gemini/extensions/caveman/skills/caveman-help ~/.claude/skills/caveman-help
ln -sfn ~/.gemini/extensions/caveman/skills/caveman-review ~/.claude/skills/caveman-review
ln -sfn ~/.gemini/extensions/caveman/skills/caveman-stats ~/.claude/skills/caveman-stats
ln -sfn ~/.gemini/extensions/caveman/skills/cavecrew ~/.claude/skills/cavecrew
ln -sfn ~/.gemini/extensions/caveman/skills/compress ~/.claude/skills/compress
ln -sfn ~/.gemini/extensions/caveman/skills/caveman-compress ~/.claude/skills/caveman-compress
```

### 3. Configure MCP Servers
Ensure `~/.claude/settings.json` and `~/.gemini/settings.json` include the `cavemem` MCP:

```json
{
  "mcpServers": {
    "cavemem": {
      "command": "node",
      "args": ["/path/to/your/global/node_modules/cavemem/dist/index.js", "mcp"]
    }
  }
}
```

## Active MCPs in this Repo

| MCP | Purpose | Source |
|---|---|---|
| `cavemem` | Persistent, compressed cross-agent memory | Global (via node) |
| `context7` | Documentation & library search | Global (HTTP/S) |
| `git` | Git integration (branching, commits) | Global (via npx) |

## Workspace Rules

- **Do NOT commit local config**: Files like `.agents/`, `.claude/`, or `skills-lock.json` must NOT be committed to the repo. The project relies on the global configuration described above.
- **Model Attribution**: Always include `Generated-By: <Agent> (<Model>)` in commit messages.
- **Workflow**: Follow the `TASK.md` protocol (Research -> Plan -> Implement) to manage context limits.

See `AGENTS.md` for specific firmware engineering mandates and the full session start protocol.
