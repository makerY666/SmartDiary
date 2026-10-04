"""Encrypted PostgreSQL backup to OSS. Run daily from the host scheduler."""

import argparse
import os
import subprocess
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from smartdiary.config import settings
from smartdiary.storage import bucket, encryption


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--restore",
        help="OSS object key; reapply independent deletion ledger after restore",
    )
    args = parser.parse_args()
    parsed = urlsplit(settings.database_url.replace("postgresql+psycopg", "postgresql"))
    if parsed.scheme != "postgresql" or settings.storage_backend != "oss":
        raise SystemExit("Requires PostgreSQL, OSS and an explicit ENCRYPTION_KEY")
    if not settings.encryption_key:
        raise SystemExit("ENCRYPTION_KEY is required for durable backups")
    env = dict(
        os.environ,
        PGHOST=parsed.hostname or "localhost",
        PGPORT=str(parsed.port or 5432),
        PGUSER=unquote(parsed.username or ""),
        PGPASSWORD=unquote(parsed.password or ""),
        PGDATABASE=parsed.path.lstrip("/"),
    )
    if args.restore:
        data = encryption().decrypt(bucket().get_object(args.restore).read())
        subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--dbname",
                env["PGDATABASE"],
            ],
            input=data,
            env=env,
            check=True,
            capture_output=True,
        )
        # OSS deletion markers are independent of the database backup and never rolled back.
        import oss2

        from smartdiary.db import SessionLocal
        from smartdiary.domain import erase_record
        from smartdiary.models import Record, User

        with SessionLocal() as db:
            for obj in oss2.ObjectIterator(bucket(), prefix="deletion-ledger/"):
                _, user_id, record_id = obj.key.split("/")
                user, record = db.get(User, user_id), db.get(Record, record_id)
                if user and record and not record.deleted:
                    erase_record(db, user, record_id)
        print("Restored database and reapplied independent deletion ledger")
    else:
        dump = subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner"],
            env=env,
            check=True,
            capture_output=True,
        ).stdout
        key = "backups/" + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ") + ".dump.enc"
        bucket().put_object(
            key,
            encryption().encrypt(dump),
            headers={"x-oss-server-side-encryption": "AES256"},
        )
        print("Encrypted backup saved: " + key)


if __name__ == "__main__":
    main()
