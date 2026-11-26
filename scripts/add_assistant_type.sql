USE ai_assistant;

-- 默认值把存量会话归入通用助手。
ALTER TABLE sessions
    ADD COLUMN assistant_type VARCHAR(24) NOT NULL DEFAULT 'general' AFTER session_id,
    ADD INDEX idx_sessions_assistant_type (assistant_type, updated_at DESC, session_id);
