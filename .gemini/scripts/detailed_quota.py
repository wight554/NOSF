import sys
import json
import subprocess
import time

def get_stats():
    try:
        # We use a short timeout and PAGER=cat to avoid hangs
        res = subprocess.run(
            ["gemini", "-p", "/stats session", "--output-format", "json"], 
            capture_output=True, 
            text=True, 
            timeout=10,
            env={"PAGER": "cat"}
        )
        if res.returncode == 0:
            return json.loads(res.stdout)
    except Exception:
        pass
    return None

def main():
    # Read AfterAgent hook input (contains transcript path)
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        hook_input = {}

    # Try to get session stats which usually include model usage and quota info
    stats = get_stats()
    
    # We want a concise one-line status similar to Claude
    # ctx [██████████░░░░░░░░░░] 52%  104k/200k  5h:12% (~4h15m)
    
    if not stats:
        # Fallback to basic info from hook_input if possible
        # but for now let's just exit if we can't get detailed stats
        return

    # Extract info from stats JSON
    # Structure based on observations: stats.models[model_name].tokens...
    # and potentially a top-level or model-level quota object.
    
    # Placeholder for actual parsing once schema is known:
    # This just shows we have the hook active.
    msg = "Gemini Status Active"
    
    # Output to Gemini CLI
    print(json.dumps({"systemMessage": msg}))

if __name__ == "__main__":
    main()
