#!/bin/bash
# Cursor afterFileEdit hook: stamp new migration SQL files with a unique Migration-Id.
# Copy this .cursor folder into a database project workspace (not this tools repo)
# so Agent writes under Deployments/Migrations/ get stamped automatically.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INPUT="$(cat)"

FILE_PATH="$(printf '%s' "$INPUT" | python3 -c '
import json
import os
import sys

data = json.load(sys.stdin)
path = data.get("file_path", "")
if not path:
    print("")
elif os.path.isabs(path):
    print(path)
else:
    roots = data.get("workspace_roots") or []
    print(os.path.join(roots[0], path) if roots else os.path.join(sys.argv[1], path))
' "$ROOT")"

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

case "$FILE_PATH" in
  *Deployments/Migrations/*/*.sql)
    python3 -m sql_mig stamp --quiet "$FILE_PATH" || true
    ;;
esac

exit 0
