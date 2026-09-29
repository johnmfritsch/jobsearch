"""Migration 015 — Versioned resumes and cover letters for search roles."""

VERSION = '015'
DESCRIPTION = 'Create versioned role document library'


def up(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS role_documents (
            id             TEXT PRIMARY KEY,
            user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            preset_id      INTEGER REFERENCES search_presets(id) ON DELETE SET NULL,
            role_key       TEXT NOT NULL,
            role_name      TEXT NOT NULL,
            kind           TEXT NOT NULL CHECK(kind IN ('resume','cover_letter')),
            format         TEXT NOT NULL CHECK(format IN ('txt','pdf')),
            version        INTEGER NOT NULL,
            is_current     INTEGER NOT NULL DEFAULT 1,
            source         TEXT NOT NULL,
            file_name      TEXT,
            relative_path  TEXT,
            sha256         TEXT,
            mime_type      TEXT,
            content_text   TEXT,
            created_at     TEXT NOT NULL,
            superseded_at  TEXT,
            UNIQUE(user_id, role_key, kind, format, version)
        );
        CREATE INDEX IF NOT EXISTS idx_role_documents_current
            ON role_documents(user_id, role_key, kind, format, is_current);
        CREATE INDEX IF NOT EXISTS idx_role_documents_preset
            ON role_documents(user_id, preset_id, created_at DESC);
    ''')
    # Preserve every existing role's ATS text as version 1. New saves create
    # later versions only when the content actually changes.
    db.execute('''
        INSERT OR IGNORE INTO role_documents
            (id,user_id,preset_id,role_key,role_name,kind,format,version,
             is_current,source,content_text,created_at)
        SELECT lower(hex(randomblob(16))),user_id,id,'preset:' || id,name,
               'resume','txt',1,1,'search_setup',resume_text,updated_at
        FROM search_presets
        WHERE trim(COALESCE(resume_text,'')) <> ''
    ''')
    db.execute('''
        INSERT OR IGNORE INTO role_documents
            (id,user_id,preset_id,role_key,role_name,kind,format,version,
             is_current,source,content_text,created_at)
        SELECT lower(hex(randomblob(16))),user_id,NULL,'default','Default',
               'resume','txt',1,1,'search_setup',resume_text,updated_at
        FROM user_profiles
        WHERE trim(COALESCE(resume_text,'')) <> ''
    ''')
    db.commit()


def verify(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(role_documents)')}
    return {'role_key', 'kind', 'format', 'version', 'is_current',
            'relative_path', 'content_text'}.issubset(columns)


def down(db):
    db.execute('DROP TABLE IF EXISTS role_documents')
    db.commit()
