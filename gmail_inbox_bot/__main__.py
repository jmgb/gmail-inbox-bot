"""Entry point: ``uv run python -m gmail_inbox_bot``."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail Inbox Bot")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without executing them")
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    parser.add_argument(
        "--server",
        action="store_true",
        help="Run FastAPI web server with admin UI + bot polling in background",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port for web server (default: 8000)"
    )
    args = parser.parse_args()

    if args.server:
        import uvicorn

        uvicorn.run(
            "gmail_inbox_bot.app:app",
            host="0.0.0.0",
            port=args.port,
            log_level="info",
        )
    else:
        from .bot import run

        run(dry_run=args.dry_run, once=args.once)


if __name__ == "__main__":
    main()
