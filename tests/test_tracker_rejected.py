"""Focused API contract checks for the Rejected tracker stage."""
import copy
import unittest
from datetime import date
from unittest import mock

from flask import Flask

import api
import profile_store


class RejectedStageTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(api.api_bp)
        self.client = app.test_client()
        self.triage = {'jobs': {
            'tracked': {
                'stage': 'applied', 'status': 'applied', 'title': 'Analyst',
                'notes': 'Keep this note', 'search_role': 'System Admin',
                'applied_date': '2026-09-01', 'stage_history': [],
            },
            'closed': {'stage': 'closed', 'status': 'applied', 'title': 'Old role',
                       'applied_date': '2026-08-01'},
        }}
        self.patches = [
            mock.patch.object(api, '_profile', return_value=('test-profile', None)),
            mock.patch.object(api, 'current_user', return_value={'id': 7}),
            mock.patch.object(api, 'request_db', return_value=mock.Mock(
                execute=mock.Mock(return_value=mock.Mock(fetchone=lambda: None)))),
            mock.patch.object(profile_store, 'load_triage', side_effect=self.load_triage),
            mock.patch.object(profile_store, 'save_triage', side_effect=self.save_triage),
            mock.patch.object(profile_store, 'load_record', side_effect=self.load_record),
            mock.patch.object(profile_store, 'upsert_record', side_effect=self.upsert_record),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def load_triage(self, _key):
        return copy.deepcopy(self.triage)

    def save_triage(self, _key, value):
        self.triage = copy.deepcopy(value)

    def load_record(self, _key, job_key):
        return copy.deepcopy(self.triage['jobs'].get(job_key))

    def upsert_record(self, _key, job_key, record):
        self.triage['jobs'][job_key] = copy.deepcopy(record)

    def test_tracker_rejection_preserves_history_notes_role_and_applied_date(self):
        response = self.client.post('/api/application_tracker', json={
            'job_key': 'tracked', 'stage': 'rejected'})
        self.assertEqual(response.status_code, 200)
        record = response.get_json()['record']
        self.assertEqual(record['stage'], 'rejected')
        self.assertEqual(record['status'], 'applied')
        self.assertEqual(record['applied_date'], '2026-09-01')
        self.assertEqual(record['notes'], 'Keep this note')
        self.assertEqual(record['search_role'], 'System Admin')
        self.assertEqual([event['stage'] for event in record['stage_history']],
                         ['rejected'])
        jobs = self.client.get('/api/job_triage').get_json()['triage']['jobs']
        self.assertEqual(jobs['tracked']['stage'], 'rejected')
        self.assertEqual(jobs['closed']['stage'], 'closed')

    def test_manual_rejection_uses_supplied_date_or_fills_missing_date(self):
        for title, supplied in [('First role', '2026-09-02'), ('Second role', '')]:
            response = self.client.post('/api/manual_jobs', json={
                'title': title, 'company': 'Example', 'stage': 'rejected',
                'applied_date': supplied, 'search_role': 'HL7 integration',
                'search_role_color': '#2563eb'})
            self.assertEqual(response.status_code, 200)
            record = response.get_json()['record']
            self.assertEqual(record['stage'], 'rejected')
            self.assertEqual(record['status'], 'applied')
            self.assertEqual(record['search_role'], 'HL7 integration')
            self.assertEqual(record['stage_history'][-1]['stage'], 'rejected')
            self.assertEqual(record['applied_date'], supplied or date.today().isoformat())

    def test_legacy_rejection_gets_applied_date_without_changing_closed(self):
        self.triage['jobs']['tracked']['stage'] = 'rejected'
        self.triage['jobs']['tracked'].pop('applied_date')
        self.triage['jobs']['tracked']['updated_at'] = '2026-09-07T12:00:00'
        response = self.client.get('/api/job_triage')
        self.assertEqual(response.status_code, 200)
        jobs = response.get_json()['triage']['jobs']
        self.assertEqual(jobs['tracked']['applied_date'], '2026-09-07')
        self.assertEqual(jobs['closed']['stage'], 'closed')

    def test_invalid_stage_is_rejected_by_both_endpoints(self):
        for endpoint, payload in [
            ('application_tracker', {'job_key': 'tracked', 'stage': 'unknown'}),
            ('manual_jobs', {'title': 'Role', 'company': 'Example',
                             'stage': 'unknown'}),
        ]:
            response = self.client.post('/api/' + endpoint, json=payload)
            self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
