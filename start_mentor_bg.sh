#!/bin/bash
trap '' TERM INT
cd /home/lk/ai_mentor

ENV_FILE="$HOME/.config/ai_mentor/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

exec .venv/bin/python mentor/run_mentor.py </dev/null > /tmp/mentor.log 2>&1
