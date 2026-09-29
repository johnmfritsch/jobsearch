"""Fixture-backed regressions for backlog #21 adapter contracts."""
import importlib.util
import json
import pathlib
import sqlite3
import unittest
from unittest import mock

import safe_fetch
from scrapers import employer_source_scraper
from scrapers import jibe_scraper as jibe
from scrapers import structured_data_scraper as structured


FIXTURES = pathlib.Path(__file__).with_name('fixtures')


def _result(url, text, content='application/json'):
    return safe_fetch.FetchResult(url, 200, content, text, {'content-type': content}, '93.184.216.34')


def _fixture(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


class JibeFixtureTests(unittest.TestCase):
    def _fetcher(self, listing_name, detail_name, host):
        def fetch(url, request_host, **_kwargs):
            self.assertEqual(request_host, host)
            if '/api/jobs/' in url and not url.rstrip('/').endswith('/api/jobs'):
                return _result(url, _fixture(detail_name))
            if '/api/jobs' in url:
                return _result(url, _fixture(listing_name))
            return _result(url, '<html data-jibe-search-version="1"><script>window.searchConfig={"endpoint":"/api/jobs"}</script></html>', 'text/html')
        return fetch

    def test_ppl_listing_detail_normalization_uses_stable_id_and_full_text(self):
        source = {'company_name': 'PPL', 'company_aliases': ['PPL Corporation'],
                  'careers_url': 'https://careers.pplweb.com/jobs'}
        with mock.patch.object(jibe, '_fetch', side_effect=self._fetcher(
                'jibe_ppl_listing.json', 'jibe_ppl_detail.json', 'careers.pplweb.com')):
            jobs = jibe.fetch_jobs(source, {'keywords': ['engineer'], 'search_local': True,
                                            'location': 'Louisville', 'priority_employer_max_details': 2}, {})
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job['id'], 'jibe:careers.pplweb.com:15121:en-us')
        self.assertEqual(job['apply_url'], 'https://careers-pplweb.icims.com/jobs/15121/login')
        self.assertIn('Required Education', job['full_description'])
        self.assertIn('• Engineering degree', job['full_description'])
        self.assertEqual(job['_search_keywords'], ['engineer'])

    def test_second_independent_booking_fixture_uses_identical_platform_contract(self):
        source = {'company_name': 'Booking.com', 'careers_url': 'https://jobs.booking.com/booking/jobs'}
        with mock.patch.object(jibe, '_fetch', side_effect=self._fetcher(
                'jibe_booking_listing.json', 'jibe_booking_detail.json', 'jobs.booking.com')):
            job = jibe.fetch_jobs(source, {'keywords': ['engineer'], 'priority_employer_max_details': 2}, {})[0]
        self.assertEqual(job['id'], 'jibe:jobs.booking.com:29701:en-us')
        self.assertEqual(job['company'], 'Booking.com')
        self.assertIn('Software development experience', job['full_description'])

    def test_probe_requires_markers_and_same_host_api_shape(self):
        with mock.patch.object(jibe, '_fetch', side_effect=[
                _result('https://careers.example/jobs', '<html>careers</html>', 'text/html')]):
            self.assertIsNone(jibe.probe('https://careers.example/jobs'))
        with mock.patch.object(jibe, '_fetch', side_effect=self._fetcher(
                'jibe_ppl_listing.json', 'jibe_ppl_detail.json', 'careers.example')):
            result = jibe.probe('https://careers.example/jobs')
        self.assertEqual(result['adapter'], 'jibe')

    def test_global_detail_cap_does_not_restart_for_each_keyword(self):
        source = {'company_name': 'PPL Corporation', 'careers_url': 'https://careers.pplweb.com/jobs'}
        listing = json.loads(_fixture('jibe_ppl_listing.json'))
        listing['totalCount'] = 2
        listing['jobs'][0]['data']['description'] = 'complete ' * 100
        listing['jobs'].append({'data': dict(listing['jobs'][0]['data'], slug='15122', req_id='15122')})
        with mock.patch.object(jibe, '_fetch', side_effect=[
                _result(source['careers_url'], '<html></html>', 'text/html'),
                _result('https://careers.pplweb.com/api/jobs', json.dumps(listing)),
                _result(source['careers_url'], '<html></html>', 'text/html'),
                _result('https://careers.pplweb.com/api/jobs', json.dumps(listing))]):
            jobs = jibe.fetch_jobs(source, {'keywords': ['one', 'two'], 'priority_employer_max_details': 2}, {})
        self.assertEqual(len(jobs), 2)


class StructuredDataFixtureTests(unittest.TestCase):
    html = '''<script type="application/ld+json">{"@graph":[
      {"@type":"JobPosting","identifier":{"value":"req-7"},"title":"Integration Engineer",
       "description":"<p>Build integrations.</p>","hiringOrganization":{"name":"Acme, Inc."},
       "jobLocation":{"address":{"addressLocality":"Allentown","addressRegion":"PA"}},
       "datePosted":"2026-09-10","url":"/careers/jobs/7","directApply":true},
      {"@type":"NotAJobPosting","title":"Ignore"}
    ]}</script>'''

    def test_json_ld_graph_is_exact_type_and_boolean_direct_apply_is_not_a_url(self):
        source = {'company_name': 'Acme Inc', 'careers_url': 'https://careers.example/jobs'}
        with mock.patch.object(structured, '_fetch', return_value=_result(source['careers_url'], self.html, 'text/html')):
            jobs = structured.fetch_jobs(source, {'keywords': ['integration']}, {})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['apply_url'], 'https://careers.example/careers/jobs/7')
        self.assertTrue(jobs[0]['direct_apply'])
        self.assertNotEqual(jobs[0]['apply_url'], 'True')

    def test_company_mismatch_is_rejected(self):
        source = {'company_name': 'Acme', 'careers_url': 'https://careers.example/jobs'}
        html = self.html.replace('Acme, Inc.', 'Other Company')
        with mock.patch.object(structured, '_fetch', return_value=_result(source['careers_url'], html, 'text/html')):
            with self.assertRaises(structured.StructuredDataError):
                structured.fetch_jobs(source, {}, {})


class Migration021Tests(unittest.TestCase):
    @staticmethod
    def _migration():
        path = pathlib.Path(__file__).parents[1] / 'migrations' / '021_add_jibe_and_structured_data_adapters.py'
        spec = importlib.util.spec_from_file_location('migration_021', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_upgrade_and_downgrade_preserve_source_roles(self):
        db = sqlite3.connect(':memory:')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            INSERT INTO users VALUES (1);
            CREATE TABLE employer_sources (
                id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                company_name TEXT NOT NULL,company_key TEXT NOT NULL,company_aliases_json TEXT NOT NULL DEFAULT '[]',
                careers_url TEXT,careers_url_key TEXT NOT NULL DEFAULT '',
                adapter TEXT NOT NULL CHECK(adapter IN ('successfactors','workday','ukg','serpapi_company','unsupported')),
                enabled_all_roles INTEGER NOT NULL DEFAULT 0 CHECK(enabled_all_roles IN (0,1)),enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                last_success_at TEXT,last_error TEXT,last_checked_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                UNIQUE(user_id,company_key,careers_url_key));
            CREATE TABLE employer_source_roles (source_id INTEGER REFERENCES employer_sources(id),preset_id INTEGER,created_at TEXT);
            INSERT INTO employer_sources VALUES (7,1,'Acme','acme','[]','https://careers.example','https://careers.example','unsupported',0,1,NULL,NULL,NULL,'now','now');
            INSERT INTO employer_source_roles VALUES (7,42,'now');
        ''')
        migration = self._migration()
        migration.up(db)
        self.assertTrue(migration.verify(db))
        db.execute("UPDATE employer_sources SET adapter='jibe' WHERE id=7")
        migration.down(db)
        self.assertEqual(db.execute('SELECT adapter FROM employer_sources WHERE id=7').fetchone()[0], 'unsupported')
        self.assertEqual(db.execute('SELECT source_id,preset_id FROM employer_source_roles').fetchone(), (7, 42))
        self.assertEqual(list(db.execute('PRAGMA foreign_key_check')), [])


class NoNetworkModeTests(unittest.TestCase):
    def test_fallback_does_not_call_serpapi_in_test_mode(self):
        source = {'adapter': 'unsupported', 'company_name': 'Acme', 'careers_url': 'https://careers.example'}
        with mock.patch('scrapers.employer_source_scraper.requests.get') as request:
            self.assertEqual(employer_source_scraper.fetch_employer_jobs(source, {}, {}, test_mode=True), [])
        request.assert_not_called()
