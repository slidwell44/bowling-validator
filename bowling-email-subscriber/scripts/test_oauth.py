import unittest
from unittest.mock import MagicMock, patch

from scripts.oauth import _Callback, authorize


class OAuthCallbackTests(unittest.TestCase):
    def test_ignores_probes_stale_states_and_incomplete_callbacks(self):
        callback = _Callback()
        callback.state = "current"
        for path, query in [
            ("/", ""),
            ("/favicon.ico", ""),
            ("/", "state=old&code=old-code"),
            ("/", "state=current"),
            ("/", "state=current&state=old&code=code"),
        ]:
            response = MagicMock()
            callback({"PATH_INFO": path, "QUERY_STRING": query}, response)
            self.assertEqual(response.call_args.args[0], "400 Bad Request")
            self.assertIsNone(callback.query)

    def test_accepts_current_success_or_denial(self):
        for query in ["state=current&code=code", "state=current&error=access_denied"]:
            callback = _Callback()
            callback.state = "current"
            callback({"PATH_INFO": "/", "QUERY_STRING": query}, MagicMock())
            self.assertEqual(callback.query, query)

    @patch("scripts.oauth.make_server")
    def test_waits_past_probe_and_preserves_state_for_token_exchange(self, make_server):
        flow = MagicMock()
        flow.authorization_url.return_value = ("https://example.test/auth", "current")
        server = make_server.return_value.__enter__.return_value
        server.server_port = 8080
        queries = iter(["", "state=old&code=old", "state=current&code=good"])

        def handle_request():
            callback = make_server.call_args.args[2]
            callback({"PATH_INFO": "/", "QUERY_STRING": next(queries)}, MagicMock())

        server.handle_request.side_effect = handle_request
        with patch("builtins.print"):
            self.assertIs(authorize(flow), flow.credentials)
        self.assertEqual(server.handle_request.call_count, 3)
        flow.fetch_token.assert_called_once_with(
            authorization_response="https://localhost:8080/?state=current&code=good"
        )

    @patch("scripts.oauth.make_server")
    def test_timeout_does_not_exchange_token(self, make_server):
        flow = MagicMock()
        flow.authorization_url.return_value = ("https://example.test/auth", "current")
        with patch("builtins.print"), self.assertRaises(TimeoutError):
            authorize(flow, timeout=0)
        flow.fetch_token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
