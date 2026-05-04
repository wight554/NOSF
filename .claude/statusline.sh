#!/bin/bash
# Claude Code status line — context window + session rate limits + cost
input=$(cat)

# ── helpers ───────────────────────────────────────────────────────────────────
G="\033[32m" Y="\033[33m" R="\033[31m" DIM="\033[90m" X="\033[0m"

pct_color() {
  local p=$1
  if   [ "$p" -ge 80 ]; then printf "%s" "$R"
  elif [ "$p" -ge 50 ]; then printf "%s" "$Y"
  else                        printf "%s" "$G"
  fi
}

# ── context window ────────────────────────────────────────────────────────────
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | awk '{printf "%d",$1}')
USED=$(echo "$input" | jq -r '.context_window.current_usage.input_tokens // 0')
TOTAL=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')
CACHE_R=$(echo "$input" | jq -r '.context_window.current_usage.cache_read_input_tokens // 0')
CACHE_W=$(echo "$input" | jq -r '.context_window.current_usage.cache_creation_input_tokens // 0')
TOTAL_K=$(( TOTAL / 1000 ))

FILLED=$(( PCT * 20 / 100 ))
BAR=""
for i in $(seq 1 $FILLED);        do BAR="${BAR}█"; done
for i in $(seq $((FILLED+1)) 20); do BAR="${BAR}░"; done

CC=$(pct_color "$PCT")
OUT="${CC}ctx [${BAR}] ${PCT}%  ${USED}/${TOTAL_K}k  r:${CACHE_R} w:${CACHE_W}${X}"

# ── session rate limits (Pro/Max only) ────────────────────────────────────────
RL5_PCT=$(echo  "$input" | jq -r '.rate_limits.five_hour.used_percentage  // empty')
RL5_RST=$(echo  "$input" | jq -r '.rate_limits.five_hour.resets_at        // empty')
RL7_PCT=$(echo  "$input" | jq -r '.rate_limits.seven_day.used_percentage  // empty')
RL7_RST=$(echo  "$input" | jq -r '.rate_limits.seven_day.resets_at        // empty')

if [ -n "$RL5_PCT" ]; then
  RL5=$(printf "%.0f" "$RL5_PCT")
  RC=$(pct_color "$RL5")

  RESET_STR=""
  if [ -n "$RL5_RST" ]; then
    SECS=$(( RL5_RST - $(date +%s) ))
    if [ "$SECS" -gt 0 ]; then
      HH=$(( SECS / 3600 ))
      MM=$(( (SECS % 3600) / 60 ))
      RESET_STR=" (~${HH}h${MM}m)"
    else
      RESET_STR=" (now)"
    fi
  fi

  OUT="${OUT}  ${RC}5h:${RL5}%${RESET_STR}${X}"

  if [ -n "$RL7_PCT" ]; then
    RL7=$(printf "%.0f" "$RL7_PCT")
    RC7=$(pct_color "$RL7")
    RL7_RST_STR=""
    if [ -n "$RL7_RST" ]; then
      RL7_RST_STR=" ($(date -r "$RL7_RST" "+%Y-%m-%d %H:%M" 2>/dev/null || date -d "@$RL7_RST" "+%Y-%m-%d %H:%M" 2>/dev/null))"
    fi
    OUT="${OUT}  ${RC7}7d:${RL7}%${RL7_RST_STR}${X}"
  fi
fi

# ── session cost ──────────────────────────────────────────────────────────────
COST=$(echo "$input" | jq -r '.cost.total_cost_usd // empty')
if [ -n "$COST" ]; then
  COST_FMT=$(LC_NUMERIC=C awk -v c="$COST" 'BEGIN{printf "%.4f",c}')
  OUT="${OUT}  ${DIM}\$${COST_FMT}${X}"
fi

printf "%b" "$OUT"
