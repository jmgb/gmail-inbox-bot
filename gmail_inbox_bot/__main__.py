"""Entry point: ``uv run python -m gmail_inbox_bot``."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail Inbox Bot")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without executing them")
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    args = parser.parse_args()

    from .bot import run

    run(dry_run=args.dry_run, once=args.once)


if __name__ == "__main__":
    main()
