import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse

from person_agents.config.settings import settings
from person_agents.runtime.llm_settings import latest_llm_settings, save_llm_settings
from person_agents.security.credentials import CredentialManager

router = APIRouter()
_page = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
_admin_page = (Path(__file__).parent / "static" / "admin.html").read_text(encoding="utf-8")
_admin_sessions: dict[str, float] = {}
_ADMIN_USERNAME = "admin"
_ADMIN_PASSWORD = "admin"
_SESSION_TTL = 60 * 60 * 8


@router.get("/", include_in_schema=False)
async def web_console() -> HTMLResponse:
    return HTMLResponse(_page)


@router.get("/admin", include_in_schema=False)
async def admin_console() -> HTMLResponse:
    return HTMLResponse(_admin_page)


@router.post("/admin/session", include_in_schema=False)
async def create_admin_session(payload: dict):
    if payload.get("username") != _ADMIN_USERNAME or payload.get("password") != _ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = secrets.token_urlsafe(32)
    _admin_sessions[token] = time.time() + _SESSION_TTL
    return {"token": token, "expires_at": _admin_sessions[token]}


async def require_admin(x_admin_session: str | None = Header(default=None)) -> None:
    if not x_admin_session:
        raise HTTPException(status_code=401, detail="Admin authentication required")
    expires = _admin_sessions.get(x_admin_session)
    if not expires or expires < time.time():
        raise HTTPException(status_code=401, detail="Admin authentication required")


@router.get("/admin/llm-config", include_in_schema=False)
async def get_llm_config(_: None = Depends(require_admin)):
    record = latest_llm_settings()
    return {"configured": bool(record), "base_url": record.openai_base_url if record else "", "model": record.openai_model if record else ""}


@router.post("/admin/llm-config", include_in_schema=False)
async def update_llm_config(payload: dict, _: None = Depends(require_admin)):
    required = {"base_url", "model", "api_key"}
    if not required.issubset(payload) or not all(payload[k] for k in required):
        raise HTTPException(status_code=422, detail="base_url, model and api_key are required")
    record = save_llm_settings(payload["base_url"], payload["model"], payload["api_key"])
    return {"configured": True, "version": record.version, "base_url": record.openai_base_url, "model": record.openai_model}


@router.get("/admin/console/credentials", include_in_schema=False)
async def list_credentials(_: None = Depends(require_admin)):
    manager = CredentialManager(settings.sqlite_path.parent / "credentials.db")
    with manager.connect() as conn:
        rows = conn.execute(
            "SELECT key_id,tenant_id,user_id,status,created_at FROM credentials ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return [{"key_id": r[0], "tenant_id": r[1], "user_id": r[2], "status": r[3], "created_at": r[4]} for r in rows]


@router.post("/admin/console/credentials", include_in_schema=False)
async def create_user_credential(payload: dict, _: None = Depends(require_admin)):
    if not payload.get("tenant_id") or not payload.get("user_id"):
        raise HTTPException(status_code=422, detail="tenant_id and user_id are required")
    manager = CredentialManager(settings.sqlite_path.parent / "credentials.db")
    key_id, plaintext = manager.create(payload["tenant_id"], payload["user_id"])
    return {"key_id": key_id, "plaintext": plaintext}


@router.post("/admin/console/credentials/{key_id}/rotate", include_in_schema=False)
async def rotate_user_credential(key_id: str, _: None = Depends(require_admin)):
    manager = CredentialManager(settings.sqlite_path.parent / "credentials.db")
    result = manager.rotate(key_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    new_id, plaintext = result
    return {"key_id": new_id, "plaintext": plaintext}


@router.post("/admin/console/credentials/{key_id}/revoke", include_in_schema=False)
async def revoke_user_credential(key_id: str, _: None = Depends(require_admin)):
    manager = CredentialManager(settings.sqlite_path.parent / "credentials.db")
    if not manager.revoke(key_id):
        raise HTTPException(status_code=404, detail="Credential not found")
