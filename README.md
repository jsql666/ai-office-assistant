# AI智能办公助手

面向日常办公和跨境电商的聊天应用，支持 Windows 本地运行和 Linux 服务器部署。FastAPI 提供会话接口，原生网页通过 `/stream_chat` 接收 DeepSeek 的流式文本，PyMySQL 将会话和原始消息保存到 MySQL。

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

### 数据库与环境变量

应用和 MySQL 可部署在同一台机器。新环境用 MySQL 管理员在客户端执行 `source /path/to/schema.sql` 初始化库表，再创建仅允许本机连接的应用账号：

```sql
CREATE USER 'ai_assistant_app'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD;
GRANT SELECT, INSERT, UPDATE, DELETE ON ai_assistant.sessions TO 'ai_assistant_app'@'127.0.0.1';
GRANT SELECT, INSERT, DELETE ON ai_assistant.messages TO 'ai_assistant_app'@'127.0.0.1';
```

新环境将 `.env.example` 复制为 `.env`，填写 `AI_DB_PASSWORD` 和 `DEEPSEEK_API_KEY`。MySQL 在同一台机器时，`AI_DB_HOST` 使用 `127.0.0.1`。程序会加载项目的 `.env`，预先设置的环境变量优先。将服务器的真实配置保存在服务器本地的 `.env`。

旧版数据库升级前备份两张项目表。若 `sessions` 尚无 `assistant_type` 字段，用 MySQL 管理员执行一次 `scripts/add_assistant_type.sql`。该脚本为存量会话设置 `general` 默认分类并增加索引。新环境执行 `schema.sql` 即可。

### Windows 本地启动

从项目目录运行。已有虚拟环境并装好依赖时，可以直接执行最后一行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B main.py
```

打开 <http://127.0.0.1:8000/> 继续聊天。

### Linux 启动

将项目代码拉取到服务器，按上面的步骤建库、授权并配置服务器的 `.env`。新装数据库可使用 [MySQL 8.4](https://dev.mysql.com/doc/refman/8.4/en/charset-unicode-sets.html)。从项目目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -B -m uvicorn main:app --host 127.0.0.1 --port 8000
```

这里的 `127.0.0.1` 指 Linux 服务器自己。可在服务器上执行 `curl -fsS http://127.0.0.1:8000/ -o /dev/null` 检查页面是否能访问。需要保留 Windows 上的聊天历史时，从旧数据库备份中单独导入项目库的数据。

### 公网访问

- **正式访问**：让 Uvicorn 继续监听 `127.0.0.1:8000`，由 Caddy 或 Nginx 把域名的 HTTPS 请求转发到应用。站点对外使用 `80/443`，应用的 `8000` 和 MySQL 的 `3306` 留在服务器内部。 [FastAPI HTTPS 部署说明](https://fastapi.tiangolo.com/deployment/https/)
- **临时联调**：将 Uvicorn 的 `--host` 改为 `0.0.0.0`，在服务器防火墙及云安全组中将 `8000` 限制为仅自己的公网 IP 可访问，再打开 `http://服务器公网IP:8000/`。 [Uvicorn 监听设置](https://uvicorn.dev/settings/#socket-binding)
- **演示保护**：当前应用是单用户版，能访问站点的人会共用会话和 DeepSeek 额度。对外共享演示链接前，建议在 HTTPS 入口加访问密码，并使用独立的演示数据库。

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
