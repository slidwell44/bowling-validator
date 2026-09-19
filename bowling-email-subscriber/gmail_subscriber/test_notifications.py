import base64
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gmail_subscriber.endpoints import router
from gmail_subscriber.services import (
    history_state,
    is_relevant_subject,
    matching_body,
    process_notification,
    start_watch,
)


def raw_email(subject="Substitute bowler request"):
    message = EmailMessage()
    message["Subject"] = subject
    message.set_content("Plain body with unicode: caf\u00e9")
    message.add_alternative("<p>HTML body</p>", subtype="html")
    message.add_attachment(
        b"not the body",
        maintype="application",
        subtype="octet-stream",
        filename="test.txt",
    )
    return base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")


class NotificationTests(unittest.TestCase):
    def test_mime_body_and_subject_marker(self):
        self.assertEqual(
            matching_body(raw_email()), "Plain body with unicode: caf\u00e9\n"
        )
        self.assertEqual(
            matching_body(raw_email("New Substitute Bowler Request - Tuesday")),
            "Plain body with unicode: café\n",
        )
        self.assertEqual(
            matching_body(raw_email("Test")), "Plain body with unicode: café\n"
        )
        self.assertIsNone(matching_body(raw_email("Re: Test")))
        self.assertTrue(is_relevant_subject("Test"))
        self.assertTrue(is_relevant_subject("New substitute bowler request"))
        self.assertFalse(is_relevant_subject("Re: Test"))
        raw = base64.urlsafe_b64encode(
            b"Subject: =?utf-8?q?Substitute_bowler_request?=\r\nContent-Type: text/html\r\n\r\n<p>Hello</p>"
        ).decode()
        self.assertEqual(matching_body(raw), "<p>Hello</p>")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.sqlite3"
        with history_state(self.path) as state:
            state.execute("INSERT INTO mailbox VALUES ('user@example.com', '10')")
        self.gmail = MagicMock()

    def test_pages_duplicates_and_out_of_order_delivery(self):
        added = {"message": {"id": "one", "labelIds": ["INBOX"]}}
        sent = {"message": {"id": "sent", "labelIds": ["SENT"]}}
        self.gmail.users().history().list().execute.side_effect = [
            {"history": [{"messagesAdded": [added, sent]}], "nextPageToken": "next"},
            {"history": [{"messagesAdded": [added]}], "historyId": "20"},
        ]
        self.gmail.users().messages().get().execute.side_effect = [
            {
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Substitute bowler request"}
                    ]
                }
            },
            {"raw": raw_email()},
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            process_notification(self.gmail, "user@example.com", "15", path=self.path)
            process_notification(self.gmail, "user@example.com", "15", path=self.path)
        self.assertEqual(output.getvalue().count("--- Gmail message"), 1)
        self.assertEqual(self.gmail.users().history().list().execute.call_count, 2)
        with history_state(self.path) as state:
            row = state.execute("SELECT history_id FROM mailbox").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(
                row[0],
                "20",
            )
            processed = state.execute(
                "SELECT message_id, mailbox_email, subject, body, history_id "
                "FROM processed_messages"
            ).fetchone()
            self.assertEqual(
                processed,
                (
                    "one",
                    "user@example.com",
                    "Substitute bowler request",
                    "Plain body with unicode: café\n",
                    "15",
                ),
            )

    def test_failure_does_not_advance_cursor(self):
        self.gmail.users().history().list().execute.side_effect = RuntimeError(
            "network failure"
        )
        with self.assertRaises(RuntimeError):
            process_notification(self.gmail, "user@example.com", "15", path=self.path)
        with history_state(self.path) as state:
            row = state.execute("SELECT history_id FROM mailbox").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(
                row[0],
                "10",
            )

    def test_renewal_preserves_cursor_and_explicit_reset_replaces_it(self):
        self.gmail.users().getProfile().execute.return_value = {
            "emailAddress": "user@example.com"
        }
        self.gmail.users().watch().execute.return_value = {"historyId": "30"}
        start_watch(self.gmail, "projects/test/topics/mail", path=self.path)
        with history_state(self.path) as state:
            row = state.execute("SELECT history_id FROM mailbox").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(
                row[0],
                "10",
            )
        start_watch(
            self.gmail, "projects/test/topics/mail", path=self.path, reset_history=True
        )
        with history_state(self.path) as state:
            row = state.execute("SELECT history_id FROM mailbox").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(
                row[0],
                "30",
            )


class PushTests(unittest.TestCase):
    def test_service_provider_reuses_service(self):
        from gmail_subscriber.dependencies import get_gmail_subscriber_service

        with patch("gmail_subscriber.dependencies.GmailSubscriberService") as service:
            get_gmail_subscriber_service.cache_clear()
            self.assertIs(
                get_gmail_subscriber_service(), get_gmail_subscriber_service()
            )
            service.assert_called_once_with()
            get_gmail_subscriber_service.cache_clear()

    def test_cloud_state_requires_database(self):
        with patch("gmail_subscriber.services.GmailSettings") as settings:
            settings.return_value.TOKEN_JSON = "configured"
            settings.return_value.DATABASE_URL = None
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"), history_state():
                self.fail("Must not fall back to ephemeral SQLite")

    def test_postgres_lock_and_rollback(self):
        with (
            patch("gmail_subscriber.services.GmailSettings") as settings,
            patch("gmail_subscriber.services.psycopg.connect") as connect,
        ):
            settings.return_value.DATABASE_URL.get_secret_value.return_value = (
                "postgresql://test"
            )
            connection = connect.return_value
            with (
                self.assertRaisesRegex(RuntimeError, "processing failed"),
                history_state(),
            ):
                raise RuntimeError("processing failed")
            connection.execute.assert_any_call(
                "SELECT pg_advisory_xact_lock(724938201)"
            )
            connection.rollback.assert_called_once()
            connection.commit.assert_not_called()
            connection.close.assert_called_once()

    def test_authentication_payload_and_retry(self):
        from gmail_subscriber.dependencies import provide_gmail_subscriber_service

        app = FastAPI()
        app.include_router(router)
        service = MagicMock()
        app.dependency_overrides[provide_gmail_subscriber_service] = lambda: service
        client = TestClient(app, raise_server_exceptions=False)
        data = base64.b64encode(
            json.dumps({"emailAddress": "user@example.com", "historyId": "15"}).encode()
        ).decode()
        envelope = {"message": {"data": data}}
        headers = {"Authorization": "Bearer token"}
        with (
            patch(
                "gmail_subscriber.services.GmailSubscriberService.verify_identity"
            ) as verify,
        ):
            self.assertEqual(
                client.post("/gmail-subscriber/push", json=envelope).status_code, 401
            )
            service.process_notification.assert_not_called()
            verify.side_effect = ValueError("wrong identity")
            self.assertEqual(
                client.post(
                    "/gmail-subscriber/push", json=envelope, headers=headers
                ).status_code,
                401,
            )
            verify.side_effect = None
            self.assertEqual(
                client.post(
                    "/gmail-subscriber/push", json=envelope, headers=headers
                ).status_code,
                204,
            )
            service.process_notification.assert_called_once_with(
                "user@example.com", "15"
            )
            service.process_notification.reset_mock()
            urlsafe_data = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {"emailAddress": "user@example.com", "historyId": "16"}
                    ).encode()
                )
                .decode()
                .rstrip("=")
            )
            self.assertEqual(
                client.post(
                    "/gmail-subscriber/push",
                    json={"message": {"data": urlsafe_data}},
                    headers=headers,
                ).status_code,
                204,
            )
            service.process_notification.assert_called_once_with(
                "user@example.com", "16"
            )
            numeric_data = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {"emailAddress": "user@example.com", "historyId": 17}
                    ).encode()
                )
                .decode()
                .rstrip("=")
            )
            service.process_notification.reset_mock()
            self.assertEqual(
                client.post(
                    "/gmail-subscriber/push",
                    json={"message": {"data": numeric_data}},
                    headers=headers,
                ).status_code,
                204,
            )
            service.process_notification.assert_called_once_with(
                "user@example.com", "17"
            )
            self.assertEqual(
                client.post(
                    "/gmail-subscriber/push",
                    json={"message": {"data": "invalid"}},
                    headers=headers,
                ).status_code,
                400,
            )
            service.process_notification.side_effect = RuntimeError("temporary failure")
            self.assertEqual(
                client.post(
                    "/gmail-subscriber/push", json=envelope, headers=headers
                ).status_code,
                500,
            )
            service.start_watch.return_value = {"historyId": "30"}
            self.assertEqual(
                client.post("/gmail-subscriber/watch", headers=headers).status_code, 200
            )
            verify.assert_called_with("token", watch=True)
            self.assertEqual(client.get("/gmail-subscriber/labels").status_code, 401)

    def test_identity_checks_audience_and_service_account(self):
        from gmail_subscriber.services import GmailSubscriberService

        with (
            patch("gmail_subscriber.services.GmailSettings") as settings,
            patch("gmail_subscriber.services.id_token.verify_oauth2_token") as verify,
        ):
            settings.return_value.PUSH_AUDIENCE = "https://app/push"
            settings.return_value.PUSH_SERVICE_ACCOUNT = "push@example.com"
            verify.return_value = {"email": "push@example.com", "email_verified": True}
            GmailSubscriberService.verify_identity("token")
            self.assertEqual(verify.call_args.kwargs["audience"], "https://app/push")
            verify.return_value["email"] = "other@example.com"
            with self.assertRaises(ValueError):
                GmailSubscriberService.verify_identity("token")


if __name__ == "__main__":
    unittest.main()
