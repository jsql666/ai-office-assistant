import argparse
import json
from pathlib import Path
from uuid import uuid4

from pymysql import IntegrityError, MySQLError

from ai_chat_plugin.assistants import ASSISTANTS
from ai_chat_plugin.storage import (
    PROJECT_DIR,
    SESSION_ID_LIMIT,
    DatabaseConfigError,
    connect,
    insert_round,
)


def read_session(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not 0 < len(path.stem) <= SESSION_ID_LIMIT:
            raise ValueError
        if not isinstance(data, dict) or not all(
            isinstance(data.get(field), str) for field in ("system_prompt", "summary")
        ):
            raise ValueError
        rounds = data.get("rounds")
        if not isinstance(rounds, list) or not all(
            isinstance(item, dict) and all(isinstance(item.get(role), str) for role in ("user", "ai"))
            for item in rounds
        ):
            raise ValueError
        assistant_type = data.get("assistant_type", "general")
        summary_round = data.get("summary_round", 0)
        if not isinstance(assistant_type, str) or assistant_type not in ASSISTANTS:
            raise ValueError
        if type(summary_round) is not int or not 0 <= summary_round <= len(rounds):
            raise ValueError
    except (OSError, ValueError):
        raise ValueError(f"无法读取或识别会话文件：{path.name}") from None
    return data


def migrate(source):
    if not source.is_dir():
        raise ValueError(f"会话目录不存在：{source}")
    sessions = [(path.stem, read_session(path)) for path in sorted(source.glob("*.json"))]
    imported = skipped = rounds_imported = 0
    with connect() as connection, connection.cursor() as cursor:
        for session_id, data in sessions:
            rounds = data["rounds"]
            try:
                cursor.execute(
                    "INSERT INTO sessions "
                    "(session_id, assistant_type, system_prompt, summary, summary_round, revision, round_count) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (session_id, data.get("assistant_type", "general"), data["system_prompt"], data["summary"],
                     data.get("summary_round", 0), uuid4().hex, len(rounds)),
                )
            except IntegrityError as error:
                if error.args[0] != 1062:
                    raise
                skipped += 1
                continue
            for round_no, item in enumerate(rounds, start=1):
                insert_round(cursor, session_id, round_no, item["user"], item["ai"])
            imported += 1
            rounds_imported += len(rounds)
        connection.commit()
    return imported, skipped, rounds_imported


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="将现有 JSON 会话导入 MySQL")
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_DIR / "archive" / "chat_sessions",
        help="JSON 会话目录，默认为 archive/chat_sessions",
    )
    args = parser.parse_args()
    try:
        imported, skipped, rounds_imported = migrate(args.source)
    except (DatabaseConfigError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    except MySQLError as error:
        parser.exit(1, f"导入失败，事务未提交。MySQL 错误：{error.args[0]}\n")
    print(f"导入 {imported} 个会话、{rounds_imported} 轮对话，跳过 {skipped} 个已存在的会话。")
