from pydantic import BaseModel, Field


class Provenance(BaseModel):
    # 保留每次执行使用的模型/提示词/工具/策略版本。
    agent_version: str
    prompt_version: str
    tool_config_version: str
    policy_version: str


class RunRequest(BaseModel):
    # 长度限制在 API 入口阻断超大 prompt，保护下游 LLM 和数据库。
    prompt: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None


class RunResponse(BaseModel):
    # confirmation_token 只在需要人工确认时返回。
    session_id: str
    status: str
    output: str | None = None
    confirmation_token: str | None = None
    provenance: Provenance | None = None


class RunAcceptedResponse(BaseModel):
    run_id: str
    status: str
    session_id: str


class CredentialResponse(BaseModel):
    # 明文 key 只应展示一次；调用方负责安全保存。
    key_id: str
    plaintext: str


class CredentialRotateRequest(BaseModel):
    key_id: str


class CredentialRevokeRequest(BaseModel):
    key_id: str
