#!/bin/bash
trap '' TERM INT
cd /home/lk/ai_mentor

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

exec .venv/bin/python mentor/run_mentor.py </dev/null > /tmp/mentor.log 2>&1
