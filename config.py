import os
import streamlit as st

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

# Safely load python-dotenv if installed; otherwise fallback to pure Python
try:
    from dotenv import load_dotenv, set_key
    load_dotenv(ENV_PATH)
    _HAS_DOTENV = True
except ImportError:
    _HAS_DOTENV = False
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _from_secrets(key: str) -> str:
    try:
        return st.secrets.get(key, "")
    except Exception:
        return ""


CLIENT_ID = _from_secrets("UPSTOX_CLIENT_ID") or os.getenv("UPSTOX_CLIENT_ID", "")
CLIENT_SECRET = _from_secrets("UPSTOX_CLIENT_SECRET") or os.getenv("UPSTOX_CLIENT_SECRET", "")
REDIRECT_URI = _from_secrets("UPSTOX_REDIRECT_URI") or os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1:5000/callback")
ACCESS_TOKEN = _from_secrets("UPSTOX_ACCESS_TOKEN") or os.getenv("UPSTOX_ACCESS_TOKEN", "")

BASE_URL = "https://api.upstox.com/v2"


def get_access_token() -> str:
    try:
        token = st.session_state.get("UPSTOX_ACCESS_TOKEN", "")
        if token:
            return token
    except Exception:
        pass
    return ACCESS_TOKEN


def save_access_token(token: str) -> None:
    """Persist token in Streamlit session_state, memory, and local .env."""
    global ACCESS_TOKEN
    ACCESS_TOKEN = token
    try:
        st.session_state["UPSTOX_ACCESS_TOKEN"] = token
    except Exception:
        pass

    os.environ["UPSTOX_ACCESS_TOKEN"] = token
    try:
        if _HAS_DOTENV:
            if not os.path.exists(ENV_PATH):
                open(ENV_PATH, "a").close()
            set_key(ENV_PATH, "UPSTOX_ACCESS_TOKEN", token)
    except Exception:
        pass


def set_runtime_credentials(client_id: str, client_secret: str, redirect_uri: str) -> None:
    global CLIENT_ID, CLIENT_SECRET, REDIRECT_URI
    CLIENT_ID = client_id.strip()
    CLIENT_SECRET = client_secret.strip()
    REDIRECT_URI = redirect_uri.strip()


def credentials_present() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)
