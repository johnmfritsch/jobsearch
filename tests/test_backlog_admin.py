"""Backlog item files, the generated index and the admin-only page.

Since 2026-10-02 (backlog #24) the page is rendered by the shared backlog-kit, so the parser
cases that used to live here (plan stages, prerequisites, unlocks, children, notices for
cycles / missing / unknown kinds / orphan children, the claim mirror) are covered by the kit's
own tests (/volume1/util/backlog_kit/tests/test_kit.py, ported from GardenBuddy's regression
suite). What stays here is what only JobSearch can check: the real backlog is valid and its
index current, and the admin route behaves (login, 403, escaping, error alert).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

import auth
import backlog_admin
import portal
from health import health_bp

from backlog_kit import index as kit_index
from backlog_kit import model as kit_model
from backlog_kit.config import load_config

ROOT = Path(__file__).resolve().parents[1]

PLAN_REVIEW_MARKER = '**Implementation plan - ready for plan-review:**'


def item(num, title='Title', pri='P1', summary='One line.', extra=()):
    lines = [f'### {num} | {title} | Change | M | {pri}', summary, '', *extra, '', 'Body text <b>raw</b>.']
    return '\n'.join(lines) + '\n'


class RealBacklogTests(unittest.TestCase):
    def test_live_backlog_is_valid_and_its_index_is_current(self):
        config, root = load_config(ROOT)
        self.assertEqual(config['name'], 'JobSearch')
        items, notices = kit_model.load_items(root / config['items_dir'])
        self.assertTrue(items)
        self.assertEqual(notices, [])
        self.assertEqual((root / config['index_file']).read_text(encoding='utf-8'),
                         kit_index.build_index(items, config))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'backlog' / 'items').mkdir(parents=True)
        (root / 'backlog' / 'items' / '1.md').write_text(
            item('1', title='First <i>thing</i>', extra=[PLAN_REVIEW_MARKER]), encoding='utf-8')
        (root / 'backlog' / 'active_claim.md').write_text(
            'Owner: Codex\nItem: 1\nWorkflow step: Plan review\n', encoding='utf-8')
        (root / 'backlog.config.json').write_text(json.dumps({
            'name': 'JobSearch', 'claim_file': 'backlog/active_claim.md'}), encoding='utf-8')
        app = Flask('jobsearch_test', root_path=str(ROOT),
                    template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
        app.config.update(SECRET_KEY='x', BACKLOG_ROOT=str(root), TESTING=True)
        app.register_blueprint(health_bp)
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(portal.portal_bp)
        app.register_blueprint(backlog_admin.backlog_bp)
        self.client = app.test_client()
        self.user = None
        for module in (auth, backlog_admin, portal):
            patcher = mock.patch.object(module, 'current_user', side_effect=lambda: self.user)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_user(self, is_admin):
        return {'id': 1, 'display_name': 'Tester', 'profile_key': 'tester', 'is_admin': is_admin}

    def test_signed_out_is_redirected_to_login(self):
        response = self.client.get('/backlog')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_non_admin_gets_403_and_no_button(self):
        self.user = self.make_user(0)
        self.assertEqual(self.client.get('/backlog').status_code, 403)
        dashboard = self.client.get('/dashboard')
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn(b'/backlog', dashboard.data)

    def test_admin_sees_page_button_badges_and_escaped_text(self):
        self.user = self.make_user(1)
        dashboard = self.client.get('/dashboard')
        self.assertIn(b'href="/backlog"', dashboard.data)
        response = self.client.get('/backlog')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('In plan-review (agent)', html)          # a plan-review item with a live claim
        self.assertIn('Show full item', html)                   # the expandable full item
        self.assertIn('What the priorities and sizes mean', html)
        self.assertIn('First &lt;i&gt;thing&lt;/i&gt;', html)
        self.assertIn('Body text &lt;b&gt;raw&lt;/b&gt;.', html)
        self.assertNotIn('<b>raw</b>', html)
        self.assertIn('Backlog', html)

    def test_missing_backlog_shows_error_not_500(self):
        self.user = self.make_user(1)
        self.client.application.config['BACKLOG_ROOT'] = str(Path(self.tmp.name) / 'nowhere')
        response = self.client.get('/backlog')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Couldn't load the backlog", response.data)


if __name__ == '__main__':
    unittest.main()
