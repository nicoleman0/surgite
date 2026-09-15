"""Serve the built SPA over HTTPS against a disposable PostgreSQL database."""

import os
import signal
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main():
    # Uvicorn replays SIGTERM after shutdown; unwind the database cleanup below.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    # Requires a test PostgreSQL role with CREATEDB. Never reuse application data.
    server_url = make_url(os.environ["E2E_DATABASE_URL"])
    database = f"surgite_e2e_{uuid4().hex}"
    server = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
    try:
        with TemporaryDirectory(prefix="surgite-auth-e2e-") as temp:
            os.environ.update(
                DATABASE_URL=server_url.set(database=database).render_as_string(
                    hide_password=False
                ),
                PYTHON_DOTENV_DISABLED="1",
                AUTH_MODE="multi_user",
                DEBUG="false",
                BOOTSTRAP_OWNER_EMAIL="admin@example.test",
                INGEST_INTERVAL="0",
                SECRETS_KEY_FILE=f"{temp}/secrets.key",
                ANTHROPIC_API_KEY="",
                GROQ_API_KEY="",
                DEEPSEEK_API_KEY="",
                LOCAL_API_KEY="",
                LOCAL_BASE_URL="",
            )
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
            from surgite.auth import bootstrap_admin

            bootstrap_admin("admin@example.test", "admin-e2e-password")
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    f"{temp}/key.pem",
                    "-out",
                    f"{temp}/cert.pem",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=localhost",
                ],
                check=True,
                capture_output=True,
            )
            import uvicorn

            uvicorn.run(
                "surgite.api:app",
                host="127.0.0.1",
                port=8443,
                ssl_keyfile=f"{temp}/key.pem",
                ssl_certfile=f"{temp}/cert.pem",
                access_log=False,
            )
    finally:
        with server.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
        server.dispose()


if __name__ == "__main__":
    main()
