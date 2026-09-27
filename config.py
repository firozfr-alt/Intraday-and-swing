import os
import streamlit as st

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

try:
    from dotenv import load_dotenv, set_key
    load_dotenv(ENV_PATH)
    _HAS_DOTENV = True
except ImportError:
    _HAS_DOTENV = False


def _from_secrets(key: str) -> str:
    try:
        return st.secrets.get(key, "")
    except Exception:
        return ""


def _get_setting(key: str, default: str = "") -> str:
    try:
        if key in st.session_state and st.session_state[key]:
            return st.session_state[key]
    except Exception:
        pass
    return _from_secrets(key) or os.getenv(key, default)


CLIENT_ID = _get_setting("UPSTOX_CLIENT_ID", "")
CLIENT_SECRET = _get_setting("UPSTOX_CLIENT_SECRET", "")
REDIRECT_URI = _get_setting("UPSTOX_REDIRECT_URI", "https://127.0.0.1:5000/callback")
ACCESS_TOKEN = _get_setting("UPSTOX_ACCESS_TOKEN", "")

BASE_URL = "https://api.upstox.com/v2"


def get_access_token() -> str:
    return _get_setting("UPSTOX_ACCESS_TOKEN", ACCESS_TOKEN)


def save_access_token(token: str) -> None:
    global ACCESS_TOKEN
    token = token.strip()
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
    try:
        st.session_state["UPSTOX_CLIENT_ID"] = CLIENT_ID
        st.session_state["UPSTOX_CLIENT_SECRET"] = CLIENT_SECRET
        st.session_state["UPSTOX_REDIRECT_URI"] = REDIRECT_URI
    except Exception:
        pass


def credentials_present() -> bool:
    return bool(
        _get_setting("UPSTOX_CLIENT_ID", CLIENT_ID)
        and _get_setting("UPSTOX_CLIENT_SECRET", CLIENT_SECRET)
        and _get_setting("UPSTOX_REDIRECT_URI", REDIRECT_URI)
    )
