#!/usr/bin/env bash
# R1.7: if staged changes touch prompt/model/classifier files, the latest AI eval must PASS on exactly these files.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)/backend"
files=$(python -c "from app.eval import gate; print('\n'.join(str(f.relative_to(gate.ROOT.parent)) for f in gate.FILES))")
staged=$(git diff --cached --name-only | sed 's#^backend/##')
echo "$staged" | grep -qxF -f <(echo "$files") || exit 0
python - <<'PY'
import asyncio, os, sys
from dotenv import load_dotenv; load_dotenv(".env")
from motor.motor_asyncio import AsyncIOMotorClient
from app.eval import gate
st = asyncio.run(gate.status(AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]))
if not st["ok_to_release"]:
    print(f"Commit blocked: prompts/models changed (hash {st['prompt_hash'][:12]}). Run scripts/ai_eval.sh until the gate PASSES.")
    sys.exit(1)
PY
