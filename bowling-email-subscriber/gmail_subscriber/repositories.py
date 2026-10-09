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


class DatabaseUnavailable(Exception):
    """Raised when the configured database cannot be reached after retries."""


def _connect_postgres(url: str):
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            return psycopg.connect(url, connect_timeout=15)
        except psycopg.OperationalError as exc:
            last_error = exc
            wait = 2**attempt
            logger.warning(
                "Database connect attempt %d failed; retrying in %ds: %s",
                attempt + 1,
                wait,
                exc,
            )
            time.sleep(wait)
    raise DatabaseUnavailable(
        f"Could not connect to the database after 4 attempts: {last_error}"
    ) from last_error


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
            "(message_id TEXT PRIMARY KEY, accepted_at TEXT NOT NULL, "
            "event_date TEXT, status TEXT NOT NULL DEFAULT 'accepted')"
        )
        accepted_columns = self._table_columns("accepted_invites")
        if "event_date" not in accepted_columns:
            self.connection.execute(
                "ALTER TABLE accepted_invites ADD COLUMN event_date TEXT"
            )
        if "status" not in accepted_columns:
            self.connection.execute(
                "ALTER TABLE accepted_invites "
                "ADD COLUMN status TEXT NOT NULL DEFAULT 'accepted'"
            )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS accepted_invites_event_date_idx "
            "ON accepted_invites (event_date)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS mailbox_processing_leases "
            "(mailbox_email TEXT PRIMARY KEY, lease_token TEXT NOT NULL, "
            "lease_until REAL NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS history_sync_checkpoints "
            "(mailbox_email TEXT PRIMARY KEY, start_history_id TEXT NOT NULL, "
            "next_page_token TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS watch_state "
            "(mailbox_email TEXT PRIMARY KEY, expiration_ms BIGINT NOT NULL, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )

    @classmethod
    @contextmanager
    def from_settings(cls, path=STATE_FILE):
        settings = GmailSettings()
        database_url = settings.DATABASE_URL
        if settings.TOKEN_JSON and not database_url:
            raise RuntimeError("DATABASE_URL is required with cloud Gmail credentials")
        connection = (
            _connect_postgres(database_url.get_secret_value())
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

    def save_watch_expiration(self, email: str, expiration_ms: int) -> None:
        self._execute(
            "INSERT INTO watch_state (mailbox_email, expiration_ms) VALUES (?, ?) "
            "ON CONFLICT (mailbox_email) DO UPDATE SET "
            "expiration_ms = excluded.expiration_ms, "
            "updated_at = CURRENT_TIMESTAMP",
            (email, expiration_ms),
        )

    def get_watch_expiration(self, email: str) -> int | None:
        row = self._execute(
            "SELECT expiration_ms FROM watch_state WHERE mailbox_email = ?",
            (email,),
        ).fetchone()
        return int(row[0]) if row is not None else None

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

    def get_history_checkpoint(self, email: str) -> tuple[str, str] | None:
        row = self._execute(
            "SELECT start_history_id, next_page_token "
            "FROM history_sync_checkpoints WHERE mailbox_email = ?",
            (email,),
        ).fetchone()
        return (row[0], row[1]) if row is not None else None

    def save_history_checkpoint(
        self, email: str, start_history_id: str, next_page_token: str
    ) -> None:
        self._execute(
            "INSERT INTO history_sync_checkpoints "
            "(mailbox_email, start_history_id, next_page_token) VALUES (?, ?, ?) "
            "ON CONFLICT (mailbox_email) DO UPDATE SET "
            "start_history_id = excluded.start_history_id, "
            "next_page_token = excluded.next_page_token",
            (email, start_history_id, next_page_token),
        )

    def complete_history_sync(self, email: str, history_id: str) -> None:
        update_query = (
            "UPDATE mailbox SET history_id = CASE "
            "WHEN CAST(history_id AS NUMERIC) < CAST(? AS NUMERIC) THEN ? "
            "ELSE history_id END WHERE email = ?"
        )
        delete_query = "DELETE FROM history_sync_checkpoints WHERE mailbox_email = ?"
        try:
            self._execute_without_commit(update_query, (history_id, history_id, email))
            self._execute_without_commit(delete_query, (email,))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

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

    def was_invite_accepted(self, event_date: str) -> bool:
        return (
            self._execute(
                "SELECT 1 FROM accepted_invites WHERE event_date = ?",
                (event_date,),
            ).fetchone()
            is not None
        )

    def reserve_invite_date(self, event_date: str, message_id: str) -> bool:
        result = self._execute(
            "INSERT INTO accepted_invites "
            "(message_id, accepted_at, event_date, status) "
            "VALUES (?, ?, ?, 'pending') ON CONFLICT DO NOTHING",
            (
                message_id,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                event_date,
            ),
        )
        return bool(result.rowcount)

    def record_accepted_invite(self, event_date: str, message_id: str) -> None:
        self._execute(
            "UPDATE accepted_invites SET status = 'accepted', accepted_at = ? "
            "WHERE event_date = ? AND message_id = ?",
            (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                event_date,
                message_id,
            ),
        )

    def _table_columns(self, table_name: str) -> set[str]:
        if isinstance(self.connection, psycopg.Connection):
            rows = self.connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s",
                (table_name,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
        column_index = 0 if isinstance(self.connection, psycopg.Connection) else 1
        return {row[column_index] for row in rows}

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
        cursor = self._execute_without_commit(query, parameters)
        self.connection.commit()
        return cursor

    def _execute_without_commit(self, query: str, parameters: tuple[Any, ...]):
        if isinstance(self.connection, psycopg.Connection):
            query = query.replace("?", "%s")
        connection: Any = self.connection
        return connection.execute(query, parameters)
