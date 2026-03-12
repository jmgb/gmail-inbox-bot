"""Tests for Google Sheets client."""

from unittest.mock import MagicMock

from gmail_inbox_bot.sheets import SheetsClient, build_sheets_client


class TestSheetsClient:
    def test_append_row_calls_api(self):
        client = SheetsClient("cid", "csecret", "rtoken", "sheet123")
        client._access_token = "fake-token"
        client._http = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        client._http.request.return_value = mock_resp

        client.append_row(["2026-03-12", "BUY", "AAPL", 100, 150.0, 15000.0, "U123"])

        client._http.request.assert_called_once()
        call_args = client._http.request.call_args
        assert call_args[0][0] == "POST"
        assert "sheet123" in call_args[0][1]
        assert "append" in call_args[0][1]

    def test_append_row_custom_tab(self):
        client = SheetsClient("cid", "csecret", "rtoken", "sheet123")
        client._access_token = "fake-token"
        client._http = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        client._http.request.return_value = mock_resp

        client.append_row(["data"], sheet="Trades")

        url = client._http.request.call_args[0][1]
        assert "Trades" in url


class TestBuildSheetsClient:
    def test_returns_client_when_configured(self):
        env = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "csecret"}
        client = build_sheets_client(env, "rtoken", "sheet123")
        assert isinstance(client, SheetsClient)

    def test_returns_none_without_spreadsheet_id(self):
        env = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "csecret"}
        assert build_sheets_client(env, "rtoken", "") is None

    def test_returns_none_without_credentials(self):
        assert build_sheets_client({}, "rtoken", "sheet123") is None
