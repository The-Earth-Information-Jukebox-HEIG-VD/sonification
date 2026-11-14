#!/usr/bin/env python3
"""
Entry point of the Docker image:  python backend/serve.py

1. starts backend/prepare_data.py in the background (converts the team's raw data, if there is any, into
   the datasets the API serves; only what changed since the last start);
2. serves the API at once, so the container is healthy immediately. The datasets show up in
   GET /api/datasets as soon as each one is ready (GET /api/health has "preparing_data").

Environment: PORT (8000), JUKEBOX_PREPARE (1; set 0 to skip step 1), plus the variables of backend/app.py.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    os.chdir(ROOT)
    if os.environ.get("JUKEBOX_PREPARE", "1") not in ("0", "false", "no"):
        subprocess.Popen([sys.executable, str(ROOT / "backend" / "prepare_data.py")])
    import uvicorn
    sys.path.insert(0, str(ROOT))
    uvicorn.run("backend.app:app", host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
