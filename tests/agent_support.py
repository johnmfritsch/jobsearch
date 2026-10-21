"""Shared setup for the apply-queue tests (backlog 19.2).

The database is a real temporary file, not :memory:, so a test can open a second
connection to the same pages (the claim race) and so role-document PDFs land in
a throwaway directory next to it. Session routes patch current_user the way the
profile tests do. Agent routes never depend on it: they read the Bearer key only.
"""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

import api
import auto_apply
import desktop_keys
import role_documents
import role_history

ROOT = Path(__file__).resolve().parents[1]


def load_migration(version):
    path = next((ROOT / 'migrations').glob(f'{version}_*.py'))
    spec = importlib.util.spec_from_file_location(f'migration_{version}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def job(title='Integration Engineer', company='Example Co', url='https://example.wd1.myworkdayjobs.com/en/job/1',
        role='Sysadmin', **extra):
    value = {'title': title, 'company': company, 'apply_url': url, 'url': url,
             'description': 'Build integrations.', 'search_role': role}
    value.update(extra)
    return value


WORK = [{'title': 'Admin', 'employer': 'Acme Corp', 'from': '01/2015', 'to': '06/2018'},
        {'title': 'Lead', 'employer': 'Oshkosh Corporation', 'from': '07/2018', 'to': '12/2022'}]


class AgentTestCase(unittest.TestCase):
    def connect(self):
        db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        return db

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / 'test.db')
        self.db = self.connect()
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, profile_key TEXT,
                is_active INTEGER NOT NULL DEFAULT 1);
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
            INSERT INTO user_profiles(user_id,resume_text) VALUES (1,'ACCOUNT RESUME'),(2,'');
            INSERT INTO search_presets(id,user_id,name,resume_text) VALUES
                (10,1,'Sysadmin','Admin at Acme Corp'),(11,1,'Dev',''),(12,1,'Ops','Ops resume'),
                (20,2,'Other','y');
            INSERT INTO search_runs(id,user_id,test_mode,state,status_json,started_at)
                VALUES (1,1,0,'complete','{}','2026-10-01'),(2,2,0,'complete','{}','2026-10-01');
        ''')
        for version in ('013', '014', '015', '022', '023'):
            load_migration(version).up(self.db)
        self.add_job('job-1', job())
        app = Flask(__name__)
        app.config['DATABASE'] = self.path
        app.config['AGENT_API_ENABLED'] = True
        app.config['APPLICATION_PREP_ENABLED'] = False
        app.config['APPLICATION_SUBMIT_ENABLED'] = False
        app.register_blueprint(api.api_bp)
        self.app = app
        self.client = app.test_client()
        self.user = {'id': 1, 'profile_key': 'one'}
        for patcher in (
                mock.patch.object(api, 'current_user', side_effect=lambda: self.user),
                mock.patch.object(api, 'request_db', side_effect=lambda: self.db)):
            patcher.start()
            self.addCleanup(patcher.stop)
        # Preset 10 is ready to queue: confirmed history and a saved resume.
        self.set_history('preset:10', WORK)
        role_documents.sync_resume_text(
            self.db, 1, 10, 'Sysadmin', 'Admin at Acme Corp\nJan 2015 - Jun 2018')

    # ── seed helpers ─────────────────────────────────────────────────────────

    def add_job(self, key, value, user_id=1, run_id=None, tracker=True):
        self.db.execute('INSERT INTO job_results(run_id,job_key,job_json,score) VALUES (?,?,?,.5)',
                        (run_id or user_id, key, json.dumps(value)))
        if tracker:
            record = dict(value, job_key=key, status='saved', stage='saved')
            self.db.execute(
                'INSERT INTO application_records(user_id,job_key,record_json,updated_at) '
                'VALUES (?,?,?,?)', (user_id, key, json.dumps(record), '2026-10-01'))
        self.db.commit()

    def set_history(self, role_key, rows, verified=True, education=None, user_id=1):
        role = {'role_key': role_key, 'role_name': role_key, 'preset_id': None}
        return role_history.save_history(
            self.db, user_id, role,
            {'work_history': rows, 'education': education or [], 'verified': verified})

    def set_profile(self, **sections):
        profile = dict({'full_name': 'John Test', 'email': 'j@example.com', 'middle_name': 'M'},
                       **sections)
        auto_apply.save_profile(self.db, 1, profile)

    def make_key(self, user_id=1, label='Test'):
        return desktop_keys.create_key(self.db, user_id, label)['token']

    @staticmethod
    def bearer(token):
        return {'Authorization': f'Bearer {token}'}

    def queue(self, job_key='job-1', role_key=None):
        body = {'job_key': job_key}
        if role_key is not None:
            body['role_key'] = role_key
        return self.client.post('/api/agent_queue', json=body)

    def claim(self, token):
        response = self.client.post('/api/agent/queue/next', headers=self.bearer(token))
        return response

    def attempt_row(self, attempt_id):
        return self.db.execute('SELECT * FROM application_attempts WHERE id=?',
                               (attempt_id,)).fetchone()

    def post(self, token, attempt_id, action, claim_id, **body):
        body['claim_id'] = claim_id
        return self.client.post(f'/api/agent/attempts/{attempt_id}/{action}', json=body,
                                headers=self.bearer(token))

    def started(self):
        """Queue job-1 and claim it. Returns (token, attempt_id, claim_id, packet)."""
        token = self.make_key()
        attempt_id = self.queue().get_json()['attempt']['id']
        packet = self.claim(token).get_json()['packet']
        return token, attempt_id, packet['attempt']['claim_id'], packet

    def reviewed(self, eeo_listed=False):
        token, attempt_id, claim, _ = self.started()
        response = self.post(token, attempt_id, 'review', claim, summary='Fields filled',
                             filled_fields=[{'label': 'Name', 'value': 'John'}],
                             eeo_listed=eeo_listed)
        return token, attempt_id, claim, response.get_json()['review_sha']

    def approved(self):
        token, attempt_id, claim, sha = self.reviewed()
        self.post(token, attempt_id, 'approval', claim, note='yes, submit', review_sha=sha)
        return token, attempt_id, claim

    def tracker(self, job_key='job-1'):
        row = self.db.execute('SELECT record_json FROM application_records WHERE user_id=1 '
                              'AND job_key=?', (job_key,)).fetchone()
        return json.loads(row['record_json'])
