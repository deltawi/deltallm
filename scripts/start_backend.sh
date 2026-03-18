#!/usr/bin/env bash
set -e

# Activate Python libs
export PATH="/home/runner/workspace/.pythonlibs/bin:$PATH"

# Ensure Prisma query engine binary is available
if [ ! -f "/home/runner/workspace/prisma-query-engine-debian-openssl-3.0.x" ]; then
  echo "Fetching Prisma binary..."
  prisma py fetch
  BINARY=$(find /home/runner/.cache/prisma-python/binaries -name "query-engine-debian-openssl-3.0.x" 2>/dev/null | head -1)
  if [ -n "$BINARY" ]; then
    cp "$BINARY" "/home/runner/workspace/prisma-query-engine-debian-openssl-3.0.x"
    chmod +x "/home/runner/workspace/prisma-query-engine-debian-openssl-3.0.x"
  fi
fi

exec uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
