"""The repo runs pytest with --import-mode=importlib (pyproject.toml), which doesn't put test folders on
sys.path. These tests import simpod.py (next to them), arm/, firmware/harness/ and firmware/healer/."""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent, HERE.parents[1] / "firmware" / "harness", HERE.parents[1] / "firmware" / "healer"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Run records from the tests go to a temp dir, never arm/runs/ (safe_replay reads this at import time).
os.environ.setdefault("ARM_RUNS_DIR", tempfile.mkdtemp(prefix="arm-runs-"))
