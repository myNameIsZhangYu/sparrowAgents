import base64
import hashlib
import hmac
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from person_agents.config.settings import settings


class LLMSettingsError(Exception):
    pass


def _encrypt(value: str, runtime_secret: str) -> tuple[bytes, bytes]:
    secret = hashlib.sha256(runtime_secret.encode()).digest()
    nonce = os.urandom(12)
    key = hashlib.sha256(nonce + secret).digest()
    payload = value.encode()
    ciphertext = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(payload))
    tag = hmac.new(secret, nonce + ciphertext, hashlib.sha256).digest()
    return nonce, tag + ciphertext


def _decrypt(nonce: bytes, sealed: bytes, runtime_secret: str) -> str:
    tag, ciphertext = sealed[:32], sealed[32:]
    secret = hashlib.sha256(runtime_secret.encode()).digest()
    expected = hmac.new(secret, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise LLMSettingsError("RUNTIME_SECRET does not match stored API key")
    key = hashlib.sha256(nonce + secret).digest()
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(ciphertext)).decode()


@dataclass(frozen=True)
class LLMRuntimeSettings:
    openai_base_url: str
    openai_model: str
    encrypted_api_key: str
    version: int

@dataclass(frozen=True)
class StoredSecret:
    nonce: bytes
    sealed: bytes


def save_llm_settings(
    openai_base_url: str,
    openai_model: str,
    openai_api_key: str,
    *,
    db_path: Path | None = None,
    runtime_secret: str | None = None,
) -> LLMRuntimeSettings:
    secret = runtime_secret if runtime_secret is not None else settings.runtime_secret
    if not secret:
        raise LLMSettingsError("RUNTIME_SECRET is required")
    nonce, sealed = _encrypt(openai_api_key, secret)
    encrypted = base64.urlsafe_b64encode(nonce + sealed).decode()
    with _connection_for(db_path) as conn:
        current = conn.execute("SELECT COALESCE(MAX(version),0) FROM runtime_llm_settings").fetchone()[0]
        version = int(current) + 1
        conn.execute(
            "INSERT INTO runtime_llm_settings (version,encrypted_api_key,openai_base_url,openai_model) VALUES (?,?,?,?)",
            (version, encrypted, openai_base_url, openai_model),
        )
    return LLMRuntimeSettings(openai_base_url, openai_model, encrypted, version)


def require_runtime_secret() -> None:
    if not settings.runtime_secret:
        raise LLMSettingsError("RUNTIME_SECRET is required")


def latest_llm_settings(*, db_path: Path | None = None) -> LLMRuntimeSettings | None:
    with _connection_for(db_path) as conn:
        row = conn.execute(
            "SELECT version,encrypted_api_key,openai_base_url,openai_model FROM runtime_llm_settings ORDER BY version DESC LIMIT 1"
        ).fetchone()
    return LLMRuntimeSettings(row[2], row[3], row[1], int(row[0])) if row else None


def decrypt_api_key(record: LLMRuntimeSettings, *, runtime_secret: str | None = None) -> str:
    secret = runtime_secret if runtime_secret is not None else settings.runtime_secret
    if not secret:
        raise LLMSettingsError("RUNTIME_SECRET is required")
    try:
        raw = base64.urlsafe_b64decode(record.encrypted_api_key.encode())
        return _decrypt(raw[:12], raw[12:], secret)
    except LLMSettingsError:
        raise
    except Exception as exc:
        raise LLMSettingsError("RUNTIME_SECRET does not match stored API key") from exc


def _connection_for(db_path: Path | None) -> sqlite3.Connection:
    path = db_path or settings.sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS runtime_llm_settings (
            version INTEGER PRIMARY KEY,
            encrypted_api_key TEXT NOT NULL,
            openai_base_url TEXT NOT NULL,
            openai_model TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    return conn
