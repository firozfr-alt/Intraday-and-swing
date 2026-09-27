import requests
from urllib.parse import urlencode
import config


def build_login_url() -> str:
    params = {
        "response_type": "code",
        "client_id": config.CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI,
    }
    return f"{config.BASE_URL}/login/authorization/dialog?{urlencode(params)}"


def exchange_code_for_token(auth_code: str) -> dict:
    url = f"{config.BASE_URL}/login/authorization/token"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "code": auth_code,
        "client_id": config.CLIENT_ID,
        "client_secret": config.CLIENT_SECRET,
        "redirect_uri": config.REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    resp = requests.post(url, headers=headers, data=data, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("access_token", "")
    if token:
        config.save_access_token(token)
    return payload
