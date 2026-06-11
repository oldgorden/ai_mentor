#!/bin/bash
trap '' TERM INT
cd /home/lk/ai_mentor
exec .venv/bin/python mentor/run_mentor.py </dev/null > /tmp/mentor.log 2>&1
