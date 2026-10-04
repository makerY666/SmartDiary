"""Exercise migrations, an actual HTTP server and the worker against disposable data."""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def main():
    with tempfile.TemporaryDirectory(prefix="smartdiary-smoke-") as temporary:
        env = dict(
            os.environ,
            DATABASE_URL="sqlite:///" + (Path(temporary) / "smoke.db").as_posix(),
            DATA_DIR=temporary,
            AI_MODE="disabled",
            ENVIRONMENT="development",
            REGISTRATION_TOKEN="",
        )

        def command(*args, **kwargs):
            return subprocess.run(
                [sys.executable, *args],
                cwd=ROOT,
                env=env,
                creationflags=FLAGS,
                check=True,
                capture_output=True,
                timeout=60,
                **kwargs,
            )

        command("-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head")
        # Check that the production schema can also compile into PostgreSQL DDL.
        postgres_env = {
            **env,
            "DATABASE_URL": "postgresql+psycopg://diary@localhost/smartdiary_test",
        }
        ddl = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "backend/alembic.ini",
                "upgrade",
                "head",
                "--sql",
            ],
            cwd=ROOT,
            env=postgres_env,
            creationflags=FLAGS,
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
        assert b"VECTOR(1024)" in ddl and b"CREATE EXTENSION" in ddl
        # Bind a free loopback port, then pass it to Uvicorn.
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "smartdiary.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            creationflags=FLAGS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
                for _ in range(50):
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.ConnectError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("HTTP server did not start")
                auth = client.post(
                    "/v1/auth/register",
                    json={
                        "username": "smoke-person",
                        "password": "only-a-smoke-password",
                    },
                )
                auth.raise_for_status()
                client.headers["Authorization"] = "Bearer " + auth.json()["token"]
                record = {
                    "id": str(uuid.uuid4()),
                    "text": "今天在青竹咖啡馆写下了一个想法。",
                    "kind": "text",
                    "occurred_at": "2026-09-20T12:00:00+08:00",
                    "recorded_at": "2026-09-20T12:01:00+08:00",
                }
                saved = client.post("/v1/records", json=record)
                assert saved.status_code == 200 and saved.json()["version"] == 1
                assert client.post("/v1/records", json=record).json()["version"] == 1
                command("-m", "smartdiary.worker", "--once")
                answer = client.post("/v1/ask", json={"question": "青竹咖啡馆"}).json()
                assert answer["sources"][0]["record_id"] == record["id"]
                assert client.get("/v1/diaries").json()[0]["paragraphs"]
                assert client.get("/v1/export").content[:2] == b"PK"
                assert client.delete("/v1/records/" + record["id"]).status_code == 200
                assert client.post("/v1/ask", json={"question": "青竹咖啡馆"}).json()["sources"] == []
        finally:
            process.terminate()
            process.wait(timeout=10)
        command("-m", "alembic", "-c", "backend/alembic.ini", "downgrade", "base")
        command("-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head")
        print(
            json.dumps(
                {
                    "http_smoke": "passed",
                    "worker": "passed",
                    "sqlite_migrations_roundtrip": "passed",
                    "postgresql_ddl_compile": "passed",
                    "live_ai_calls": 0,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
