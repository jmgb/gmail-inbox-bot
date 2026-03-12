"""Obtain a Gmail OAuth2 refresh token.

Usage:
    uv run python scripts/get_refresh_token.py [client_secret_file]

Generates an authorization URL with localhost redirect.
After authorizing, copy the code= parameter from the browser URL bar.
"""

import json
import sys
import urllib.parse
from pathlib import Path

import httpx

SCOPE = "https://www.googleapis.com/auth/gmail.modify"
REDIRECT_URI = "http://localhost"


def find_client_secret(path_arg: str | None = None) -> Path:
    if path_arg:
        p = Path(path_arg)
        if not p.exists():
            print(f"ERROR: No se encontró {p}")
            sys.exit(1)
        return p

    matches = list(Path(".").glob("client_secret_*.json"))
    if not matches:
        print("ERROR: No se encontró client_secret_*.json en el directorio actual.")
        print("Pasa la ruta como argumento: python scripts/get_refresh_token.py /ruta/al/archivo.json")
        sys.exit(1)
    return matches[0]


def main() -> None:
    path_arg = sys.argv[1] if len(sys.argv) > 1 else None
    secrets_file = find_client_secret(path_arg)

    with open(secrets_file) as f:
        creds = json.load(f)["installed"]

    client_id = creds["client_id"]
    client_secret = creds["client_secret"]

    # Step 1: Generate auth URL
    auth_params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    auth_url = f"https://accounts.google.com/o/oauth2/auth?{auth_params}"

    print(f"\nUsando: {secrets_file.name}")
    print(f"Scope:  {SCOPE}\n")
    print("1. Abre esta URL en tu navegador:\n")
    print(auth_url)
    print("\n2. Autoriza la app. El navegador redirigirá a localhost (no cargará).")
    print("   Copia el valor de code= de la barra de direcciones.\n")

    code = input("3. Pega el código aquí: ").strip()
    if not code:
        print("ERROR: No se proporcionó código.")
        sys.exit(1)

    # Step 2: Exchange code for tokens
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )

    if resp.status_code != 200:
        print(f"\nERROR {resp.status_code}: {resp.text}")
        sys.exit(1)

    tokens = resp.json()
    refresh_token = tokens.get("refresh_token")

    if not refresh_token:
        print("\nERROR: No se recibió refresh_token.")
        print(f"Respuesta: {tokens}")
        sys.exit(1)

    # Get account email
    profile = httpx.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    ).json()
    email = profile.get("emailAddress", "desconocido")

    print(f"\n=== Token obtenido para {email} ===\n")
    print(f"GOOGLE_REFRESH_TOKEN={refresh_token}")
    print(f"\nAñade al .env con un nombre descriptivo:")
    print(f"GOOGLE_REFRESH_TOKEN_{email.split('@')[0].upper()}={refresh_token}")


if __name__ == "__main__":
    main()
