"""Desktop keys, Apply for me, and the guards that keep the worker off agent rows (backlog 19.2)."""
import hashlib
import json
import unittest
from unittest import mock

import api
import auto_apply
import desktop_keys
import role_documents
from agent_support import AgentTestCase, job


class DesktopKeyTests(AgentTestCase):
    def test_token_is_shown_once_and_only_its_hash_is_stored(self):
        created = self.client.post('/api/desktop_session_keys', json={'label': ' My PC '})
        self.assertEqual(created.status_code, 201)
        body = created.get_json()
        token = body['token']
        self.assertTrue(token.startswith('jsa_'))
        self.assertEqual(body['label'], 'My PC')
        dump = json.dumps([list(map(str, row)) for row in self.db.execute(
            'SELECT * FROM desktop_session_keys')])
        self.assertNotIn(token, dump)
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), dump)
        listed = self.client.get('/api/desktop_session_keys').get_json()
        text = json.dumps(listed)
        self.assertNotIn(token, text)
        self.assertNotIn('token_hash', text)
        self.assertEqual(listed['keys'][0]['key_id'], body['key_id'])

    def test_revoke_and_other_users_key_is_404(self):
        mine = self.client.post('/api/desktop_session_keys', json={}).get_json()
        other = desktop_keys.create_key(self.db, 2, 'Theirs')
        self.assertEqual(self.client.delete(f"/api/desktop_session_keys/{other['key_id']}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/desktop_session_keys/{mine['key_id']}").status_code, 200)
        response = self.client.post('/api/agent/queue/next', headers=self.bearer(mine['token']))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM desktop_session_keys WHERE user_id=2')
                         .fetchone()[0], 1)

    def test_five_key_cap_and_default_label(self):
        for _ in range(5):
            self.assertEqual(self.client.post('/api/desktop_session_keys', json={}).status_code, 201)
        self.assertEqual(self.client.post('/api/desktop_session_keys', json={}).status_code, 409)
        self.assertEqual(self.client.get('/api/desktop_session_keys').get_json()['keys'][0]['label'], 'Desktop')

    def test_expired_key_is_rejected(self):
        token = self.make_key()
        self.db.execute("UPDATE desktop_session_keys SET expires_at='2000-01-01T00:00:00'")
        self.db.commit()
        self.assertEqual(self.client.post('/api/agent/queue/next', headers=self.bearer(token)).status_code, 401)

    def test_bearer_without_a_cookie_is_401_on_the_session_queue(self):
        token = self.make_key()
        self.user = None
        response = self.client.post('/api/agent_queue', json={'job_key': 'job-1'},
                                    headers=self.bearer(token))
        self.assertEqual(response.status_code, 401)

    def test_cookie_without_a_bearer_is_401_on_an_agent_route(self):
        self.assertEqual(self.client.post('/api/agent/queue/next').status_code, 401)
        self.assertEqual(self.client.get('/api/agent/queue').status_code, 401)

    def test_flag_off_is_503_and_never_touches_the_key_table(self):
        self.app.config['AGENT_API_ENABLED'] = False
        with mock.patch.object(desktop_keys, 'authenticate') as authenticate:
            response = self.client.post('/api/agent/queue/next', headers=self.bearer('jsa_x'))
        self.assertEqual(response.status_code, 503)
        authenticate.assert_not_called()

    def test_inactive_user_key_is_rejected(self):
        token = self.make_key()
        self.db.execute('UPDATE users SET is_active=0 WHERE id=1')
        self.db.commit()
        self.assertEqual(self.client.post('/api/agent/queue/next', headers=self.bearer(token)).status_code, 401)


class QueueTests(AgentTestCase):
    def rows(self):
        return self.db.execute('SELECT * FROM application_attempts').fetchall()

    def test_queue_inserts_straight_into_queued_for_agent(self):
        response = self.queue()
        self.assertEqual(response.status_code, 201)
        attempt = response.get_json()['attempt']
        self.assertEqual(attempt['state'], 'queued_for_agent')
        self.assertTrue(attempt['agent_owned'])
        self.assertEqual(attempt['role_key'], 'preset:10')
        states = [e['state'] for e in attempt['events']]
        self.assertEqual(states, ['queued_for_agent'])
        self.assertEqual(self.tracker()['application_attempt_id'], attempt['id'])

    def test_second_click_returns_the_same_attempt(self):
        first = self.queue().get_json()['attempt']['id']
        again = self.queue()
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.get_json()['created'])
        self.assertEqual(again.get_json()['attempt']['id'], first)
        self.assertEqual(len(self.rows()), 1)

    def test_role_comes_from_the_tracker_when_omitted_and_explicit_keys_are_parsed(self):
        self.assertEqual(self.queue(role_key='preset:10').status_code, 201)

    def test_role_failures_leave_no_row(self):
        self.add_job('no-role', job(role=''), tracker=True)
        self.add_job('before', job(role='Before roles'))
        self.add_job('gone', job(role='Deleted Role'))
        self.add_job('default-name', job(role='Default'))
        cases = [('no-role', None), ('before', None), ('gone', None),
                 ('job-1', 'na'), ('job-1', 'preset:20'), ('job-1', 'preset:999'),
                 ('job-1', 'name:abc'), ('job-1', 'preset:x'), ('default-name', None)]
        for key, role in cases:
            response = self.queue(key, role)
            self.assertEqual(response.status_code, 409, (key, role, response.get_json()))
            self.assertIn(response.get_json()['code'], ('role_required', 'history_required'), (key, role))
        self.assertEqual(self.queue('no-role').get_json()['code'], 'role_required')
        self.assertEqual(self.queue('default-name').get_json()['code'], 'history_required')
        self.assertEqual(self.rows(), [])

    def test_history_must_be_confirmed_with_a_work_row(self):
        self.set_history('preset:12', [], verified=True)
        self.assertEqual(self.queue('job-1', 'preset:12').get_json()['code'], 'history_required')
        self.set_history('preset:12', [dict(self.WORK_ROW)], verified=False)
        self.assertEqual(self.queue('job-1', 'preset:12').get_json()['code'], 'history_required')
        self.set_history('preset:11', [dict(self.WORK_ROW)], verified=True)
        response = self.queue('job-1', 'preset:11')
        self.assertEqual(response.get_json()['code'], 'resume_required')
        self.assertEqual(self.rows(), [])

    WORK_ROW = {'title': 'Admin', 'employer': 'Acme', 'from': '01/2015', 'to': '06/2018'}

    def test_account_resume_is_not_a_fallback(self):
        self.set_history('preset:11', [dict(self.WORK_ROW)], verified=True)
        self.assertEqual(self.db.execute('SELECT resume_text FROM user_profiles WHERE user_id=1')
                         .fetchone()[0], 'ACCOUNT RESUME')
        response = self.queue('job-1', 'preset:11')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'resume_required')

    def test_preset_only_resume_text_is_accepted(self):
        self.set_history('preset:12', [dict(self.WORK_ROW)], verified=True)
        self.assertEqual(self.queue('job-1', 'preset:12').status_code, 201)

    def test_linkedin_and_bad_urls_are_refused(self):
        self.add_job('li', job(url='https://www.linkedin.com/jobs/view/1'))
        self.add_job('none', job(url=''))
        self.assertEqual(self.queue('li').get_json()['code'], 'linkedin_refused')
        self.assertEqual(self.queue('none').get_json()['code'], 'url_invalid')
        self.assertEqual(self.rows(), [])

    def test_unknown_job_is_404_and_signed_out_is_401(self):
        self.assertEqual(self.queue('missing').status_code, 404)
        self.user = None
        self.assertEqual(self.queue().status_code, 401)

    def test_a_worker_path_attempt_is_not_converted(self):
        attempt, _ = auto_apply.create_attempt(self.db, 1, 'job-1')
        self.assertEqual(attempt['state'], 'queued')
        response = self.queue()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'attempt_in_progress')
        self.assertEqual(self.attempt_row(attempt['id'])['agent_owned'], 0)

    def test_paused_attempts_requeue_and_busy_or_submitted_ones_do_not(self):
        attempt_id = self.queue().get_json()['attempt']['id']
        for state in ('needs_login', 'needs_user_input', 'blocked_captcha'):
            self.db.execute('UPDATE application_attempts SET state=?,claim_id=? WHERE id=?',
                            (state, 'old', attempt_id))
            self.db.commit()
            again = self.queue()
            self.assertEqual(again.status_code, 200, state)
            self.assertEqual(again.get_json()['attempt']['state'], 'queued_for_agent')
            self.assertIsNone(self.attempt_row(attempt_id)['claim_id'])
        for state, code in (('ready_for_review', 'attempt_in_progress'), ('analyzing_form', 'attempt_in_progress'),
                            ('awaiting_final_confirmation', 'attempt_in_progress'),
                            ('submitting', 'attempt_in_progress'), ('submitted', 'already_submitted')):
            self.db.execute('UPDATE application_attempts SET state=? WHERE id=?', (state, attempt_id))
            self.db.commit()
            response = self.queue()
            self.assertEqual(response.status_code, 409, state)
            self.assertEqual(response.get_json()['code'], code)

    def test_a_closed_agent_attempt_gets_a_fresh_row(self):
        first = self.queue().get_json()['attempt']['id']
        self.db.execute("UPDATE application_attempts SET state='could_not_fill' WHERE id=?", (first,))
        self.db.commit()
        second = self.queue()
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(second.get_json()['attempt']['id'], first)

    def test_list_includes_every_agent_state_and_counts_only_waiting(self):
        self.add_job('job-2', job(title='Second'))
        first = self.queue().get_json()['attempt']['id']
        self.queue('job-2')
        self.db.execute("UPDATE application_attempts SET state='submitted' WHERE id=?", (first,))
        self.db.commit()
        listed = self.client.get('/api/agent_queue').get_json()
        self.assertEqual(len(listed['attempts']), 2)
        self.assertEqual(listed['waiting'], 1)
        self.assertNotIn('packet', json.dumps(listed))


class WorkerGuardTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.app.config['APPLICATION_PREP_ENABLED'] = True
        self.app.config['APPLICATION_SUBMIT_ENABLED'] = True
        self.attempt_id = self.queue().get_json()['attempt']['id']

    def state(self):
        return self.attempt_row(self.attempt_id)['state']

    def test_prepare_and_submit_refuse_an_agent_row_without_calling_the_worker(self):
        with mock.patch.object(api, '_worker_request') as worker:
            prepare = self.client.post(f'/api/application_attempts/{self.attempt_id}/prepare')
            submit = self.client.post(f'/api/application_attempts/{self.attempt_id}/submit',
                                      json={'confirmation_token': 'x'})
        self.assertEqual(prepare.status_code, 409)
        self.assertEqual(submit.status_code, 409)
        worker.assert_not_called()
        self.assertEqual(self.state(), 'queued_for_agent')

    def test_confirmation_token_returns_before_inserting_one(self):
        response = self.client.post(f'/api/application_attempts/{self.attempt_id}/confirmation-token')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.db.execute(
            'SELECT COUNT(*) FROM application_confirmation_tokens').fetchone()[0], 0)

    def test_document_approval_returns_before_any_update(self):
        self.db.execute(
            'INSERT INTO application_artifacts (id,attempt_id,user_id,kind,format,file_name,'
            'relative_path,sha256,mime_type,approval_state,version,generated_at) '
            "VALUES ('art',?,1,'resume','txt','a.txt','a','x','text/plain','pending',1,'now')",
            (self.attempt_id,))
        self.db.commit()
        response = self.client.post('/api/application_artifacts/art/decision',
                                    json={'decision': 'approved'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.db.execute("SELECT approval_state FROM application_artifacts WHERE id='art'")
                         .fetchone()[0], 'pending')
        self.assertEqual(self.state(), 'queued_for_agent')

    def test_transition_refuses_without_allow_agent_but_cancel_works(self):
        with self.assertRaises(ValueError):
            auto_apply.transition(self.db, 1, self.attempt_id, 'analyzing_form')
        self.assertEqual(self.state(), 'queued_for_agent')
        response = self.client.post(f'/api/application_attempts/{self.attempt_id}/cancel')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state(), 'cancelled')

    def test_extra_updates_are_whitelisted(self):
        with self.assertRaises(ValueError):
            auto_apply.transition(self.db, 1, self.attempt_id, 'analyzing_form', allow_agent=True,
                                  extra_updates={'user_id': 2})
        self.assertEqual(self.state(), 'queued_for_agent')

    def test_retry_never_mints_a_worker_attempt(self):
        self.db.execute("UPDATE application_attempts SET state='could_not_fill' WHERE id=?",
                        (self.attempt_id,))
        self.db.commit()
        response = self.client.post(f'/api/application_attempts/{self.attempt_id}/retry')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM application_attempts').fetchone()[0], 1)

    def test_agent_rows_expose_no_secret_and_show_the_state_label(self):
        detail = self.client.get(f'/api/application_attempts/{self.attempt_id}').get_json()['attempt']
        self.assertTrue(detail['agent_owned'])
        self.assertEqual(detail['state_label'], 'Queued for agent')
        self.assertNotIn('token', ' '.join(detail.keys()))

    def test_worker_path_is_unchanged(self):
        attempt, created = auto_apply.create_attempt(self.db, 1, 'job-1')
        self.assertFalse(created)  # the open agent attempt is returned, not a second row
        self.add_job('job-2', job(title='Plain'))
        plain, created = auto_apply.create_attempt(self.db, 1, 'job-2')
        self.assertTrue(created)
        self.assertEqual(plain['state'], 'queued')
        self.assertFalse(plain['agent_owned'])
        self.assertIsNone(plain['role_key'])
        moved = auto_apply.transition(self.db, 1, plain['id'], 'validating_url')
        self.assertEqual(moved['state'], 'validating_url')


if __name__ == '__main__':
    unittest.main()
