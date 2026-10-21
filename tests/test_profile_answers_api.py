"""HTTP-level checks for backlog 19.1: profile routes, role history routes, and
that nothing sensitive reaches an AI provider."""
import importlib.util
import json
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

import api
import auto_apply

ROOT = Path(__file__).resolve().parents[1]


def load_migration(version):
    path = next((ROOT / 'migrations').glob(f'{version}_*.py'))
    spec = importlib.util.spec_from_file_location(f'migration_{version}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SENSITIVE = {'screening': {'us_person': 'yes', 'notes': 'private note'},
             'eeo': {'opt_in': True, 'gender': 'male', 'race_ethnicity': 'white'}}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, profile_key TEXT);
            CREATE TABLE user_profiles (user_id INTEGER PRIMARY KEY, resume_text TEXT, updated_at TEXT);
            CREATE TABLE search_presets (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
                resume_text TEXT NOT NULL DEFAULT '', color TEXT,
                config_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '');
            CREATE TABLE search_runs (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                test_mode INTEGER NOT NULL, state TEXT NOT NULL, status_json TEXT NOT NULL,
                started_at TEXT NOT NULL);
            CREATE TABLE job_results (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL,
                job_key TEXT NOT NULL, job_json TEXT NOT NULL, score REAL);
            CREATE TABLE application_records (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                job_key TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(user_id,job_key));
            INSERT INTO users(id,profile_key) VALUES (1,'one'),(2,'two');
            INSERT INTO search_presets(id,user_id,name,resume_text) VALUES
                (10,1,'Sysadmin','Dev | Acme\nJan 2015 - Jun 2018'),(11,1,'Dev','x'),(20,2,'Other','y');
        ''')
        for version in ('013', '014', '015', '022'):
            load_migration(version).up(self.db)
        app = Flask(__name__)
        app.config['DATABASE'] = ':memory:'
        app.register_blueprint(api.api_bp)
        self.client = app.test_client()
        self.user = {'id': 1, 'profile_key': 'one'}
        for patcher in (
                mock.patch.object(api, 'current_user', side_effect=lambda: self.user),
                mock.patch.object(api, 'request_db', return_value=self.db)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_profile_sections_round_trip_through_the_routes(self):
        response = self.client.post('/api/application_profile', json={
            'profile': dict(SENSITIVE, full_name='A')})
        self.assertEqual(response.status_code, 200)
        loaded = self.client.get('/api/application_profile').get_json()['profile']
        self.assertEqual(loaded['eeo']['gender'], 'male')
        self.assertEqual(loaded['screening']['us_person'], 'yes')
        bad = self.client.post('/api/application_profile', json={
            'profile': {'eeo': {'opt_in': True, 'gender': 'robot'}}})
        self.assertEqual(bad.status_code, 400)

    def test_ai_document_routes_never_receive_sensitive_profile_data(self):
        auto_apply.save_profile(self.db, 1, dict(SENSITIVE, full_name='A', email='a@b.c'))
        seen = []
        with mock.patch.object(api.auto_apply, 'get_attempt', return_value={'job': {}, 'state': 'queued'}), \
                mock.patch.object(api, '_resume_for_attempt', return_value=('resume text', 'Default')), \
                mock.patch.object(api.document_generator, 'estimate_cost',
                                  side_effect=lambda r, j, profile, *a, **k: seen.append(profile) or {}), \
                mock.patch.object(api.document_generator, 'generate_documents',
                                  side_effect=lambda *a, **k: seen.append(a[7]) or {'id': 'x'}), \
                mock.patch.object(api, '_provider_key', return_value='k'):
            self.client.get('/api/application_attempts/abc/documents/estimate')
            self.client.post('/api/application_attempts/abc/documents/generate',
                             json={'provider': 'openai'})
        self.assertEqual(len(seen), 2)
        for profile in seen:
            text = json.dumps(profile)
            for leaked in ('male', 'white', 'private note', 'screening', 'eeo'):
                self.assertNotIn(leaked, text)
            self.assertEqual(profile['full_name'], 'A')

    def test_role_history_save_get_and_errors(self):
        rows = [{'title': 'Admin', 'employer': 'Acme', 'from': '01/2015', 'to': '06/2018'}]
        saved = self.client.post('/api/role_history', json={
            'preset_id': 10, 'history': {'work_history': rows, 'education': []}})
        self.assertEqual(saved.status_code, 200)
        got = self.client.get('/api/role_history?preset_id=10').get_json()['history']
        self.assertEqual(got['work_history'][0]['employer'], 'Acme')
        self.assertEqual(got['role']['role_key'], 'preset:10')
        bad = self.client.post('/api/role_history', json={
            'preset_id': 10, 'history': {'work_history': [dict(rows[0], **{'from': 'x'})]}})
        self.assertEqual(bad.status_code, 400)
        self.assertIn('MM/YYYY', bad.get_json()['error'])
        self.assertEqual(self.client.get('/api/role_history?preset_id=999').status_code, 404)
        self.assertEqual(self.client.get('/api/role_history?role_name=Nonexistent').status_code, 404)
        self.assertEqual(self.client.get('/api/role_history').status_code, 200)  # Default role

    def test_another_users_role_is_not_reachable(self):
        self.assertEqual(self.client.get('/api/role_history?preset_id=20').status_code, 404)
        self.assertEqual(self.client.post('/api/role_history', json={
            'preset_id': 20, 'history': {'work_history': []}}).status_code, 404)

    def test_signed_out_requests_are_rejected(self):
        self.user = None
        self.assertEqual(self.client.get('/api/role_history').status_code, 401)
        self.assertEqual(self.client.post('/api/role_history/undo', json={}).status_code, 401)

    def test_import_copy_preview_and_undo_routes(self):
        with mock.patch.object(api, '_resume_for_role_name',
                               return_value=('Dev | Acme\nJan 2015 - Jun 2018', 'Sysadmin')):
            draft = self.client.post('/api/role_history/import_resume', json={'preset_id': 10})
        self.assertEqual(draft.status_code, 200)
        self.assertEqual(draft.get_json()['draft']['work_history'][0]['employer'], 'Acme')
        self.assertEqual(self.client.get('/api/role_history?preset_id=10').get_json()
                         ['history']['work_history'], [])  # drafts are never saved
        rows = [{'title': 'Admin', 'employer': 'Acme', 'from': '01/2015', 'to': '06/2018'}]
        self.client.post('/api/role_history', json={'preset_id': 10, 'history': {'work_history': rows}})
        preview = self.client.post('/api/role_history/copy_preview', json={
            'preset_id': 11, 'source_preset_id': 10, 'mode': 'merge'})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.get_json()['preview']['work_history'][0]['status'], 'new')
        self.assertEqual(self.client.get('/api/role_history?preset_id=11').get_json()
                         ['history']['work_history'], [])
        self.assertEqual(self.client.post('/api/role_history/copy_preview', json={
            'preset_id': 10, 'source_preset_id': 10, 'mode': 'merge'}).status_code, 400)
        self.client.post('/api/role_history', json={'preset_id': 10, 'history': {'work_history': []}})
        undone = self.client.post('/api/role_history/undo', json={'preset_id': 10})
        self.assertEqual(undone.status_code, 200)
        self.assertEqual(undone.get_json()['history']['work_history'][0]['employer'], 'Acme')
        self.assertEqual(self.client.post('/api/role_history/undo', json={'preset_id': 11}).status_code, 404)

    def test_deleting_a_preset_removes_its_history(self):
        rows = [{'title': 'Admin', 'employer': 'Acme', 'from': '01/2015', 'to': '06/2018'}]
        self.client.post('/api/role_history', json={'preset_id': 10, 'history': {'work_history': rows}})
        with mock.patch.object(api, '_profile', return_value=('one', None)), \
                mock.patch.object(api.profile_store, 'delete_search_preset', return_value=True):
            response = self.client.post('/api/search_presets/delete', json={'preset_id': 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM role_application_history WHERE role_key='preset:10'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
