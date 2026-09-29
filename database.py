"""SQLite access for JobSearch.

Two accessors, deliberately kept separate:

* get_db()      — a NEW connection the caller must close. This is what the
                  pipeline uses: main.py runs as a detached subprocess with no
                  Flask context at all, and profile_store.py closes after every
                  operation.
* request_db()  — the connection for the current Flask request, cached on `g`
                  and closed by app.py's teardown handler.

Keeping them apart avoids a real footgun: profile_store's `finally: db.close()`
would close a request-scoped connection out from under the rest of the request
if both shared one accessor.

Two deliberate differences from the pre-re-platform database.py:

1. The path comes from config.py, so it follows JOBSEARCH_ENV
   (data/jobsearch_dev.db vs data/jobsearch.db). The old module hardcoded
   'jobsearch_dev.db' in every environment, which would have pointed
   production at a file that does not exist.
2. It does NOT create or patch schema. migrations/ owns the schema; a module
   that silently CREATE TABLEs behind the migration runner's back makes
   schema_diff.py and `migrate.py status` untrustworthy.
"""
import sqlite3
from pathlib import Path

from config import Config


def db_path():
    return Path(Config.get_config().DATABASE)


def _connect(path=None):
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target)
    db.row_factory = sqlite3.Row
    # SQLite defaults foreign_keys OFF per connection; the migrations declare
    # ON DELETE CASCADE relationships that silently do nothing without this.
    db.execute('PRAGMA foreign_keys = ON')
    return db


def get_db(path=None):
    """A fresh connection. The caller owns it and must close it."""
    return _connect(path)


def request_db():
    """The current request's connection. Closed by app.py's teardown."""
    from flask import g
    if 'db' not in g:
        g.db = _connect()
    return g.db


def close_request_db(exception=None):
    from flask import g
    db = g.pop('db', None)
    if db is not None:
        db.close()
