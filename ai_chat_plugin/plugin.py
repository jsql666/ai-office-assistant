import logging
import os
from typing import Annotated, Optional

from fastapi import APIRouter, Form, HTTPException, Path
from fastapi.responses import StreamingResponse
from openai import APITimeoutError, OpenAI, OpenAIError
from pymysql import MySQLError

from . import storage
from .assistants import ASSISTANTS
from .storage import DatabaseConfigError, SessionConflict

KEEP_RECENT = 3
SUMMARY_TRIGGER = 6
MODEL = "deepseek-flash"

logger = logging.getLogger(__name__)
plugin_router = APIRouter()
SessionForm = Annotated[str, Form(min_length=1, max_length=storage.SESSION_ID_LIMIT)]
SessionPath = Annotated[str, Path(min_length=1, max_length=storage.SESSION_ID_LIMIT)]


class ChatStreamError(Exception):
    pass


def get_client():
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="请配置 DeepSeek API 密钥")
    return OpenAI(api_key=key, base_url="https://api.deepseek.com", timeout=30, max_retries=0)


def load_chat_session(session_id, **options):
    session = storage.load_session(session_id, **options)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


def validate_assistant(assistant_type):
    if assistant_type not in ASSISTANTS:
        raise HTTPException(status_code=422, detail="无效的助手类型")


def build_messages(session):
    # 每次只用当前会话的提示词、摘要和未压缩对话构建上下文。
    messages = [{"role": "system", "content": session["system_prompt"]}]
    if session["summary"]:
        messages.append({"role": "assistant", "content": f"历史记忆：{session['summary']}"})
    for item in session["rounds"]:
        messages.append({"role": "user", "content": item["user"]})
        messages.append({"role": "assistant", "content": item["ai"]})
    return messages


def summarize(client, session_id, session, revision):
    rounds = session["rounds"]
    if len(rounds) < SUMMARY_TRIGGER:
        return
    old_rounds = rounds[:-KEEP_RECENT]
    dialogue = "\n".join(f"用户：{item['user']}\nAI：{item['ai']}" for item in old_rounds)
    prompt = f"请把原有记忆和新增对话合并成一份简短记忆，保留关键事实。\n原有记忆：{session['summary']}\n新增对话：\n{dialogue}"
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        extra_body={"thinking": {"type": "disabled"}},
    )
    summary = (response.choices[0].message.content or "").strip() if response.choices else ""
    if summary:
        storage.save_summary(session_id, revision, summary, session["summary_round"] + len(old_rounds))


@plugin_router.get("/assistants")
def list_assistants():
    return {"assistants": [
        {"id": code, "name": preset["name"], "description": preset["description"], "example": preset["example"]}
        for code, preset in ASSISTANTS.items()
    ]}


@plugin_router.post("/create_session", status_code=201)
def create_session(assistant_type: str = Form("general")):
    validate_assistant(assistant_type)
    return {"session_id": storage.create_session(assistant_type), "assistant_type": assistant_type}


@plugin_router.post("/set_role")
def set_role(session_id: SessionForm, role_prompt: Annotated[str, Form()]):
    if not storage.set_role(session_id, role_prompt):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "success", "msg": "角色设置成功"}


@plugin_router.get("/history/{session_id}")
def get_history(session_id: SessionPath):
    session = load_chat_session(session_id)
    history = []
    for item in session["rounds"]:
        history.append({"type": "user", "content": item["user"]})
        history.append({"type": "ai", "content": item["ai"]})
    return {"history": history}


@plugin_router.post("/stream_chat")
def stream_chat(session_id: SessionForm, msg: Annotated[str, Form(min_length=1)]):
    session = load_chat_session(session_id, context_only=True)
    messages = build_messages(session)
    messages.append({"role": "user", "content": msg})
    client = get_client()
    try:
        stream = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            stream=True,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except OpenAIError as error:
        client.close()
        logger.warning("DeepSeek request failed: %s", type(error).__name__)
        if isinstance(error, APITimeoutError):
            raise HTTPException(status_code=504, detail="DeepSeek 响应超时，请重试") from None
        raise HTTPException(status_code=502, detail="DeepSeek 请求失败，请重试") from None

    def generate():
        parts = []
        finish_reason = None
        try:
            with stream as chunks:
                for chunk in chunks:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    if choice.delta.content:
                        parts.append(choice.delta.content)
                        yield choice.delta.content
            reply = "".join(parts)
            if finish_reason not in {"stop", "length"} or not reply.strip():
                raise ChatStreamError("Missing complete reply")
            revision = storage.append_round(session_id, session, msg, reply)
            session["rounds"].append({"user": msg, "ai": reply})
            try:
                summarize(client, session_id, session, revision)
            except (OpenAIError, MySQLError, DatabaseConfigError) as error:
                logger.warning("Could not update session summary: %s", type(error).__name__)
        except (OpenAIError, MySQLError, DatabaseConfigError, SessionConflict, ChatStreamError) as error:
            logger.warning("Chat round did not finish: %s", type(error).__name__)
            raise RuntimeError("Chat stream did not complete") from None
        finally:
            client.close()

    return StreamingResponse(generate(), media_type="text/plain")


@plugin_router.post("/clear_session")
def clear_session(session_id: SessionForm):
    if not storage.clear_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "success", "msg": "会话已清空"}


@plugin_router.post("/delete_session")
def delete_session(session_id: SessionForm):
    storage.delete_session(session_id)
    return {"status": "success", "msg": "会话已删除"}


@plugin_router.get("/get_role/{session_id}")
def get_role(session_id: SessionPath):
    session = load_chat_session(session_id, include_messages=False)
    return {"system_prompt": session["system_prompt"]}


@plugin_router.get("/list_sessions")
def list_sessions(assistant_type: Optional[str] = None):
    if assistant_type is not None:
        validate_assistant(assistant_type)
    rows = storage.list_sessions(assistant_type)
    result = {"sessions": [row["session_id"] for row in rows]}
    if assistant_type is not None:
        result["details"] = rows
    return result


@plugin_router.get("/session_status/{session_id}")
def session_status(session_id: SessionPath):
    session = storage.load_session(session_id, include_messages=False)
    if session is None:
        return {"exists": False, "rounds_count": 0}
    return {
        "exists": True,
        "rounds_count": session["round_count"],
        "has_summary": bool(session["summary"]),
    }


@plugin_router.get("/export_session/{session_id}")
def export_session(session_id: SessionPath):
    session = load_chat_session(session_id)
    return {field: session[field] for field in ("assistant_type", "system_prompt", "rounds", "summary", "summary_round")}
