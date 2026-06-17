#!/bin/bash
# Claude Code Statusline — Color-coded context usage + model display
# Format: | [████░░░░] 25% | tokens: 50k/200k | model: DeepSeek-v4-pro[1m] |
input=$(cat)

model=$(echo "$input" | jq -r '.model.display_name // "unknown"')
used_pct=$(echo "$input" | jq -r '.context_window.used_percentage // "0"')
total_tokens=$(echo "$input" | jq -r '.context_window.context_window_size // 0')
input_tokens=$(echo "$input" | jq -r '.context_window.total_input_tokens // 0')

# Colors
PURPLE='\033[35m'
GREEN='\033[32m'
BLUE='\033[34m'
DIM='\033[2m'
GRAY='\033[90m'
RESET='\033[0m'

# 10-char progress bar
pct_int=${used_pct%.*}
[ -z "$pct_int" ] && pct_int=0
width=10
filled=$(( pct_int * width / 100 ))
[ "$filled" -gt "$width" ] && filled=$width
empty=$(( width - filled ))

bar=""
for ((i=0; i<filled; i++)); do bar+="█"; done
spaces=""
for ((i=0; i<empty; i++)); do spaces+="░"; done

# Token counts in k
used_k=$(( input_tokens / 1024 ))
total_k=$(( total_tokens / 1024 ))

# Build output
printf "${DIM}|${RESET} ${PURPLE}[${bar}${spaces}]${RESET} ${PURPLE}%s%%${RESET} " "$pct_int"
printf "${DIM}|${RESET} ${DIM}tokens:${RESET} ${GREEN}%sk/%sk${RESET} " "$used_k" "$total_k"
printf "${DIM}|${RESET} ${DIM}model:${RESET} ${BLUE}%s${RESET} ${DIM}|${RESET}" "$model"
printf "\n"
