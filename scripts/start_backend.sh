#!/usr/bin/env bash
set -e

# Activate Python libs
export PATH="/home/runner/workspace/.pythonlibs/bin:$PATH"

exec uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
