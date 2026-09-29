import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

import role_documents


ROOT = Path(__file__).resolve().parents[1]


def migration_015():
    path = ROOT / 'migrations' / '015_create_role_documents.py'
    spec = importlib.util.spec_from_file_location('migration_015', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RoleDocumentTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            CREATE TABLE user_profiles (
                user_id INTEGER PRIMARY KEY, resume_text TEXT, updated_at TEXT);
            CREATE TABLE search_presets (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                name TEXT NOT NULL, resume_text TEXT, updated_at TEXT);
            INSERT INTO users(id) VALUES (1),(2);
            INSERT INTO user_profiles VALUES (1,'Default resume','2026-08-10');
            INSERT INTO user_profiles VALUES (2,'Other resume','2026-08-10');
            INSERT INTO search_presets VALUES
                (10,1,'HL7 Integration','Built HL7 interfaces','2026-08-10');
        ''')
        migration_015().up(self.db)

    def tearDown(self):
        self.db.close()

    def test_text_versions_only_change_when_content_changes(self):
        first = role_documents.sync_resume_text(
            self.db, 1, 10, 'HL7 Integration', 'Built HL7 interfaces')
        same = role_documents.sync_resume_text(
            self.db, 1, 10, 'HL7 Integration', 'Built HL7 interfaces')
        second = role_documents.sync_resume_text(
            self.db, 1, 10, 'HL7 Integration', 'Built HL7 and FHIR interfaces')
        self.assertEqual(first['id'], same['id'])
        self.assertEqual(second['version'], 2)
        self.assertEqual(self.db.execute(
            'SELECT COUNT(*) FROM role_documents WHERE role_key=? '
            'AND format=? AND is_current=1',
            ('preset:10', 'txt')).fetchone()[0], 1)

    def test_pdf_replacement_versions_and_is_user_scoped(self):
        role = role_documents.resolve_role(self.db, 1, 10)
        with tempfile.TemporaryDirectory() as directory:
            database_path = str(Path(directory) / 'jobsearch.db')
            first = role_documents.save_pdf(
                self.db, database_path, 1, role, 'resume', b'%PDF-1.4\none',
                'resume.pdf')
            second = role_documents.save_pdf(
                self.db, database_path, 1, role, 'resume', b'%PDF-1.4\ntwo',
                'resume.pdf')
            self.assertEqual(second['version'], 2)
            self.assertFalse(role_documents.get_document(
                self.db, 2, second['id']))
            listed = role_documents.list_documents(self.db, 1, role)['documents']
            self.assertEqual(sum(item['is_current'] for item in listed
                                 if item['format'] == 'pdf'), 1)
            self.assertEqual(first['is_current'], 1)


if __name__ == '__main__':
    unittest.main()
