import os
import sys
import tempfile
from pathlib import Path

# Isolated data directory and a pinned date so every test sees the same records.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="sahayak-test-")
os.environ["APP_TODAY"] = "2026-10-14"
os.environ["N8N_WEBHOOK_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

db.init_db(reset=True)
