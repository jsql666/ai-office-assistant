import os
from pathlib import Path
from uuid import uuid4

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor

from .assistants import ASSISTANTS

PROJECT_DIR = Path(__file__).resolve().parent.parent
SESSION_ID_LIMIT = 191

load_dotenv(PROJECT_DIR / ".env")


class DatabaseConfigError(Exception):
    pass


class SessionConflict(Exception):
    pass


def connect():
    password = os.getenv("AI_DB_PASSWORD")
    if not password:
        raise DatabaseConfigError("AI_DB_PASSWORD is required")
    try:
        port = int(os.getenv("AI_DB_PORT", "3306"))
        if not 0 < port <= 65535:
            raise ValueError
    except ValueError as error:
        raise DatabaseConfigError("Invalid AI_DB_PORT") from error
    return pymysql.connect(
        host=os.getenv("AI_DB_HOST", "127.0.0.1"),
        port=port,
        user=os.getenv("AI_DB_USER", "ai_assistant_app"),
        password=password,
        database=os.getenv("AI_DB_NAME", "ai_assistant"),
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
    )


def create_session(assistant_type):
    session_id = uuid4().hex
    system_prompt = ASSISTANTS[assistant_type]["system_prompt"]
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO sessions (session_id, assistant_type, system_prompt, summary, revision) "
            "VALUES (%s, %s, %s, '', %s)",
            (session_id, assistant_type, system_prompt, uuid4().hex),
        )
        connection.commit()
    return session_id


def load_session(session_id, *, include_messages=True, context_only=False):
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT assistant_type, system_prompt, summary, summary_round, round_count, revision "
            "FROM sessions WHERE session_id = %s FOR SHARE",
            (session_id,),
        )
        session = cursor.fetchone()
        if session is None or not include_messages:
            return session
        cursor.execute(
            "SELECT round_no, sender_role, content FROM messages "
            "WHERE session_id = %s AND round_no > %s ORDER BY round_no, sender_role",
            (session_id, session["summary_round"] if context_only else 0),
        )
        rounds = {}
        for row in cursor:
            role = "ai" if row["sender_role"] == "assistant" else "user"
            rounds.setdefault(row["round_no"], {})[role] = row["content"]
        session["rounds"] = list(rounds.values())
        return session


def set_role(session_id, prompt):
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE sessions SET system_prompt = %s, revision = %s WHERE session_id = %s",
            (prompt, uuid4().hex, session_id),
        )
        found = cursor.rowcount == 1
        connection.commit()
    return found


def insert_round(cursor, session_id, round_no, user, reply):
    cursor.execute(
        "INSERT INTO messages (session_id, round_no, sender_role, content) "
        "VALUES (%s, %s, 'user', %s), (%s, %s, 'assistant', %s)",
        (session_id, round_no, user, session_id, round_no, reply),
    )


def append_round(session_id, session, user, reply):
    revision = uuid4().hex
    round_no = session["round_count"] + 1
    with connect() as connection, connection.cursor() as cursor:
        # 版本匹配才提交问答，避免覆盖已变更或被清空的会话。
        cursor.execute(
            "UPDATE sessions SET round_count = %s, revision = %s "
            "WHERE session_id = %s AND revision = %s",
            (round_no, revision, session_id, session["revision"]),
        )
        if cursor.rowcount == 0:
            raise SessionConflict("Session changed while generating a reply")
        insert_round(cursor, session_id, round_no, user, reply)
        connection.commit()
    return revision


def save_summary(session_id, revision, summary, summary_round):
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE sessions SET summary = %s, summary_round = %s "
            "WHERE session_id = %s AND revision = %s AND summary_round < %s",
            (summary, summary_round, session_id, revision, summary_round),
        )
        connection.commit()


def clear_session(session_id):
    with connect() as connection, connection.cursor() as cursor:
        # 锁定当前会话，按原分类恢复提示词，并在同一事务清空消息。
        cursor.execute(
            "SELECT assistant_type FROM sessions WHERE session_id = %s FOR UPDATE",
            (session_id,),
        )
        session = cursor.fetchone()
        if session is None:
            return False
        cursor.execute(
            "UPDATE sessions SET system_prompt = %s, summary = '', "
            "summary_round = 0, round_count = 0, revision = %s WHERE session_id = %s",
            (ASSISTANTS[session["assistant_type"]]["system_prompt"], uuid4().hex, session_id),
        )
        cursor.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))
        connection.commit()
    return True


def delete_session(session_id):
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
        connection.commit()


def list_sessions(assistant_type=None):
    with connect() as connection, connection.cursor() as cursor:
        if assistant_type is None:
            cursor.execute("SELECT session_id FROM sessions ORDER BY session_id")
        else:
            cursor.execute(
                "SELECT sessions.session_id, sessions.updated_at, LEFT(messages.content, 48) AS title "
                "FROM sessions LEFT JOIN messages ON messages.session_id = sessions.session_id "
                "AND messages.round_no = 1 AND messages.sender_role = 'user' "
                "WHERE sessions.assistant_type = %s "
                "ORDER BY sessions.updated_at DESC, sessions.session_id",
                (assistant_type,),
            )
        return cursor.fetchall()
