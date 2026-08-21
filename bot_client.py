"""
bot_client.py
Unified Telegram Bot API Client with Encapsulated Credentials & Token Isolation.

Provides isolated TelegramBotClient instances per bot actor:
- gluco_track (GlucoTrack Bot)
- med_flow (MedFlowAssist Bot)
- monke_helper (MonkeHelper Master Hub)
- biometrics (Circadian & Biometrics Bot)
"""

import os
import requests
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime, timezone
import db

TELEGRAM_API_BASE = "https://api.telegram.org/bot"

# Default hardcoded bot tokens
DEFAULT_MED_BOT_TOKEN = "8839060131:AAFRBcijx-Aic7COA7eKIjoBKpZ8ABlQ53o"
DEFAULT_MONKE_BOT_TOKEN = "8703572491:AAG6puQZOmpCey4rHbILMpJ3a0ojuOIY3s8"


def mask_token(token: Optional[str]) -> str:
    """Masks a token for secure logging."""
    if not token or len(token) < 10:
        return "UNCONFIGURED"
    return f"{token[:6]}...{token[-3:]}"


class TelegramBotClient:
    """
    Isolated Telegram Bot API client encapsulating credentials, base URL,
    request timeouts, error handling, and message operations.
    """

    def __init__(
        self,
        bot_id: str,
        name: str,
        token_getter: Callable[[], Optional[str]],
        chat_id_getter: Optional[Callable[[], Optional[str]]] = None,
        default_token: Optional[str] = None,
        default_chat_id: Optional[str] = None,
        timeout: int = 12
    ):
        self.bot_id = bot_id
        self.name = name
        self.token_getter = token_getter
        self.chat_id_getter = chat_id_getter
        self.default_token = default_token
        self._default_chat_id = default_chat_id
        self.timeout = timeout

    @property
    def token(self) -> Optional[str]:
        """Resolves token dynamically with precedence: getter -> default_token."""
        tok = None
        if self.token_getter:
            try:
                tok = self.token_getter()
            except Exception:
                tok = None

        if tok and isinstance(tok, str) and tok.strip():
            return tok.strip()

        if self.default_token and isinstance(self.default_token, str) and self.default_token.strip():
            return self.default_token.strip()

        return None

    @property
    def default_chat_id(self) -> Optional[str]:
        """Resolves default chat ID dynamically with precedence: getter -> _default_chat_id."""
        cid = None
        if self.chat_id_getter:
            try:
                cid = self.chat_id_getter()
            except Exception:
                cid = None

        if cid and str(cid).strip():
            return str(cid).strip()

        if self._default_chat_id and str(self._default_chat_id).strip():
            return str(self._default_chat_id).strip()

        return None

    @property
    def is_configured(self) -> bool:
        return bool(self.token)

    def _api_url(self, method: str) -> str:
        tok = self.token
        if not tok:
            raise ValueError(f"[{self.name}] Telegram Bot Token is not configured.")
        return f"{TELEGRAM_API_BASE}{tok}/{method}"

    def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        reply_markup: Optional[dict] = None,
        parse_mode: str = "HTML"
    ) -> dict:
        """Sends a message via Telegram Bot API."""
        tok = self.token
        target_chat = chat_id or self.default_chat_id
        if not tok or not target_chat:
            return {
                "success": False,
                "error": f"[{self.name}] Missing bot token or target chat_id."
            }

        url = self._api_url("sendMessage")
        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                return {"success": True, "result": resp.json().get("result")}
            else:
                return {
                    "success": False,
                    "status_code": resp.status_code,
                    "error": resp.text
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False
    ) -> dict:
        """Acknowledges an inline button callback query."""
        if not self.token:
            return {"success": False, "error": f"[{self.name}] Unconfigured token."}

        url = self._api_url("answerCallbackQuery")
        payload = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert
        }
        if text:
            payload["text"] = text

        try:
            resp = requests.post(url, json=payload, timeout=8)
            return {
                "success": resp.status_code == 200,
                "result": resp.json() if resp.ok else resp.text
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: Optional[dict] = None,
        parse_mode: str = "HTML"
    ) -> dict:
        """Updates the text and markup of an existing message."""
        if not self.token:
            return {"success": False, "error": f"[{self.name}] Unconfigured token."}

        url = self._api_url("editMessageText")
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            resp = requests.post(url, json=payload, timeout=8)
            return {
                "success": resp.status_code == 200,
                "result": resp.json() if resp.ok else resp.text
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_message(self, chat_id: str, message_id: int) -> dict:
        """Deletes a message from a chat."""
        if not self.token:
            return {"success": False, "error": f"[{self.name}] Unconfigured token."}

        url = self._api_url("deleteMessage")
        payload = {"chat_id": chat_id, "message_id": message_id}
        try:
            resp = requests.post(url, json=payload, timeout=8)
            return {
                "success": resp.status_code == 200,
                "result": resp.json() if resp.ok else resp.text
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_updates(
        self,
        offset: Optional[int] = None,
        limit: int = 100,
        timeout: int = 20,
        allowed_updates: Optional[List[str]] = None
    ) -> dict:
        """Fetches pending updates via getUpdates."""
        if not self.token:
            return {"success": False, "error": f"[{self.name}] Unconfigured token."}

        url = self._api_url("getUpdates")
        payload = {
            "offset": offset,
            "limit": limit,
            "timeout": timeout,
            "allowed_updates": allowed_updates or ["message", "callback_query", "my_chat_member"]
        }
        try:
            resp = requests.post(url, json=payload, timeout=timeout + 5)
            if resp.status_code == 200:
                data = resp.json()
                return {"success": True, "result": data.get("result", [])}
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": resp.text
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_webhook(self, drop_pending_updates: bool = False) -> dict:
        """Deletes registered webhook on Telegram API."""
        if not self.token:
            return {"success": False, "error": f"[{self.name}] Unconfigured token."}

        url = self._api_url("deleteWebhook")
        payload = {"drop_pending_updates": drop_pending_updates}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            return {
                "success": resp.status_code == 200,
                "result": resp.json() if resp.ok else resp.text
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_webhook(
        self,
        url: str,
        secret_token: Optional[str] = None,
        allowed_updates: Optional[List[str]] = None,
        drop_pending_updates: bool = False
    ) -> dict:
        """Registers a webhook URL with optional secret token."""
        if not self.token:
            return {"success": False, "error": f"[{self.name}] Unconfigured token."}

        api_url = self._api_url("setWebhook")
        payload = {
            "url": url,
            "drop_pending_updates": drop_pending_updates,
            "allowed_updates": allowed_updates or ["message", "callback_query", "my_chat_member"]
        }
        if secret_token:
            payload["secret_token"] = secret_token

        try:
            resp = requests.post(api_url, json=payload, timeout=10)
            return {
                "success": resp.status_code == 200,
                "result": resp.json() if resp.ok else resp.text
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


# --- Dynamic Credential Resolution Functions ---

def _get_gt_token() -> Optional[str]:
    env_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("GLUCOTRACK_BOT_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()
    try:
        stored = db.get_system_setting("telegram_config")
        if stored and isinstance(stored, dict):
            t = stored.get("bot_token")
            if t and isinstance(t, str) and t.strip():
                return t.strip()
    except Exception:
        pass
    return None


def _get_gt_chat_id() -> Optional[str]:
    env_cid = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("GLUCOTRACK_CHAT_ID")
    if env_cid and str(env_cid).strip():
        return str(env_cid).strip()
    try:
        stored = db.get_system_setting("telegram_config")
        if stored and isinstance(stored, dict):
            c = stored.get("chat_id")
            if c and str(c).strip():
                return str(c).strip()
    except Exception:
        pass
    return None


def _get_med_token() -> Optional[str]:
    env_token = os.getenv("MED_BOT_TOKEN") or os.getenv("MEDFLOW_BOT_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()
    try:
        stored = db.get_system_setting("med_bot_config")
        if stored and isinstance(stored, dict):
            t = stored.get("bot_token")
            if t and isinstance(t, str) and t.strip():
                return t.strip()
    except Exception:
        pass
    return DEFAULT_MED_BOT_TOKEN


def _get_med_chat_id() -> Optional[str]:
    env_cid = os.getenv("MED_CHAT_ID") or os.getenv("MEDFLOW_CHAT_ID")
    if env_cid and str(env_cid).strip():
        return str(env_cid).strip()
    try:
        stored = db.get_system_setting("med_bot_config")
        if stored and isinstance(stored, dict):
            c = stored.get("chat_id")
            if c and str(c).strip():
                return str(c).strip()
    except Exception:
        pass
    return None


def _get_monke_token() -> Optional[str]:
    env_token = os.getenv("MONKE_BOT_TOKEN") or os.getenv("MONKEHELPER_BOT_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()
    try:
        stored = db.get_system_setting("monke_bot_config")
        if stored and isinstance(stored, dict):
            t = stored.get("bot_token")
            if t and isinstance(t, str) and t.strip():
                return t.strip()
    except Exception:
        pass
    return DEFAULT_MONKE_BOT_TOKEN


def _get_monke_chat_id() -> Optional[str]:
    env_cid = os.getenv("MONKE_CHAT_ID") or os.getenv("MONKEHELPER_CHAT_ID")
    if env_cid and str(env_cid).strip():
        return str(env_cid).strip()
    try:
        stored = db.get_system_setting("monke_bot_config")
        if stored and isinstance(stored, dict):
            c = stored.get("chat_id")
            if c and str(c).strip():
                return str(c).strip()
    except Exception:
        pass
    return None


def _get_bio_token() -> Optional[str]:
    env_token = os.getenv("BIOMETRICS_BOT_TOKEN") or os.getenv("BIO_BOT_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()
    try:
        stored = db.get_system_setting("biometrics_bot_config")
        if stored and isinstance(stored, dict):
            t = stored.get("bot_token")
            if t and isinstance(t, str) and t.strip():
                return t.strip()
    except Exception:
        pass
    return None


def _get_bio_chat_id() -> Optional[str]:
    env_cid = os.getenv("BIOMETRICS_CHAT_ID") or os.getenv("BIO_CHAT_ID")
    if env_cid and str(env_cid).strip():
        return str(env_cid).strip()
    try:
        stored = db.get_system_setting("biometrics_bot_config")
        if stored and isinstance(stored, dict):
            c = stored.get("chat_id")
            if c and str(c).strip():
                return str(c).strip()
    except Exception:
        pass
    return None


# Singleton instances
_BOT_CLIENTS: Dict[str, TelegramBotClient] = {
    "gluco_track": TelegramBotClient(
        bot_id="gluco_track",
        name="GlucoTrack Bot",
        token_getter=_get_gt_token,
        chat_id_getter=_get_gt_chat_id,
        default_token=None
    ),
    "med_flow": TelegramBotClient(
        bot_id="med_flow",
        name="MedFlowAssist Bot",
        token_getter=_get_med_token,
        chat_id_getter=_get_med_chat_id,
        default_token=DEFAULT_MED_BOT_TOKEN
    ),
    "monke_helper": TelegramBotClient(
        bot_id="monke_helper",
        name="MonkeHelperBot",
        token_getter=_get_monke_token,
        chat_id_getter=_get_monke_chat_id,
        default_token=DEFAULT_MONKE_BOT_TOKEN
    ),
    "biometrics": TelegramBotClient(
        bot_id="biometrics",
        name="Biometrics Bot",
        token_getter=_get_bio_token,
        chat_id_getter=_get_bio_chat_id,
        default_token=None
    )
}

_BOT_ALIASES: Dict[str, str] = {
    "gluco_track": "gluco_track",
    "glucotrack": "gluco_track",
    "telegram": "gluco_track",
    "gt": "gluco_track",
    "med_flow": "med_flow",
    "medflow": "med_flow",
    "medbot": "med_flow",
    "med": "med_flow",
    "monke_helper": "monke_helper",
    "monkehelper": "monke_helper",
    "monkebot": "monke_helper",
    "monke": "monke_helper",
    "mh": "monke_helper",
    "biometrics": "biometrics",
    "circadian": "biometrics",
    "bio": "biometrics",
    "bio_bot": "biometrics"
}


def get_bot_client(bot_id: str) -> TelegramBotClient:
    """
    Returns the dedicated TelegramBotClient instance for the specified bot_id.
    Supports canonical IDs and recognized aliases.
    """
    normalized_key = bot_id.lower().strip()
    canonical_id = _BOT_ALIASES.get(normalized_key, normalized_key)
    if canonical_id not in _BOT_CLIENTS:
        raise KeyError(
            f"Unknown bot_id: '{bot_id}'. Registered bots: {list(_BOT_CLIENTS.keys())}"
        )
    return _BOT_CLIENTS[canonical_id]
