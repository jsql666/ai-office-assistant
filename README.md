# AI智能办公助手

面向日常办公和跨境电商的本地聊天应用。FastAPI 提供会话接口，原生网页通过 `/stream_chat` 接收 DeepSeek 的流式文本，PyMySQL 将会话和原始消息保存到 MySQL。

聊天和摘要使用 DeepSeek Flash 的非思考模式。

## 功能

| 助手 | 适用任务 |
| --- | --- |
| 通用助手 | 周报、清单和办公材料 |
| 商务邮件助手 | 起草、回复和润色商务邮件 |
| 多语言翻译助手 | 翻译办公、商品及客服内容 |
| 商品文案助手 | 商品标题、卖点和跨境电商详情 |
| 智能客服助手 | 订单、物流与退换货回复 |
| 内容总结助手 | 会议纪要、文档重点和待办 |

左侧按助手分类查看历史，可新建、切换、清空、删除和导出独立会话。浏览器记住上次选中的助手和会话，MySQL 持久保存对话。角色设置只修改当前会话，清空后恢复对应助手的默认提示词。

## 配置与启动

从项目目录运行以下 PowerShell 命令。已有 `.venv` 的情况下可以直接使用其中的 Python。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

新环境中，用 MySQL 管理员在客户端执行 `source /path/to/schema.sql` 初始化库表，再创建仅允许运行于本机的应用账号：

```sql
CREATE USER 'ai_assistant_app'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD;
GRANT SELECT, INSERT, UPDATE, DELETE ON ai_assistant.sessions TO 'ai_assistant_app'@'127.0.0.1';
GRANT SELECT, INSERT, DELETE ON ai_assistant.messages TO 'ai_assistant_app'@'127.0.0.1';
```

新环境将 `.env.example` 复制为 `.env`，填写 `AI_DB_PASSWORD` 和 `DEEPSEEK_API_KEY`。已有本地配置可直接使用。程序会加载项目的 `.env`，预先设置的环境变量优先。

旧版数据库升级前备份两张项目表。若 `sessions` 尚无 `assistant_type` 字段，用 MySQL 管理员执行一次 `scripts/add_assistant_type.sql`。该脚本为存量会话设置 `general` 默认分类并增加索引。新环境执行 `schema.sql` 即可。

在项目目录启动应用：

```powershell
.\.venv\Scripts\python.exe -B main.py
```

打开 <http://127.0.0.1:8000/> 继续聊天。

## 归档与迁移

- `archive/backups/` 保存升级前的代码和数据库备份。
- `archive/chat_sessions/` 保存已迁移的原始 JSON 聊天记录。
- `scripts/` 存放迁移脚本，`tests/` 存放测试。

需要从归档恢复到新数据库时，在启动应用前从项目目录执行：

```powershell
.\.venv\Scripts\python.exe -B -m scripts.migrate_json
```

默认读取 `archive/chat_sessions/`，也可用 `--source` 指定导出文件所在目录。导入使用一个事务，保留原文件并跳过数据库中同 ID 的会话。旧 JSON 默认归入通用助手，当前版本的导出同时保留助手类型和摘要覆盖轮次。旧记录的创建时间使用导入时间。

## 数据库与流程

- `sessions` 以 `session_id` 为主键，保存 `assistant_type`、角色提示词、摘要、摘要覆盖轮次、总轮数和会话版本。分类索引用于查询该助手的会话列表。
- `messages` 每轮保存两条消息。复合主键按会话和轮次读取，外键在删除会话时级联删除所属消息。
- 新建会话时根据预设生成提示词和唯一 ID。发送时只读取当前会话的提示词、摘要和未压缩消息，通过同一接口请求模型。在回复完整后，用版本校验和事务保存问答。
- 摘要每累计 6 个未压缩轮次触发，保留最近 3 轮进入模型上下文。原文仍由历史和导出接口提供，摘要失败时下一轮继续读取未压缩原文。

## 测试

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

测试使用真正的 MySQL 应用账号和临时会话，覆盖六种预设、同类和跨类隔离、会话管理、流式异常、压缩和 JSON 导入导出。自动化用例模拟 DeepSeek 回复。
