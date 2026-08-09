"""FastAPI adapter for the channel-neutral Codex AgentService."""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(Path(__file__).with_name(".env"))

from core.agent_service import ALLOWED_CHANNELS, AgentService  # noqa: E402


logging.basicConfig(level=os.getenv("GATEWAY_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("codex-gateway")
app = FastAPI(title="Codex Gateway", docs_url=None, redoc_url=None)
agent_service = AgentService()


class ChatRequest(BaseModel):
    user: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")
    channel: str = Field(min_length=1, max_length=32)
    message: str = Field(min_length=1, max_length=12000)


class ChatResponse(BaseModel):
    request_id: str
    answer: str
    cost_time_ms: int


def _require_api_key(authorization: str | None) -> None:
    expected = os.getenv("GATEWAY_API_KEY", "")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not expected or not authorization or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "agent": "codex-gateway"}


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request, authorization: str | None = Header(default=None)) -> ChatResponse:
    _require_api_key(authorization)
    request_id = uuid.uuid4().hex
    started = time.perf_counter()
    channel = payload.channel.strip().lower()
    if channel not in ALLOWED_CHANNELS:
        raise HTTPException(status_code=422, detail="channel must be one of: dingtalk, voice, web")
    try:
        answer = await agent_service.chat(user=payload.user, channel=channel, message=payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("gateway request failed request_id=%s user=%s channel=%s", request_id, payload.user, channel)
        raise HTTPException(status_code=503, detail="agent temporarily unavailable") from None
    cost_time_ms = int((time.perf_counter() - started) * 1000)
    logger.info("gateway request completed request_id=%s user=%s channel=%s ip=%s cost_time_ms=%s", request_id, payload.user, channel, request.client.host if request.client else "-", cost_time_ms)
    return ChatResponse(request_id=request_id, answer=answer, cost_time_ms=cost_time_ms)
