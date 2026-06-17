"""Run the private-beta cleanup policy.

Usage (from backend/, with the venv):
    .venv\\Scripts\\python.exe scripts\\cleanup.py

Cron-friendly: closes stale open sessions, deletes failed uploads and abandoned
empty sessions. NEVER deletes completed project outputs. See app/cleanup.py.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cleanup import run_cleanup  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        summary = run_cleanup(db)
    finally:
        db.close()
    print("Cleanup done:", summary)


if __name__ == "__main__":
    main()
