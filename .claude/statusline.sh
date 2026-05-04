#!/bin/bash
# Claude Code status line — shows context window usage
input=$(cat)

PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | awk '{printf "%d", $1}')
USED=$(echo "$input" | jq -r '.context_window.current_usage.input_tokens // 0')
CACHE_R=$(echo "$input" | jq -r '.context_window.current_usage.cache_read_input_tokens // 0')
CACHE_W=$(echo "$input" | jq -r '.context_window.current_usage.cache_creation_input_tokens // 0')
TOTAL=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')

# Build bar (20 chars)
FILLED=$(( PCT * 20 / 100 ))
BAR=""
for i in $(seq 1 $FILLED); do BAR="${BAR}█"; done
for i in $(seq $((FILLED+1)) 20); do BAR="${BAR}░"; done

# Color: green <50%, yellow <80%, red >=80%
if [ "$PCT" -ge 80 ]; then
  COLOR="\033[31m"   # red
elif [ "$PCT" -ge 50 ]; then
  COLOR="\033[33m"   # yellow
else
  COLOR="\033[32m"   # green
fi
RESET="\033[0m"

TOTAL_K=$(( TOTAL / 1000 ))
printf "${COLOR}ctx [${BAR}] ${PCT}%%  ${USED} in / ${TOTAL_K}k max  cache r:${CACHE_R} w:${CACHE_W}${RESET}"
