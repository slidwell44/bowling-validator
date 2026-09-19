from __future__ import annotations

from typing import Any

import psycopg


class MailboxRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_tables(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS mailbox "
            "(email TEXT PRIMARY KEY, history_id TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS processed_messages "
            "(message_id TEXT PRIMARY KEY, mailbox_email TEXT NOT NULL, "
            "subject TEXT NOT NULL, body TEXT NOT NULL, history_id TEXT NOT NULL, "
            "processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )

    def get_history_id(self, email: str) -> str | None:
        row = self._execute(
            "SELECT history_id FROM mailbox WHERE email = ?", (email,)
        ).fetchone()
        return row[0] if row is not None else None

    def save_initial_history(self, email: str, history_id: str) -> None:
        self._execute(
            "INSERT INTO mailbox VALUES (?, ?) ON CONFLICT (email) DO NOTHING",
            (email, history_id),
        )

    def reset_history(self, email: str) -> None:
        self._execute("DELETE FROM mailbox WHERE email = ?", (email,))

    def update_history(self, email: str, history_id: str) -> None:
        self._execute(
            "UPDATE mailbox SET history_id = ? WHERE email = ?",
            (history_id, email),
        )

    def record_processed_message(
        self,
        message_id: str,
        mailbox_email: str,
        subject: str,
        body: str,
        history_id: str,
    ) -> bool:
        result = self._execute(
            "INSERT INTO processed_messages "
            "(message_id, mailbox_email, subject, body, history_id) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (message_id) DO NOTHING",
            (message_id, mailbox_email, subject, body, history_id),
        )
        return bool(result.rowcount)

    def _execute(self, query: str, parameters: tuple[Any, ...]):
        if isinstance(self.connection, psycopg.Connection):
            query = query.replace("?", "%s")
        connection: Any = self.connection
        return connection.execute(query, parameters)
