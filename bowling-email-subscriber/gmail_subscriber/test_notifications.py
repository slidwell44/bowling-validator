import base64
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from threading import Event, Lock, Thread
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import OperationalError

from form_automation.services import is_acceptable_date, parse_dac_invite
from gmail_subscriber.endpoints import router
from gmail_subscriber.services import (
    GmailProcessingBusy,
    GmailQuotaExceeded,
    GmailSubscriberService,
    history_state,
    is_relevant_subject,
    matching_body,
    message_subject_and_body,
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


def api_message(
    subject="Substitute bowler request",
    body="Plain body",
    sender="DAC Mail <dacmail@dacrsc.com>",
):
    encoded_body = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {
                        "data": base64.urlsafe_b64encode(b"<p>HTML body</p>").decode()
                    },
                },
                {
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "attachment-not-fetched"},
                },
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": encoded_body},
                        }
                    ],
                },
            ],
        }
    }


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

    def test_full_message_extracts_text_without_fetching_attachments(self):
        subject, body = message_subject_and_body(api_message(body="Request body"))
        self.assertEqual(subject, "Substitute bowler request")
        self.assertEqual(body, "Request body")

    def test_auto_accepts_only_one_invite_for_each_event_date(self):
        first = {"message": {"id": "first", "labelIds": ["INBOX"]}}
        second = {"message": {"id": "second", "labelIds": ["INBOX"]}}
        self.gmail.users().history().list().execute.side_effect = [
            {"history": [{"messagesAdded": [first]}], "historyId": "20"},
            {"history": [{"messagesAdded": [second]}], "historyId": "21"},
        ]
        self.gmail.users().messages().get().execute.side_effect = [
            api_message(
                subject="Substitute bowler request for Player One; 10/13/2026",
                body="Please accept: http://email.dacrsc.com/c/invite-one",
            ),
            api_message(
                subject="Substitute bowler request for Player Two; 10/13/2026",
                body="Please accept: http://email.dacrsc.com/c/invite-two",
            ),
        ]
        with (
            patch(
                "gmail_subscriber.services.accept_invite", return_value=True
            ) as accept,
            patch("gmail_subscriber.services.is_acceptable_date", return_value=True),
            redirect_stdout(io.StringIO()),
        ):
            process_notification(self.gmail, "user@example.com", "15", path=self.path)
            process_notification(self.gmail, "user@example.com", "21", path=self.path)

        accept.assert_called_once_with("http://email.dacrsc.com/c/invite-one")
        with history_state(self.path) as state:
            accepted = state.execute(
                "SELECT event_date, message_id, status FROM accepted_invites"
            ).fetchone()
        self.assertEqual(accepted, ("2026-10-13", "first", "accepted"))

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
        self.gmail.users().messages().get().execute.return_value = api_message(
            body="Plain body with unicode: café\n"
        )
        message_get = self.gmail.users().messages().get
        message_get.reset_mock()
        output = io.StringIO()
        with redirect_stdout(output):
            process_notification(self.gmail, "user@example.com", "15", path=self.path)
            process_notification(self.gmail, "user@example.com", "15", path=self.path)
        self.assertEqual(output.getvalue().count("--- Gmail message"), 1)
        self.assertEqual(self.gmail.users().history().list().execute.call_count, 2)
        for call in self.gmail.users().history().list().execute.call_args_list:
            self.assertEqual(call.kwargs["num_retries"], 3)
        self.assertEqual(message_get.call_count, 1)
        self.assertEqual(message_get.call_args.kwargs["format"], "full")
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
            inspected = state.execute(
                "SELECT mailbox_email, message_id FROM inspected_messages"
            ).fetchone()
            self.assertEqual(inspected, ("user@example.com", "one"))

    def test_retry_skips_messages_already_inspected(self):
        with history_state(self.path) as state:
            state.execute(
                "INSERT INTO inspected_messages (mailbox_email, message_id) "
                "VALUES ('user@example.com', 'one')"
            )
        self.gmail.users().history().list().execute.return_value = {
            "history": [
                {"messagesAdded": [{"message": {"id": "one", "labelIds": ["INBOX"]}}]}
            ],
            "historyId": "20",
        }

        process_notification(self.gmail, "user@example.com", "15", path=self.path)

        self.gmail.users().messages().get.assert_not_called()

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
    def test_quota_failure_puts_mailbox_in_distributed_cooldown(self):
        from gmail_subscriber.repositories import MailboxRepository

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            with history_state(path) as connection:
                repository = MailboxRepository(connection)
                with patch(
                    "gmail_subscriber.services._process_notification_with_repository",
                    side_effect=GmailQuotaExceeded("quota"),
                ) as process:
                    with self.assertRaises(GmailQuotaExceeded):
                        process_notification(
                            MagicMock(),
                            "user@example.com",
                            "15",
                            repository,
                        )
                    with self.assertRaises(GmailProcessingBusy):
                        process_notification(
                            MagicMock(),
                            "user@example.com",
                            "15",
                            repository,
                        )
                    process.assert_called_once()

    def test_database_lease_excludes_another_owner(self):
        from gmail_subscriber.repositories import MailboxRepository

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            with history_state(path) as connection:
                repository = MailboxRepository(connection)
                self.assertTrue(
                    repository.acquire_processing_lease("user@example.com", "first")
                )
                self.assertFalse(
                    repository.acquire_processing_lease("user@example.com", "second")
                )
                repository.release_processing_lease("user@example.com", "first")
                self.assertTrue(
                    repository.acquire_processing_lease("user@example.com", "second")
                )

    def test_gmail_service_calls_are_serialized(self):
        service = GmailSubscriberService(
            repository=MagicMock(), gmail_service=MagicMock()
        )
        first_entered = Event()
        second_attempting = Event()
        second_entered = Event()
        release_first = Event()
        state_lock = Lock()
        active_calls = 0
        maximum_active_calls = 0

        def fake_process_notification(_gmail, email, _history_id, _repository):
            nonlocal active_calls, maximum_active_calls
            with state_lock:
                active_calls += 1
                maximum_active_calls = max(maximum_active_calls, active_calls)
            if email == "first@example.com":
                first_entered.set()
                release_first.wait(timeout=2)
            else:
                second_entered.set()
            with state_lock:
                active_calls -= 1

        def run_first():
            service.process_notification("first@example.com", "11")

        def run_second():
            second_attempting.set()
            service.process_notification("second@example.com", "12")

        with patch(
            "gmail_subscriber.services.process_notification",
            side_effect=fake_process_notification,
        ):
            first = Thread(target=run_first)
            second = Thread(target=run_second)
            first.start()
            self.assertTrue(first_entered.wait(timeout=2))
            second.start()
            self.assertTrue(second_attempting.wait(timeout=2))
            self.assertFalse(second_entered.wait(timeout=0.1))
            release_first.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertTrue(second_entered.is_set())
        self.assertEqual(maximum_active_calls, 1)

    def test_playwright_can_select_decline_without_selecting_accept(self):
        from form_automation.services import _find_response_button

        page = MagicMock()
        decline_button = MagicMock()
        page.get_by_role.return_value = decline_button
        decline_button.count.return_value = 1
        decline_button.first.is_visible.return_value = True

        found = _find_response_button(page, "decline")

        self.assertIs(found, decline_button.first)
        self.assertEqual(page.get_by_role.call_args.kwargs["name"].pattern, "^decline$")

    def test_service_provider_reuses_service(self):
        from gmail_subscriber.dependencies import get_gmail_service

        with patch(
            "gmail_subscriber.dependencies.GmailSubscriberService.create_gmail_service"
        ) as service:
            get_gmail_service.cache_clear()
            self.assertIs(get_gmail_service(), get_gmail_service())
            service.assert_called_once_with()
            get_gmail_service.cache_clear()

    def test_cloud_state_requires_database(self):
        with patch("gmail_subscriber.repositories.GmailSettings") as settings:
            settings.return_value.TOKEN_JSON = "configured"
            settings.return_value.DATABASE_URL = None
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"), history_state():
                self.fail("Must not fall back to ephemeral SQLite")

    def test_postgres_schema_creation_is_serialized_in_short_transaction(self):
        with (
            patch("gmail_subscriber.repositories.GmailSettings") as settings,
            patch("gmail_subscriber.repositories.psycopg.connect") as connect,
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
                "SELECT pg_advisory_xact_lock(%s)", (724938202,)
            )
            connection.rollback.assert_called_once()
            connection.commit.assert_called_once()
            connection.close.assert_called_once()

    def test_history_cursor_never_moves_backwards(self):
        from gmail_subscriber.repositories import MailboxRepository

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            with history_state(path) as connection:
                connection.execute(
                    "INSERT INTO mailbox VALUES ('user@example.com', '10')"
                )
            with history_state(path) as connection:
                repository = MailboxRepository(connection)
                repository.update_history("user@example.com", "9")
                self.assertEqual(repository.get_history_id("user@example.com"), "10")

    def test_rollback_failure_preserves_original_exception(self):
        with (
            patch("gmail_subscriber.repositories.GmailSettings") as settings,
            patch("gmail_subscriber.repositories.psycopg.connect") as connect,
        ):
            settings.return_value.DATABASE_URL.get_secret_value.return_value = (
                "postgresql://test"
            )
            connection = connect.return_value
            connection.rollback.side_effect = OperationalError("the connection is lost")
            with (
                self.assertLogs("gmail_subscriber.repositories", level="ERROR") as logs,
                self.assertRaisesRegex(RuntimeError, "processing failed"),
                history_state(),
            ):
                raise RuntimeError("processing failed")

            self.assertIn(
                "Database rollback failed; preserving the original exception",
                logs.output[0],
            )
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
            service.process_notification.side_effect = GmailQuotaExceeded("quota")
            quota_response = client.post(
                "/gmail-subscriber/push", json=envelope, headers=headers
            )
            self.assertEqual(quota_response.status_code, 503)
            self.assertEqual(quota_response.headers["retry-after"], "60")
            service.process_notification.side_effect = None
            service.process_notification.side_effect = GmailProcessingBusy("busy")
            busy_response = client.post(
                "/gmail-subscriber/push", json=envelope, headers=headers
            )
            self.assertEqual(busy_response.status_code, 503)
            self.assertEqual(busy_response.headers["retry-after"], "30")
            service.process_notification.side_effect = None
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


def dac_raw_email(
    subject="Substitute bowler request for Kurt R. Vilders; 10/13/2026",
    sender="DAC Mail <dacmail@dacrsc.com>",
    body=(
        "You are invited to sub.\n"
        "Please click here to accept or decline: http://email.dacrsc.com/c/abc123\n"
        '<img src="http://email.dacrsc.com/o/xyz789">'
    ),
):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")


class DacInviteTests(unittest.TestCase):
    def test_parse_invite_extracts_url_and_date(self):
        result = parse_dac_invite(dac_raw_email())
        self.assertIsNotNone(result)
        url, event_date = result
        self.assertEqual(url, "http://email.dacrsc.com/c/abc123")
        self.assertEqual(event_date, date(2026, 10, 13))

    def test_ignores_non_dac_sender(self):
        self.assertIsNone(
            parse_dac_invite(dac_raw_email(sender="Spammer <spam@example.com>"))
        )

    def test_ignores_update_subject(self):
        self.assertIsNone(
            parse_dac_invite(
                dac_raw_email(subject="Substitute bowler update by Simon Lidwell")
            )
        )

    def test_ignores_pixel_only_body(self):
        self.assertIsNone(
            parse_dac_invite(
                dac_raw_email(body='<img src="http://email.dacrsc.com/o/xyz789">')
            )
        )

    def test_acceptable_dates(self):
        self.assertTrue(is_acceptable_date(date(2100, 1, 1)))
        self.assertFalse(is_acceptable_date(date(2000, 1, 1)))


if __name__ == "__main__":
    unittest.main()
