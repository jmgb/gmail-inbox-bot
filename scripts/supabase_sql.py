"""Execute SQL against the Supabase database via Management API.

Usage:
    uv run python scripts/supabase_sql.py "SELECT count(*) FROM email_metrics"
    uv run python scripts/supabase_sql.py "ALTER TABLE ... ADD COLUMN ..."
    echo "SELECT 1" | uv run python scripts/supabase_sql.py

Requires SUPABASE_ACCESS_TOKEN and SUPABASE_PROJECT_REF in .env
"""

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()


def execute_sql(sql: str) -> None:
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    ref = os.environ.get("SUPABASE_PROJECT_REF", "")

    if not token or not ref:
        print("Error: SUPABASE_ACCESS_TOKEN y SUPABASE_PROJECT_REF requeridos en .env")
        sys.exit(1)

    resp = httpx.post(
        f"https://api.supabase.com/v1/projects/{ref}/database/query",
        json={"query": sql},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    if resp.status_code == 201:
        data = resp.json()
        if data:
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                cols = list(data[0].keys())
                print(" | ".join(cols))
                print("-" * (sum(len(c) for c in cols) + 3 * (len(cols) - 1)))
                for row in data:
                    print(" | ".join(str(row.get(c, "")) for c in cols))
            else:
                print(data)
        else:
            print("OK (no rows returned)")
    else:
        print(f"Error {resp.status_code}: {resp.text[:500]}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sql = " ".join(sys.argv[1:])
    elif not sys.stdin.isatty():
        sql = sys.stdin.read().strip()
    else:
        print(__doc__)
        sys.exit(1)

    execute_sql(sql)
