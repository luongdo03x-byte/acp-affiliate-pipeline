"""Encrypted OpenAI credentials managed from the ACP operator UI."""
import base64
import os
import sqlite3

from . import crypto
from .db import audit
from .system_settings import get_system_setting, set_system_setting

API_KEY_SETTING = "openai_api_key_encrypted"
MODEL_SETTING = "openai_model"
DEFAULT_MODEL = "gpt-4o-mini"
MODELS = (
    ("gpt-5.6-luna", "GPT-5.6 Luna - nhanh, rất tiết kiệm (khuyên dùng)"),
    ("gpt-5.6-terra", "GPT-5.6 Terra - cân bằng chất lượng và chi phí"),
    ("gpt-5.6-sol", "GPT-5.6 Sol - chất lượng cao nhất, chi phí cao"),
    ("gpt-4o-mini", "GPT-4o mini - nhanh, tiết kiệm (legacy)"),
    ("gpt-4.1-mini", "GPT-4.1 mini - bám chỉ dẫn tốt"),
    ("gpt-4o", "GPT-4o - chất lượng cao hơn"),
    ("gpt-4.1", "GPT-4.1 - chất lượng cao, chi phí cao"),
)
MODEL_IDS = {item[0] for item in MODELS}


def _read(conn, key, default=None):
    try:
        return get_system_setting(conn, key, default)
    except sqlite3.OperationalError:
        return default


def has_saved_key(conn=None) -> bool:
    if conn is not None:
        return bool(_read(conn, API_KEY_SETTING, ""))
    from .db import connect
    conn = connect()
    try:
        return bool(_read(conn, API_KEY_SETTING, ""))
    finally:
        conn.close()


def get_api_key(conn=None) -> str:
    own = conn is None
    if own:
        from .db import connect
        conn = connect()
    try:
        encoded = _read(conn, API_KEY_SETTING, "")
        if encoded:
            return crypto.decrypt(base64.b64decode(encoded))
    finally:
        if own:
            conn.close()
    return os.environ.get("ACP_OPENAI_API_KEY", "")


def get_model(conn=None) -> str:
    own = conn is None
    if own:
        from .db import connect
        conn = connect()
    try:
        saved = str(_read(conn, MODEL_SETTING, "") or "").strip()
    finally:
        if own:
            conn.close()
    return saved or os.environ.get("ACP_OPENAI_MODEL", DEFAULT_MODEL)


def save(conn, api_key: str, model: str, actor: str = "operator") -> None:
    model = str(model or "").strip()
    if model not in MODEL_IDS:
        raise ValueError("Model OpenAI không được hỗ trợ")
    api_key = str(api_key or "").strip()
    if api_key:
        encrypted = base64.b64encode(crypto.encrypt(api_key)).decode("ascii")
        set_system_setting(conn, API_KEY_SETTING, encrypted, actor=actor)
    set_system_setting(conn, MODEL_SETTING, model, actor=actor)


def clear_key(conn, actor: str = "operator") -> None:
    conn.execute("DELETE FROM system_setting WHERE key=?", (API_KEY_SETTING,))
    audit(conn, "system_setting", API_KEY_SETTING, "cleared", actor=actor)
