CREATE DATABASE IF NOT EXISTS ai_assistant
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

USE ai_assistant;

CREATE TABLE IF NOT EXISTS sessions (
    session_id VARCHAR(191) NOT NULL,
    assistant_type VARCHAR(24) NOT NULL DEFAULT 'general',
    system_prompt MEDIUMTEXT NOT NULL,
    summary MEDIUMTEXT NOT NULL,
    summary_round INT UNSIGNED NOT NULL DEFAULT 0,
    round_count INT UNSIGNED NOT NULL DEFAULT 0,
    revision CHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id),
    KEY idx_sessions_assistant_type (assistant_type, updated_at DESC, session_id),
    CHECK (summary_round <= round_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS messages (
    session_id VARCHAR(191) NOT NULL,
    round_no INT UNSIGNED NOT NULL,
    sender_role ENUM('user', 'assistant') NOT NULL,
    content MEDIUMTEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, round_no, sender_role),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
