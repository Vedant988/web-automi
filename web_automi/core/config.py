"""
web_automi/core/config.py
-------------------------
Configuration settings, environment loaders, and API key pool manager for Web-Automi.
"""

import os
import sys
import threading
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# --- Database Path Resolution ---
if os.path.exists("/app/data") or os.getenv("RENDER") == "true":
    # Render persistent disk or container environment
    try:
        os.makedirs("/app/data", exist_ok=True)
    except Exception:
        pass
    DB_PATH = "/app/data/chats.db"
else:
    # Use absolute path based on the project root folder for local development to prevent absolute path trap
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    DB_PATH = os.path.join(PROJECT_ROOT, "chats.db")



# --- LLM Service Parameters ---
GROQ_API_BASE = os.getenv("GROQ_API_BASE")
DEFAULT_FINAL_MODEL = "llama-3.3-70b-versatile"
DEFAULT_REASONING_EFFORT = "low"
VLM_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
_SUMMARIZER_MODEL = "llama-3.1-8b-instant"
_SUMMARIZER_TRUNCATE_FALLBACK = 3500

TPM_LIMIT = 8000
RPM_LIMIT = 30

MODELS_WITHOUT_REASONING_EFFORT = {
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "moonshotai/kimi-k2-instruct",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
}


# --- API Key Pool Rotation (Thread-Safe) ---
def _load_api_key_pool() -> list[str]:
    keys = []
    # Primary key
    k = os.getenv("GROQ_API_KEY", "").strip()
    if k:
        keys.append(k)
    # Numbered fallbacks
    for i in range(1, 20):
        k = os.getenv(f"GROQ_API_KEY{i}", "").strip()
        if k:
            keys.append(k)
        else:
            break
    return keys

_API_KEY_POOL: list[str] = _load_api_key_pool()
_current_key_index: int = 0
_pool_lock = threading.Lock()

def get_current_key() -> str | None:
    with _pool_lock:
        if not _API_KEY_POOL:
            return None
        return _API_KEY_POOL[_current_key_index % len(_API_KEY_POOL)]

def rotate_key() -> str | None:
    global _current_key_index
    with _pool_lock:
        if len(_API_KEY_POOL) <= 1:
            return get_current_key()
        _current_key_index = (_current_key_index + 1) % len(_API_KEY_POOL)
        new_key = _API_KEY_POOL[_current_key_index]
        print(
            f"[rate-limit] Rotated to API key #{_current_key_index + 1} / {len(_API_KEY_POOL)}",
            file=sys.stderr, flush=True,
        )
        return new_key

def build_client(api_key: str | None = None) -> "Groq | None":
    key = api_key or get_current_key()
    if not key:
        return None
    if GROQ_API_BASE:
        return Groq(api_key=key, base_url=GROQ_API_BASE)
    return Groq(api_key=key)


# --- Global Cancellation Flag ---
AGENT_SHOULD_STOP = False
