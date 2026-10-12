import asyncio
import hmac
import os
import secrets
import time
from contextlib import nullcontext
from uuid import UUID, uuid4
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from agent.graph import agent, call_tool
from ai.audit import log
from ai.security import INJECTION, digest, redact, sign

router = APIRouter()
sessions, ip_requests = {}, {}

class ChatRequest(BaseModel):
    session_id: UUID
    message: str = Field(min_length=1, max_length=2000)
    confirm_token: str | None = Field(default=None, max_length=64)

def trace(request_id):
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return nullcontext(None)
    from langfuse import get_client
    return get_client().start_as_current_span(name="expense-chat", metadata={"request_id": request_id})

@router.post("/chat")
async def chat(body: ChatRequest, request: Request, x_chat_token: str = Header(default="")):
    secret = os.getenv("CHAT_ACCESS_TOKEN", "")
    if len(secret) < 32 or len(os.getenv("WRITE_APPROVAL_KEY", "")) < 32:
        raise HTTPException(503, "AI features offline: configure demo access and approval keys")
    if not hmac.compare_digest(secret, x_chat_token):
        raise HTTPException(401, "Chat access token required")
    now, sid, request_id = time.time(), str(body.session_id), str(uuid4())
    for key in list(sessions):
        if now - sessions[key]["seen"] > 1800:
            del sessions[key]
    if sid not in sessions and len(sessions) >= 100:
        raise HTTPException(429, "Session capacity reached; try later")
    session = sessions.setdefault(sid, {"history": [], "requests": [], "seen": now})
    session["seen"] = now
    ip = request.client.host if request.client else "local"
    for key in list(ip_requests):
        if not ip_requests[key] or now-ip_requests[key][-1] >= 60:
            del ip_requests[key]
    if ip not in ip_requests and len(ip_requests) >= 100:
        raise HTTPException(429, "Chat capacity reached")
    ip_requests[ip] = [t for t in ip_requests.get(ip, []) if now-t < 60]
    session["requests"] = [t for t in session["requests"] if now-t < 60]
    if len(session["requests"]) >= 10 or len(ip_requests[ip]) >= 60:
        log(request_id, sid, "chat", body.message, status="blocked", events=("rate_limit",))
        raise HTTPException(429, "Chat rate limit reached")
    session["requests"].append(now); ip_requests[ip].append(now)
    question, pii = redact(body.message)
    events = ["pii_input"] if pii else []
    start, result = time.monotonic(), {}
    try:
        # Fail closed if audit storage is unavailable, before any model/tool call.
        log(request_id, sid, "request_started", question)
        with trace(request_id) as span:
            if INJECTION.search(question):
                result = {"response": "Request blocked: unsafe instructions.", "route": "blocked", "citations": []}
                events.append("injection_input")
            elif body.confirm_token:
                pending = session.get("pending", {})
                if pending.get("token") != body.confirm_token or pending.get("expires", 0) < now:
                    raise HTTPException(409, "Confirmation missing or expired; submit the request again")
                if "result" not in pending:
                    output = await call_tool("crud_expense", {"approval": sign(pending["payload"])})
                    pending["result"] = {"route": "crud", "response": f"{output['action']} confirmed for expense {output['expense_id']}. Run Spark ETL to update analytics.", "citations": []}
                result = pending["result"]
            else:
                state = await asyncio.wait_for(agent.ainvoke({"question": question, "session_id": sid, "request_id": request_id, "history": session["history"]}), timeout=60)
                result = {k: state[k] for k in ("response", "route", "citations")}
                result["sources"] = state.get("docs", [])
                if state.get("tool"):
                    result.update(state["tool"])
                events += state.get("events", [])
                if state["route"] == "crud":
                    token, expires = secrets.token_urlsafe(24), now+300
                    session["pending"] = {"token": token, "expires": expires, "payload": {"draft": state["draft"], "session_id": sid, "request_id": request_id, "nonce": str(uuid4()), "expires": expires}}
                    result.update(draft=state["draft"], confirm_token=token)
                else:
                    session.pop("pending", None)
                session["history"] = (session["history"]+[question, result["response"]])[-6:]
            if span:
                span.update(metadata={"route": result["route"], "tool_calls": ["query_warehouse"] if "sql" in result else ["crud_expense"] if body.confirm_token else [], "latency_ms": (time.monotonic()-start)*1000, "session_hash": digest(sid)}, output={"status": result["route"]})
                result["trace_id"] = span.trace_id
    except HTTPException:
        log(request_id, sid, "chat", question, status="blocked", events=("confirmation_rejected",))
        raise
    except Exception:
        result = {"route": "offline", "response": "AI features offline or request rejected. Check AI configuration and retry.", "citations": []}
        events.append("dependency_or_policy_failure")
    try:
        log(request_id, sid, "chat", question, (time.monotonic()-start)*1000, result["route"], len(result.get("citations", [])), events)
    except Exception:
        raise HTTPException(503, "AI audit storage unavailable") from None
    return {**result, "question": question, "request_id": request_id, "test_mode": os.getenv("LLM_PROVIDER") == "mock"}
