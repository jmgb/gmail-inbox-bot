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


class TestFindInsertRow:
    def test_finds_first_non_empty_row(self):
        client = SheetsClient("cid", "csecret", "rtoken", "sheet123")
        client._access_token = "fake-token"
        client._http = MagicMock()

        # Simulate: rows 43-53 empty, row 54 has "CODX"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "values": [
                [],  # row 43
                [],  # row 44
                [],  # ...
                [], [], [], [], [], [], [], [],
                ["CODX"],  # row 54 (index 11)
            ]
        }
        client._http.request.return_value = mock_resp

        result = client.find_insert_row(sheet="Resumen", search_from=43)
        assert result == 54

    def test_returns_search_from_when_all_empty(self):
        client = SheetsClient("cid", "csecret", "rtoken", "sheet123")
        client._access_token = "fake-token"
        client._http = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"values": []}
        client._http.request.return_value = mock_resp

        result = client.find_insert_row(sheet="Resumen", search_from=43)
        assert result == 43


class TestInsertRowAt:
    def test_calls_batch_update_then_values_update(self):
        client = SheetsClient("cid", "csecret", "rtoken", "sheet123")
        client._access_token = "fake-token"
        client._sheet_id_cache = {"Resumen": 0}
        client._http = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        client._http.request.return_value = mock_resp

        client.insert_row_at(54, ["VEEA", 1511, 0.5722], sheet="Resumen")

        assert client._http.request.call_count == 2
        # First call: batchUpdate (insert row)
        first_call = client._http.request.call_args_list[0]
        assert first_call[0][0] == "POST"
        assert "batchUpdate" in first_call[0][1]
        # Second call: values update (write data)
        second_call = client._http.request.call_args_list[1]
        assert second_call[0][0] == "PUT"
        assert "A54" in second_call[0][1]


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
