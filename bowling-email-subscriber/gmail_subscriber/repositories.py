from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg

from config import GmailSettings

STATE_FILE = Path(__file__).resolve().parents[1] / "gmail-history.sqlite3"
SCHEMA_LOCK_ID = 724938202
logger = logging.getLogger(__name__)


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
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS inspected_messages "
            "(mailbox_email TEXT NOT NULL, message_id TEXT NOT NULL, "
            "inspected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (mailbox_email, message_id))"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS accepted_invites "
            "(message_id TEXT PRIMARY KEY, accepted_at TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS mailbox_processing_leases "
            "(mailbox_email TEXT PRIMARY KEY, lease_token TEXT NOT NULL, "
            "lease_until REAL NOT NULL)"
        )

    @classmethod
    @contextmanager
    def from_settings(cls, path=STATE_FILE):
        settings = GmailSettings()
        database_url = settings.DATABASE_URL
        if settings.TOKEN_JSON and not database_url:
            raise RuntimeError("DATABASE_URL is required with cloud Gmail credentials")
        connection = (
            psycopg.connect(database_url.get_secret_value(), connect_timeout=10)
            if database_url
            else sqlite3.connect(path, timeout=30)
        )
        try:
            repository = cls(connection)
            if database_url:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_ID,)
                )
            repository.create_tables()
            connection.commit()
            yield repository
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            except Exception:
                logger.exception(
                    "Database rollback failed; preserving the original exception"
                )
            raise
        finally:
            connection.close()

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
            "UPDATE mailbox SET history_id = CASE "
            "WHEN CAST(history_id AS NUMERIC) < CAST(? AS NUMERIC) THEN ? "
            "ELSE history_id END WHERE email = ?",
            (history_id, history_id, email),
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

    def was_message_inspected(self, mailbox_email: str, message_id: str) -> bool:
        return (
            self._execute(
                "SELECT 1 FROM inspected_messages "
                "WHERE mailbox_email = ? AND message_id = ?",
                (mailbox_email, message_id),
            ).fetchone()
            is not None
        )

    def mark_message_inspected(self, mailbox_email: str, message_id: str) -> None:
        self._execute(
            "INSERT INTO inspected_messages (mailbox_email, message_id) "
            "VALUES (?, ?) ON CONFLICT (mailbox_email, message_id) DO NOTHING",
            (mailbox_email, message_id),
        )

    def was_invite_accepted(self, message_id: str) -> bool:
        return (
            self._execute(
                "SELECT 1 FROM accepted_invites WHERE message_id = ?", (message_id,)
            ).fetchone()
            is not None
        )

    def record_accepted_invite(self, message_id: str, accepted_at: str) -> None:
        self._execute(
            "INSERT INTO accepted_invites (message_id, accepted_at) "
            "VALUES (?, ?) ON CONFLICT (message_id) DO NOTHING",
            (message_id, accepted_at),
        )

    def acquire_processing_lease(
        self, mailbox_email: str, lease_token: str, *, lease_seconds: int = 180
    ) -> bool:
        now = time.time()
        result = self._execute(
            "INSERT INTO mailbox_processing_leases "
            "(mailbox_email, lease_token, lease_until) VALUES (?, ?, ?) "
            "ON CONFLICT (mailbox_email) DO UPDATE SET "
            "lease_token = excluded.lease_token, lease_until = excluded.lease_until "
            "WHERE mailbox_processing_leases.lease_until <= ?",
            (mailbox_email, lease_token, now + lease_seconds, now),
        )
        return bool(result.rowcount)

    def defer_processing_lease(
        self, mailbox_email: str, lease_token: str, *, delay_seconds: int = 60
    ) -> None:
        self._execute(
            "UPDATE mailbox_processing_leases SET lease_token = ?, lease_until = ? "
            "WHERE mailbox_email = ? AND lease_token = ?",
            ("cooldown", time.time() + delay_seconds, mailbox_email, lease_token),
        )

    def release_processing_lease(self, mailbox_email: str, lease_token: str) -> None:
        self._execute(
            "DELETE FROM mailbox_processing_leases "
            "WHERE mailbox_email = ? AND lease_token = ?",
            (mailbox_email, lease_token),
        )

    def _execute(self, query: str, parameters: tuple[Any, ...]):
        if isinstance(self.connection, psycopg.Connection):
            query = query.replace("?", "%s")
        connection: Any = self.connection
        try:
            cursor = connection.execute(query, parameters)
            connection.commit()
            return cursor
        except Exception:
            try:
                connection.rollback()
            except Exception:
                logger.exception(
                    "Database rollback failed; preserving the original exception"
                )
            raise
