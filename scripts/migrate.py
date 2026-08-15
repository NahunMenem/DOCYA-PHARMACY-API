"""Apply pending SQL migrations. Run explicitly as a Railway pre-deploy command."""

from pathlib import Path
import sys

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings

MIGRATIONS = ROOT / "migrations"
LOCK_ID = 68025901


def main() -> None:
    settings = get_settings()
    settings.validate_runtime_secrets()
    conn = psycopg2.connect(settings.database_url, sslmode="require", connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
            cur.execute("CREATE SCHEMA IF NOT EXISTS pharmacy")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pharmacy.schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.commit()

            for path in sorted(MIGRATIONS.glob("*.sql")):
                cur.execute(
                    "SELECT 1 FROM pharmacy.schema_migrations WHERE version = %s",
                    (path.name,),
                )
                if cur.fetchone():
                    continue
                print(f"Applying {path.name}")
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute(
                    "INSERT INTO pharmacy.schema_migrations (version) VALUES (%s)",
                    (path.name,),
                )
                conn.commit()
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
