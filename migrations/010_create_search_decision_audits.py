"""Migration 010 — Persist per-run search decision audits."""

VERSION = '010'
DESCRIPTION = 'Create search run audit and job decision tables'


def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS search_run_audits (
            run_id       INTEGER PRIMARY KEY REFERENCES search_runs(id) ON DELETE CASCADE,
            config_json  TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS job_decisions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id        INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
            job_key       TEXT NOT NULL,
            job_json      TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            outcome       TEXT NOT NULL,
            score         REAL,
            UNIQUE(run_id, job_key)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_job_decisions_run_id ON job_decisions(run_id)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_job_decisions_outcome ON job_decisions(run_id, outcome)')
    db.commit()


def verify(db):
    tables = {
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('search_run_audits', 'job_decisions')"
        )
    }
    return tables == {'search_run_audits', 'job_decisions'}


def down(db):
    db.execute('DROP TABLE IF EXISTS job_decisions')
    db.execute('DROP TABLE IF EXISTS search_run_audits')
    db.commit()
