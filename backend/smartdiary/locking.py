"""Serialize derived-data writers across commits, without blocking raw record saves."""

import hashlib
import threading
from contextlib import contextmanager
from functools import wraps

from sqlalchemy import text

_guard = threading.Lock()
_locks = {}


@contextmanager
def entity_lock(db, key):
    if db.bind.dialect.name == "postgresql":
        number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        # A dedicated connection holds a session lock while model calls commit budget rows.
        with db.bind.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": number})
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": number})
    else:
        # Development SQLite uses one worker process. Production requires PostgreSQL.
        with _guard:
            lock = _locks.setdefault(key, threading.RLock())
        with lock:
            yield


def serialized(key):
    def decorate(function):
        @wraps(function)
        def run(db, *args, **kwargs):
            with entity_lock(db, key(db, *args, **kwargs)):
                return function(db, *args, **kwargs)

        return run

    return decorate
