"""Central settings, read from environment variables (.env in development)."""
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# Bigger model for answering and tool calling, small fast model for routing/extraction.
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "llama-3.1-8b-instant")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
DOCS_DIR = ROOT / "data" / "docs"
DB_PATH = DATA_DIR / "sahayak.db"
CHECKPOINT_DB = DATA_DIR / "checkpoints.db"
QDRANT_PATH = DATA_DIR / "qdrant"
MODEL_CACHE = Path(os.getenv("MODEL_CACHE", ROOT / ".models"))
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
COLLECTION = "regulations"

# Retrieval: chunks scoring below this cosine similarity are treated as "not found".
RAG_TOP_K = 4
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.55"))

# n8n webhook that emails the faculty and logs the request in Google Sheets.
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")
N8N_SHARED_SECRET = os.getenv("N8N_SHARED_SECRET", "")
# For demos, all faculty mail can be redirected to one inbox.
FACULTY_EMAIL_OVERRIDE = os.getenv("FACULTY_EMAIL_OVERRIDE", "")

# Number of past messages sent to the LLM (older ones stay in the checkpoint).
HISTORY_WINDOW = 8


def today() -> date:
    """Allows tests and demos to pin the date (APP_TODAY=YYYY-MM-DD)."""
    pinned = os.getenv("APP_TODAY")
    return date.fromisoformat(pinned) if pinned else date.today()
