import os
import streamlit as st
from dotenv import load_dotenv, set_key

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_PATH)


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
    return st.session_state.get("UPSTOX_ACCESS_TOKEN") or ACCESS_TOKEN


def save_access_token(token: str) -> None:
    """Persist token in Streamlit session_state, memory, and local .env."""
    global ACCESS_TOKEN
    ACCESS_TOKEN = token
    st.session_state["UPSTOX_ACCESS_TOKEN"] = token
    try:
        if not os.path.exists(ENV_PATH):
            open(ENV_PATH, "a").close()
        set_key(ENV_PATH, "UPSTOX_ACCESS_TOKEN", token)
    except Exception:
        # Streamlit Cloud file systems may be read-only; session_state handles it
        pass


def set_runtime_credentials(client_id: str, client_secret: str, redirect_uri: str) -> None:
    global CLIENT_ID, CLIENT_SECRET, REDIRECT_URI
    CLIENT_ID = client_id.strip()
    CLIENT_SECRET = client_secret.strip()
    REDIRECT_URI = redirect_uri.strip()


def credentials_present() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)
