"""Google Sheets client — append rows using OAuth2 credentials (same as Gmail)."""

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

    def append_row(self, values: list, *, sheet: str = "Sheet1") -> None:
        """Append a single row to the specified sheet.

        Parameters
        ----------
        values:
            List of cell values (strings, numbers) for one row.
        sheet:
            Tab name within the spreadsheet (default: "Sheet1").
        """
        url = (
            f"{SHEETS_API}/{self.spreadsheet_id}/values/{sheet}!A:Z:append"
            f"?valueInputOption=USER_ENTERED"
            f"&insertDataOption=INSERT_ROWS"
        )
        body = {"values": [values]}
        self._request("POST", url, json=body)
        log.info("Appended row to %s: %s", sheet, values)


def build_sheets_client(
    env: dict[str, str],
    refresh_token: str,
    spreadsheet_id: str,
) -> SheetsClient | None:
    """Build a SheetsClient if a spreadsheet_id is configured."""
    if not spreadsheet_id:
        return None
    client_id = env.get("GOOGLE_CLIENT_ID", os.environ.get("GOOGLE_CLIENT_ID", ""))
    client_secret = env.get(
        "GOOGLE_CLIENT_SECRET", os.environ.get("GOOGLE_CLIENT_SECRET", "")
    )
    if not client_id or not client_secret:
        log.warning("Missing GOOGLE_CLIENT_ID/SECRET — Sheets disabled")
        return None
    return SheetsClient(client_id, client_secret, refresh_token, spreadsheet_id)
