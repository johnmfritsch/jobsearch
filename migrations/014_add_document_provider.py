"""Migration 014 — Record the source/model for application documents."""

VERSION = '014'
DESCRIPTION = 'Record application document provider and model'


def _columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info({table})')}


def up(db):
    columns = _columns(db, 'application_artifacts')
    if 'provider' not in columns:
        db.execute("ALTER TABLE application_artifacts ADD COLUMN provider TEXT NOT NULL DEFAULT ''")
    if 'model' not in columns:
        db.execute("ALTER TABLE application_artifacts ADD COLUMN model TEXT NOT NULL DEFAULT ''")
    db.commit()


def verify(db):
    return {'provider', 'model'}.issubset(_columns(db, 'application_artifacts'))


def down(db):
    # SQLite cannot safely drop these columns on older NAS builds. They are
    # additive, nullable-in-practice metadata and can remain during rollback.
    pass
