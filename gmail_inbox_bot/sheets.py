"""Google Sheets client — read, insert, and write rows using OAuth2 credentials."""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("gmail_inbox_bot.sheets")

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class SheetsClient:
    """Lightweight Google Sheets client that reuses Gmail OAuth2 credentials.

    Parameters
    ----------
    client_id, client_secret, refresh_token:
        Same OAuth2 credentials used for Gmail (must include spreadsheets scope).
    spreadsheet_id:
        The ID from the Google Sheets URL.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        spreadsheet_id: str,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.spreadsheet_id = spreadsheet_id
        self._access_token: str | None = None
        self._http = httpx.Client(timeout=15.0)
        self._sheet_id_cache: dict[str, int] = {}

    def _refresh_access_token(self) -> str:
        resp = self._http.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        self._access_token = token
        return token

    def _headers(self) -> dict[str, str]:
        if not self._access_token:
            self._refresh_access_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Authenticated request with one retry on 401."""
        resp = self._http.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401:
            self._refresh_access_token()
            resp = self._http.request(method, url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        return resp

    def _get_sheet_id(self, sheet_name: str) -> int:
        """Get the numeric sheetId for a tab name (needed for batchUpdate)."""
        if sheet_name in self._sheet_id_cache:
            return self._sheet_id_cache[sheet_name]
        url = f"{SHEETS_API}/{self.spreadsheet_id}"
        resp = self._request("GET", url)
        for s in resp.json().get("sheets", []):
            name = s["properties"]["title"]
            sid = s["properties"]["sheetId"]
            self._sheet_id_cache[name] = sid
        if sheet_name not in self._sheet_id_cache:
            raise ValueError(f"Sheet tab '{sheet_name}' not found")
        return self._sheet_id_cache[sheet_name]

    def read_range(self, range_str: str) -> list[list[str]]:
        """Read a range and return rows as lists of strings."""
        url = f"{SHEETS_API}/{self.spreadsheet_id}/values/{range_str}"
        resp = self._request("GET", url)
        return resp.json().get("values", [])

    def append_row(self, values: list, *, sheet: str = "Sheet1") -> None:
        """Append a single row at the end of the sheet."""
        url = (
            f"{SHEETS_API}/{self.spreadsheet_id}/values/{sheet}!A:Z:append"
            f"?valueInputOption=USER_ENTERED"
            f"&insertDataOption=INSERT_ROWS"
        )
        body = {"values": [values]}
        self._request("POST", url, json=body)
        log.info("Appended row to %s: %s", sheet, values)

    def insert_row_at(self, row_index: int, values: list, *, sheet: str = "Resumen") -> None:
        """Insert a blank row at row_index (1-based) and write values into it.

        Steps:
        1. Insert an empty row via batchUpdate (insertDimension).
        2. Write values into the newly inserted row via values.update.
        """
        sheet_id = self._get_sheet_id(sheet)

        # Insert empty row (API uses 0-based index)
        insert_url = f"{SHEETS_API}/{self.spreadsheet_id}:batchUpdate"
        insert_body = {
            "requests": [
                {
                    "insertDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": row_index - 1,
                            "endIndex": row_index,
                        },
                        "inheritFromBefore": True,
                    }
                }
            ]
        }
        self._request("POST", insert_url, json=insert_body)

        # Write values into the new row
        write_url = (
            f"{SHEETS_API}/{self.spreadsheet_id}/values/{sheet}!A{row_index}"
            f"?valueInputOption=USER_ENTERED"
        )
        self._request("PUT", write_url, json={"values": [values]})
        log.info("Inserted row at %s!A%d: %s", sheet, row_index, values)

    def find_insert_row(self, *, sheet: str = "Resumen", search_from: int = 43) -> int:
        """Find the row to insert before: first non-empty row after search_from.

        Scans column A from search_from downward. Returns the row number of the
        first cell with content, so the caller can insert just above it.
        """
        range_str = f"{sheet}!A{search_from}:A200"
        rows = self.read_range(range_str)
        for i, row in enumerate(rows):
            if row and row[0].strip():
                return search_from + i
        # No content found — insert at search_from
        return search_from


def build_sheets_client(
    env: dict[str, str],
    refresh_token: str,
    spreadsheet_id: str,
) -> SheetsClient | None:
    """Build a SheetsClient if a spreadsheet_id is configured."""
    if not spreadsheet_id:
        return None
    client_id = env.get("GOOGLE_CLIENT_ID", os.environ.get("GOOGLE_CLIENT_ID", ""))
    client_secret = env.get("GOOGLE_CLIENT_SECRET", os.environ.get("GOOGLE_CLIENT_SECRET", ""))
    if not client_id or not client_secret:
        log.warning("Missing GOOGLE_CLIENT_ID/SECRET — Sheets disabled")
        return None
    return SheetsClient(client_id, client_secret, refresh_token, spreadsheet_id)
