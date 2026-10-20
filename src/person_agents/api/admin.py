import hashlib
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException

from person_agents.api.schemas import (
    CredentialResponse,
    CredentialRevokeRequest,
    CredentialRotateRequest,
)
from person_agents.config.settings import settings
from person_agents.governance.audit_store import create_audit_store
from person_agents.security.credential_store import PostgresCredentialStore
from person_agents.security.credentials import CredentialManager

router = APIRouter(prefix="/admin/credentials", tags=["admin"])
_audit = create_audit_store()


async def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    request_id = uuid.uuid4().hex
    if not x_admin_key or not settings.admin_api_key_hash:
        await _audit.arecord("admin", "credential_access", status="denied", request_id=request_id)
        raise HTTPException(status_code=401, detail="Admin authentication required")
    if hashlib.sha256(x_admin_key.encode()).hexdigest() != settings.admin_api_key_hash:
        await _audit.arecord("admin", "credential_access", status="denied", request_id=request_id)
        raise HTTPException(status_code=403, detail="Invalid admin key")


def credential_manager() -> CredentialManager:
    return CredentialManager(settings.sqlite_path.parent / "credentials.db")


def postgres_credential_store() -> PostgresCredentialStore:
    return PostgresCredentialStore()


@router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(
    tenant_id: str,
    user_id: str,
    _: None = Depends(require_admin),
) -> CredentialResponse:
    if settings.postgres_dsn:
        key_id, plaintext = await postgres_credential_store().create(tenant_id, user_id)
    else:
        key_id, plaintext = credential_manager().create(tenant_id, user_id)
    return CredentialResponse(key_id=key_id, plaintext=plaintext)


@router.post("/rotate", response_model=CredentialResponse)
async def rotate_credential(
    request: CredentialRotateRequest,
    _: None = Depends(require_admin),
) -> CredentialResponse:
    result = (
        await postgres_credential_store().rotate(request.key_id)
        if settings.postgres_dsn
        else credential_manager().rotate(request.key_id)
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    key_id, plaintext = result
    return CredentialResponse(key_id=key_id, plaintext=plaintext)


@router.post("/revoke", status_code=204)
async def revoke_credential(
    request: CredentialRevokeRequest,
    _: None = Depends(require_admin),
) -> None:
    changed = (
        await postgres_credential_store().revoke(request.key_id)
        if settings.postgres_dsn
        else credential_manager().revoke(request.key_id)
    )
    if not changed:
        raise HTTPException(status_code=404, detail="Credential not found")
