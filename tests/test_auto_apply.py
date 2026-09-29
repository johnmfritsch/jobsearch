import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import auto_apply


ROOT = Path(__file__).resolve().parents[1]


def load_migration(version):
    path = next((ROOT / 'migrations').glob(f'{version}_*.py'))
    spec = importlib.util.spec_from_file_location(f'migration_{version}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AutoApplyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, profile_key TEXT);
            CREATE TABLE user_profiles (
                user_id INTEGER PRIMARY KEY, resume_text TEXT,
                updated_at TEXT
            );
            CREATE TABLE search_runs (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                test_mode INTEGER NOT NULL, state TEXT NOT NULL,
                status_json TEXT NOT NULL, started_at TEXT NOT NULL
            );
            CREATE TABLE job_results (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL,
                job_key TEXT NOT NULL, job_json TEXT NOT NULL, score REAL
            );
            CREATE TABLE application_records (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                job_key TEXT NOT NULL, record_json TEXT NOT NULL,
                updated_at TEXT NOT NULL, UNIQUE(user_id,job_key)
            );
            CREATE TABLE search_presets (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                name TEXT NOT NULL, resume_text TEXT NOT NULL DEFAULT '',
                color TEXT, config_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO users(id,profile_key) VALUES (1,'one'),(2,'two');
            INSERT INTO search_runs(id,user_id,test_mode,state,status_json,started_at)
                VALUES (10,1,0,'complete','{}','2026-08-10'),
                       (20,2,0,'complete','{}','2026-08-10');
        ''')
        job = {'title': 'Integration Engineer', 'company': 'Example',
               'apply_url': 'https://jobs.ashbyhq.com/example/123',
               'description': 'Build HL7 integrations.'}
        self.db.execute('INSERT INTO job_results(run_id,job_key,job_json,score) '
                        'VALUES (10,?,?,.8)', ('job-1', json.dumps(job)))
        self.db.commit()
        load_migration('013').up(self.db)
        load_migration('014').up(self.db)
        load_migration('015').up(self.db)

    def tearDown(self):
        self.db.close()

    def test_attempt_is_idempotent_and_user_scoped(self):
        self.db.execute(
            'INSERT INTO application_records(user_id,job_key,record_json,updated_at) '
            'VALUES (1,?,?,?)',
            ('job-1', json.dumps({'stage': 'interviewing', 'status': 'applied',
                                  'notes': 'Preserve me'}), '2026-08-10'))
        self.db.commit()
        first, created = auto_apply.create_attempt(self.db, 1, 'job-1')
        second, created_again = auto_apply.create_attempt(self.db, 1, 'job-1')
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first['id'], second['id'])
        self.assertIsNone(auto_apply.get_attempt(self.db, 2, first['id']))
        self.assertEqual(first['ats_kind'], 'ashby')
        self.assertFalse(first['automation_used'])
        tracker = json.loads(self.db.execute(
            'SELECT record_json FROM application_records WHERE user_id=1 AND job_key=?',
            ('job-1',)).fetchone()['record_json'])
        self.assertEqual(tracker['notes'], 'Preserve me')
        self.assertEqual(tracker['stage'], 'interviewing')

    def test_profile_whitelists_and_limits_fields(self):
        result = auto_apply.save_profile(
            self.db, 1, {'full_name': 'A' * 400, 'gender': 'must not store'})
        self.assertEqual(len(result['profile']['full_name']), 160)
        self.assertNotIn('gender', result['profile'])

    def test_transition_history_and_confirmation_token_single_use(self):
        attempt, _ = auto_apply.create_attempt(self.db, 1, 'job-1')
        auto_apply.transition(self.db, 1, attempt['id'], 'preparing_documents')
        auto_apply.transition(self.db, 1, attempt['id'], 'awaiting_document_approval')
        auto_apply.transition(self.db, 1, attempt['id'], 'ready_for_review')
        token, _ = auto_apply.create_confirmation_token(self.db, 1, attempt['id'])
        self.assertTrue(auto_apply.consume_confirmation_token(
            self.db, 1, attempt['id'], token))
        self.assertFalse(auto_apply.consume_confirmation_token(
            self.db, 1, attempt['id'], token))
        loaded = auto_apply.get_attempt(self.db, 1, attempt['id'])
        self.assertGreaterEqual(len(loaded['events']), 5)

    def test_artifact_path_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            safe = auto_apply.safe_artifact_path(root, '1/attempt/resume.pdf')
            self.assertTrue(str(safe).startswith(str(root)))
            with self.assertRaises(ValueError):
                auto_apply.safe_artifact_path(root, '../secret_key.txt')

    def test_aggregator_url_is_labeled_as_job_listing(self):
        self.assertEqual(auto_apply.application_url_kind(
            'https://www.ziprecruiter.com/jobs/example',
            'https://www.ziprecruiter.com/jobs/example'), 'job_listing')
        self.assertEqual(auto_apply.application_url_kind(
            'https://example.com/job',
            'https://boards.greenhouse.io/example/jobs/1'), 'application_page')

    def test_approving_docx_selects_pdf_and_ready_requires_both_documents(self):
        attempt, _ = auto_apply.create_attempt(self.db, 1, 'job-1')
        auto_apply.transition(self.db, 1, attempt['id'], 'preparing_documents')
        auto_apply.transition(self.db, 1, attempt['id'], 'awaiting_document_approval')
        usage = {}
        resume_docx = auto_apply.add_artifact(
            self.db, 1, attempt['id'], 'resume', 'docx', 'resume.docx',
            '1/a/v1/resume.docx', 'a', 'docx', 1, usage)
        resume_pdf = auto_apply.add_artifact(
            self.db, 1, attempt['id'], 'resume', 'pdf', 'resume.pdf',
            '1/a/v1/resume.pdf', 'b', 'pdf', 1, usage)
        cover_docx = auto_apply.add_artifact(
            self.db, 1, attempt['id'], 'cover_letter', 'docx', 'cover.docx',
            '1/a/v1/cover.docx', 'c', 'docx', 1, usage)
        cover_pdf = auto_apply.add_artifact(
            self.db, 1, attempt['id'], 'cover_letter', 'pdf', 'cover.pdf',
            '1/a/v1/cover.pdf', 'd', 'pdf', 1, usage)
        self.db.commit()
        after_resume = auto_apply.approve_artifact(
            self.db, 1, resume_docx, 'approved')
        self.assertEqual(after_resume['selected_resume_id'], resume_pdf)
        self.assertEqual(after_resume['state'], 'awaiting_document_approval')
        after_cover = auto_apply.approve_artifact(
            self.db, 1, cover_docx, 'approved')
        self.assertEqual(after_cover['selected_cover_id'], cover_pdf)
        self.assertEqual(after_cover['state'], 'ready_for_review')

    def test_saved_role_resume_can_be_ready_without_cover_letter(self):
        attempt, _ = auto_apply.create_attempt(self.db, 1, 'job-1')
        auto_apply.transition(self.db, 1, attempt['id'], 'preparing_documents')
        resume_pdf = auto_apply.add_artifact(
            self.db, 1, attempt['id'], 'resume', 'pdf', 'resume.pdf',
            '1/a/v1/resume.pdf', 'b', 'pdf', 1,
            {'provider': 'saved_role'}, 'approved')
        self.db.execute(
            'UPDATE application_attempts SET selected_resume_id=? WHERE id=?',
            (resume_pdf, attempt['id']))
        self.db.commit()
        ready = auto_apply.transition(
            self.db, 1, attempt['id'], 'ready_for_review')
        self.assertEqual(ready['selected_resume_id'], resume_pdf)
        self.assertIsNone(ready['selected_cover_id'])

    def test_could_not_fill_can_start_a_fresh_retry(self):
        attempt, _ = auto_apply.create_attempt(self.db, 1, 'job-1')
        auto_apply.transition(self.db, 1, attempt['id'], 'preparing_documents')
        auto_apply.transition(self.db, 1, attempt['id'], 'could_not_fill')
        retried, created = auto_apply.retry_attempt(self.db, 1, attempt['id'])
        self.assertTrue(created)
        self.assertNotEqual(retried['id'], attempt['id'])


if __name__ == '__main__':
    unittest.main()
