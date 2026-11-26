import json
import os
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from openai import OpenAIError

from ai_chat_plugin import plugin, storage
from ai_chat_plugin.assistants import ASSISTANTS
from ai_chat_plugin.storage import SessionConflict
from main import app
from scripts.migrate_json import migrate


def chunk(content=None, finish_reason=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)]
    )


class ChatConfigurationTests(unittest.TestCase):
    def test_missing_deepseek_api_key_returns_503(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaises(HTTPException) as failure:
                plugin.get_client()
        self.assertEqual(failure.exception.status_code, 503)


class StorageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.api = TestClient(app)
        self.addCleanup(self.api.close)
        self.session_id = self.create_session("general")
        self.contexts = []
        self.summary_inputs = []
        self.fail_summary = False
        self.upstream = Mock()
        self.upstream.chat.completions.create.side_effect = self.complete

    def complete(self, *, model, messages, extra_body, stream=False):
        self.assertEqual(model, "deepseek-flash")
        self.assertEqual(extra_body, {"thinking": {"type": "disabled"}})
        if stream:
            self.contexts.append(messages)
            return nullcontext(iter([SimpleNamespace(choices=[]), chunk("你好"), chunk("🙂"), chunk(finish_reason="stop")]))
        self.summary_inputs.append(messages[0]["content"])
        if self.fail_summary:
            raise OpenAIError("simulated summary outage")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="已压缩记忆"))])

    def create_session(self, assistant_type):
        response = self.api.post("/create_session", data={"assistant_type": assistant_type})
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["assistant_type"], assistant_type)
        session_id = data["session_id"]
        self.addCleanup(storage.delete_session, session_id)
        return session_id

    def post(self, route, *, session_id=None, **params):
        return self.api.post(route, data={"session_id": session_id or self.session_id, **params})

    def get(self, route, session_id=None):
        return self.api.get(f"{route}/{session_id or self.session_id}")

    def send(self, text, session_id=None):
        with patch("ai_chat_plugin.plugin.get_client", return_value=self.upstream):
            return self.post("/stream_chat", session_id=session_id, msg=text)

    def test_connection_and_session_lifecycle(self):
        with storage.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS db, CURRENT_USER() AS account")
            self.assertEqual(cursor.fetchone(), {"db": "ai_assistant", "account": "ai_assistant_app@127.0.0.1"})

        self.assertEqual(self.api.get("/").status_code, 200)
        self.assertTrue(self.post("/set_role", role_prompt="技术面试官").is_success)
        self.assertEqual(self.get("/get_role").json()["system_prompt"], "技术面试官")
        self.assertIn(self.session_id, self.api.get("/list_sessions").json()["sessions"])
        response = self.send("事务隔离")
        self.assertEqual(response.text, "你好🙂")
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertEqual(self.get("/history").json(), {
            "history": [{"type": "user", "content": "事务隔离"}, {"type": "ai", "content": "你好🙂"}]
        })
        self.assertTrue(self.post("/clear_session").is_success)
        self.assertEqual(self.get("/history").json()["history"], [])
        self.assertEqual(self.get("/get_role").json()["system_prompt"], ASSISTANTS["general"]["system_prompt"])
        self.assertTrue(self.send("再测试一轮").is_success)
        self.assertTrue(self.post("/delete_session").is_success)
        self.assertEqual(self.get("/history").status_code, 404)
        self.assertEqual(self.send("不应恢复已删除会话").status_code, 404)
        self.assertEqual(self.get("/session_status").json(), {"exists": False, "rounds_count": 0})
        with storage.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM messages WHERE session_id = %s", (self.session_id,))
            self.assertEqual(cursor.fetchone()["total"], 0)

    def test_summary_keeps_raw_messages(self):
        for turn in range(1, 7):
            self.assertTrue(self.send(f"聊天轮次 {turn}").is_success)
        self.assertIn("聊天轮次 1", [item["content"] for item in self.contexts[5]])
        self.assertIn("聊天轮次 3", self.summary_inputs[0])
        self.assertNotIn("聊天轮次 4", self.summary_inputs[0])
        self.assertEqual(len(self.get("/history").json()["history"]), 12)
        export = self.get("/export_session").json()
        self.assertEqual(export["summary"], "已压缩记忆")
        self.assertEqual(len(export["rounds"]), 6)
        self.assertTrue(self.send("继续").is_success)
        context = [item["content"] for item in self.contexts[-1]]
        self.assertIn("历史记忆：已压缩记忆", context)
        self.assertIn("聊天轮次 4", context)
        self.assertNotIn("聊天轮次 3", context)

    def test_summary_failure_preserves_round_and_context(self):
        self.fail_summary = True
        for turn in range(1, 7):
            self.assertTrue(self.send(f"原始对话 {turn}").is_success)
        self.assertEqual(self.get("/session_status").json(), {
            "exists": True, "rounds_count": 6, "has_summary": False
        })
        self.fail_summary = False
        self.assertTrue(self.send("重新触发摘要").is_success)
        self.assertIn("原始对话 1", [item["content"] for item in self.contexts[-1]])
        self.assertEqual(len(self.get("/history").json()["history"]), 14)

    def test_failed_reply_does_not_save_partial_messages(self):
        self.upstream.chat.completions.create.side_effect = OpenAIError("simulated connection failure")
        self.assertEqual(self.send("失败测试").status_code, 502)
        self.upstream.chat.completions.create.side_effect = None
        self.upstream.chat.completions.create.return_value = nullcontext(iter([chunk("半句回复")]))
        with self.assertRaisesRegex(RuntimeError, "Chat stream did not complete"):
            self.send("断流测试")
        self.assertEqual(self.get("/history").json()["history"], [])

    def test_stale_stream_cannot_write_to_reset_session(self):
        snapshot = storage.load_session(self.session_id)
        storage.clear_session(self.session_id)
        with self.assertRaises(SessionConflict):
            storage.append_round(self.session_id, snapshot, "旧问题", "旧回答")
        self.assertEqual(storage.load_session(self.session_id)["rounds"], [])

    def test_each_assistant_uses_its_preset_and_category(self):
        catalog = self.api.get("/assistants").json()["assistants"]
        self.assertEqual(
            [record["name"] for record in catalog],
            ["通用助手", "商务邮件助手", "多语言翻译助手", "商品文案助手", "智能客服助手", "内容总结助手"],
        )
        self.assertEqual(self.api.post("/create_session", data={"assistant_type": "unknown"}).status_code, 422)
        self.assertEqual(self.api.get("/list_sessions", params={"assistant_type": "unknown"}).status_code, 422)
        created = {preset["id"]: self.create_session(preset["id"]) for preset in catalog}
        self.assertEqual(len(set(created.values())), 6)

        for assistant_type, session_id in created.items():
            with self.subTest(assistant_type=assistant_type):
                text = "分类验证 " + assistant_type
                self.assertEqual(self.send(text, session_id).text, "你好🙂")
                self.assertEqual(self.contexts[-1], [
                    {"role": "system", "content": ASSISTANTS[assistant_type]["system_prompt"]},
                    {"role": "user", "content": text},
                ])
                sessions = self.api.get("/list_sessions", params={"assistant_type": assistant_type}).json()
                self.assertEqual([value for value in sessions["sessions"] if value in created.values()], [session_id])
                details = next(value for value in sessions["details"] if value["session_id"] == session_id)
                self.assertEqual(details["title"], text)
                export = self.get("/export_session", session_id).json()
                self.assertEqual(export["assistant_type"], assistant_type)
                self.assertEqual(export["rounds"], [{"user": text, "ai": "你好🙂"}])

    def test_same_type_sessions_and_other_types_keep_memory_separate(self):
        first = self.create_session("email")
        second = self.create_session("email")
        other = self.create_session("translation")
        self.assertTrue(self.post("/set_role", session_id=first, role_prompt="仅用于第一封邮件").is_success)
        for round_no in range(1, 7):
            self.assertTrue(self.send(f"第一会话邮件 {round_no}", first).is_success)
        self.assertEqual(self.get("/export_session", first).json()["summary_round"], 3)

        for session_id, assistant_type, text in [(second, "email", "第二会话邮件"), (other, "translation", "翻译文本")]:
            self.assertTrue(self.send(text, session_id).is_success)
            self.assertEqual(self.contexts[-1], [
                {"role": "system", "content": ASSISTANTS[assistant_type]["system_prompt"]},
                {"role": "user", "content": text},
            ])
            self.assertEqual(self.get("/session_status", session_id).json()["rounds_count"], 1)

        self.assertTrue(self.send("第一会话继续", first).is_success)
        contents = [item["content"] for item in self.contexts[-1]]
        self.assertEqual(contents[0], "仅用于第一封邮件")
        self.assertIn("历史记忆：已压缩记忆", contents)
        self.assertNotIn("第二会话邮件", contents)
        self.assertNotIn("翻译文本", contents)
        self.assertTrue(self.post("/clear_session", session_id=first).is_success)
        self.assertEqual(self.get("/export_session", first).json(), {
            "assistant_type": "email", "system_prompt": ASSISTANTS["email"]["system_prompt"],
            "summary": "", "summary_round": 0, "rounds": [],
        })
        self.assertEqual(self.get("/history", second).json()["history"][0]["content"], "第二会话邮件")

    def test_import_legacy_and_typed_export_preserves_memory_boundary(self):
        with TemporaryDirectory(dir=storage.PROJECT_DIR / "archive") as directory:
            source = Path(directory)
            session_ids = ["legacy_" + uuid4().hex, "typed_" + uuid4().hex]
            legacy = {"system_prompt": "旧自定义角色", "summary": "旧记忆", "rounds": [{"user": "原问", "ai": "原答"}]}
            typed = {**legacy, "assistant_type": "summary", "summary_round": 1}
            for session_id, data in zip(session_ids, (legacy, typed)):
                self.addCleanup(storage.delete_session, session_id)
                (source / f"{session_id}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(migrate(source), (2, 0, 2))
            self.assertEqual(migrate(source), (0, 2, 0))
            self.assertEqual(self.get("/export_session", session_ids[0]).json(), {
                **legacy, "assistant_type": "general", "summary_round": 0,
            })
            self.assertEqual(self.get("/export_session", session_ids[1]).json(), typed)


if __name__ == "__main__":
    unittest.main()
