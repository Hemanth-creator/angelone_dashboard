"""
Angel One SmartAPI authentication.

Reads credentials from environment variables (loaded from a local .env file
via python-dotenv) and returns an authenticated SmartConnect client plus the
feed token needed for websocket streaming.

Required env vars (see .env.example):
    ANGEL_API_KEY       - API key from your SmartAPI app (smartapi.angelone.in)
    ANGEL_CLIENT_CODE   - Your Angel One client / trading account code
    ANGEL_PIN           - Your 4-digit login PIN (NOT your trading password)
    ANGEL_TOTP_SECRET   - Base32 TOTP secret (from smartapi.angelbroking.com/enable-totp)

Nothing here ever leaves your machine. The session token is cached to
.session_cache.json next to this file so you don't re-login on every restart
within the same day (Angel One sessions are valid ~1 trading day / until logout).
"""

import json
import os
from pathlib import Path

import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect

from ist_time import today_ist_str

load_dotenv()

CACHE_FILE = Path(__file__).parent / ".session_cache.json"


class AuthError(RuntimeError):
    pass


def _required_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise AuthError(
            f"Missing {name} in your .env file. Copy .env.example to .env "
            f"and fill in your Angel One SmartAPI credentials."
        )
    return val


def _load_cache():
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            # Cache is only valid for the same IST trading day (session resets daily)
            if data.get("date") == today_ist_str():
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _save_cache(jwt_token, refresh_token, feed_token, client_code):
    CACHE_FILE.write_text(
        json.dumps(
            {
                "date": today_ist_str(),
                "jwt_token": jwt_token,
                "refresh_token": refresh_token,
                "feed_token": feed_token,
                "client_code": client_code,
            }
        )
    )


def get_session(force_relogin: bool = False):
    """
    Returns (smart_connect_client, feed_token, client_code).

    Uses a same-day cache to avoid hitting the login API (and burning your
    rate limit / triggering 2FA repeatedly) on every Streamlit rerun.
    """
    api_key = _required_env("ANGEL_API_KEY")
    client_code = _required_env("ANGEL_CLIENT_CODE")
    pin = _required_env("ANGEL_PIN")
    totp_secret = _required_env("ANGEL_TOTP_SECRET")

    smart_api = SmartConnect(api_key=api_key)

    cache = None if force_relogin else _load_cache()
    if cache:
        try:
            smart_api.setAccessToken(cache["jwt_token"])
            smart_api.setRefreshToken(cache["refresh_token"])
            smart_api.setFeedToken(cache["feed_token"])
            # A cached token can be dead even on the same day (expired, or the account
            # logged in elsewhere). Prove it works before reusing it.
            profile = smart_api.getProfile(cache["refresh_token"])
            if isinstance(profile, dict) and (profile.get("status") or profile.get("success")):
                return smart_api, cache["feed_token"], cache["client_code"]
            print("[auth] Cached session is no longer valid - logging in again.")
        except Exception:
            print("[auth] Cached session check failed - logging in again.")
        # fall through to fresh login

    try:
        totp = pyotp.TOTP(totp_secret).now()
    except Exception as e:
        raise AuthError(f"Could not generate TOTP from ANGEL_TOTP_SECRET: {e}")

    smart_api = SmartConnect(api_key=api_key)  # clean client, no stale tokens from the cache attempt
    data = smart_api.generateSession(client_code, pin, totp)
    if not data.get("status"):
        raise AuthError(f"Angel One login failed: {data.get('message', data)}")

    jwt_token = data["data"]["jwtToken"]
    refresh_token = data["data"]["refreshToken"]
    feed_token = smart_api.getfeedToken()

    _save_cache(jwt_token, refresh_token, feed_token, client_code)
    return smart_api, feed_token, client_code


def logout():
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()