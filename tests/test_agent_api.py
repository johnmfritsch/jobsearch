"""The desktop-agent API: claim, packet, review/approval/outcome, question bank, tracker (backlog 19.2)."""
import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import agent_queue
import api
import auto_apply
import manual_jobs
import role_documents
import role_history
from agent_support import AgentTestCase, job


def past(hours=3):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(
        tzinfo=None, microsecond=0).isoformat()


class ClaimTests(AgentTestCase):
    def test_empty_queue_is_204_with_no_body(self):
        response = self.claim(self.make_key())
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.data, b'')

    def test_claim_returns_the_packet_and_marks_the_attempt(self):
        token, attempt_id, claim, packet = self.started()
        row = self.attempt_row(attempt_id)
        self.assertEqual(row['state'], 'analyzing_form')
        self.assertEqual(row['claim_id'], claim)
        self.assertTrue(row['lease_expires_at'] > auto_apply.utcnow())
        self.assertEqual(packet['attempt']['id'], attempt_id)
        events = [r['message'] for r in self.db.execute(
            'SELECT message FROM application_events WHERE attempt_id=? ORDER BY id', (attempt_id,))]
        self.assertIn('Claimed by the desktop agent', events)
        self.assertIsNotNone(self.db.execute('SELECT last_used_at FROM desktop_session_keys').fetchone()[0])

    def test_two_connections_have_one_winner(self):
        self.queue()
        first, second = self.connect(), self.connect()
        results = []
        barrier = threading.Barrier(2)

        def worker(db):
            barrier.wait()
            results.append(agent_queue.claim_next(db, 1))
        threads = [threading.Thread(target=worker, args=(c,)) for c in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        first.close()
        second.close()
        self.assertEqual(sum(1 for r in results if r), 1)

    def test_oldest_queued_attempt_goes_first(self):
        self.add_job('job-2', job(title='Second'))
        one = self.queue().get_json()['attempt']['id']
        self.queue('job-2')
        self.db.execute("UPDATE application_attempts SET updated_at='2000-01-01T00:00:00' WHERE id=?", (one,))
        self.db.commit()
        packet = self.claim(self.make_key()).get_json()['packet']
        self.assertEqual(packet['attempt']['id'], one)

    def test_expired_analyzing_lease_returns_to_the_queue_and_is_reclaimed(self):
        token, attempt_id, claim, _ = self.started()
        self.db.execute('UPDATE application_attempts SET lease_expires_at=? WHERE id=?', (past(), attempt_id))
        self.db.commit()
        packet = self.claim(token).get_json()['packet']
        self.assertEqual(packet['attempt']['id'], attempt_id)
        self.assertNotEqual(packet['attempt']['claim_id'], claim)
        self.assertEqual(self.post(token, attempt_id, 'progress', claim, message='old').status_code, 409)

    def test_expiry_never_pulls_a_review_back(self):
        token, attempt_id, claim, _ = self.reviewed()
        self.db.execute('UPDATE application_attempts SET lease_expires_at=? WHERE id=?', (past(), attempt_id))
        self.db.commit()
        self.assertEqual(self.claim(token).status_code, 204)
        self.assertEqual(self.attempt_row(attempt_id)['state'], 'ready_for_review')

    def test_wrong_or_missing_claim_id_is_409_and_other_users_attempt_is_404(self):
        token, attempt_id, claim, _ = self.started()
        self.assertEqual(self.post(token, attempt_id, 'progress', 'wrong', message='x').status_code, 409)
        self.assertEqual(self.post(token, attempt_id, 'progress', None, message='x').status_code, 409)
        other = self.make_key(user_id=2)
        self.assertEqual(self.post(other, attempt_id, 'progress', claim, message='x').status_code, 404)

    def test_release_from_analyzing_returns_it_and_from_a_paused_state_is_refused(self):
        token, attempt_id, claim, _ = self.started()
        released = self.post(token, attempt_id, 'release', claim)
        self.assertEqual(released.status_code, 200)
        row = self.attempt_row(attempt_id)
        self.assertEqual(row['state'], 'queued_for_agent')
        self.assertIsNone(row['claim_id'])
        packet = self.claim(token).get_json()['packet']
        claim2 = packet['attempt']['claim_id']
        self.assertEqual(self.post(token, attempt_id, 'outcome', claim2, outcome='needs_login').status_code, 200)
        self.assertEqual(self.post(token, attempt_id, 'release', claim2).status_code, 409)

    def test_release_from_review_clears_the_review(self):
        token, attempt_id, claim, _ = self.reviewed()
        self.assertEqual(self.post(token, attempt_id, 'release', claim).status_code, 200)
        row = self.attempt_row(attempt_id)
        self.assertEqual(row['state'], 'queued_for_agent')
        self.assertIsNone(row['review_json'])

    def test_request_bodies_over_64kb_are_413(self):
        token, attempt_id, claim, _ = self.started()
        response = self.post(token, attempt_id, 'progress', claim, message='x' * 70000)
        self.assertEqual(response.status_code, 413)


class PostingTests(AgentTestCase):
    def test_closed_cancels_and_notes_the_tracker_once(self):
        token, attempt_id, claim, _ = self.started()
        with mock.patch.object(manual_jobs, 'extract', side_effect=AssertionError('network')), \
                mock.patch.object(api, '_worker_request', side_effect=AssertionError('worker')):
            response = self.post(token, attempt_id, 'posting', claim, status='closed', note='gone')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.post(token, attempt_id, 'posting', claim, status='closed').status_code, 200)
        row = self.attempt_row(attempt_id)
        self.assertEqual(row['state'], 'cancelled')
        self.assertEqual(row['error_code'], 'posting_closed')
        self.assertIsNone(row['lease_expires_at'])
        notes = [n['text'] for n in self.tracker()['notes_log']]
        self.assertEqual(len([n for n in notes if 'Posting closed before apply' in n]), 1)
        self.assertEqual(self.tracker()['stage'], 'saved')

    def test_unreadable_and_open_only_record_the_check(self):
        token, attempt_id, claim, _ = self.started()
        for status in ('unreadable', 'open'):
            self.assertEqual(self.post(token, attempt_id, 'posting', claim, status=status).status_code, 200)
            row = self.attempt_row(attempt_id)
            self.assertEqual(row['posting_status'], status)
            self.assertEqual(row['state'], 'analyzing_form')
        self.assertNotIn('notes_log', self.tracker())

    def test_resolved_url_must_be_public_and_not_linkedin(self):
        token, attempt_id, claim, _ = self.started()
        for bad in ('javascript:alert(1)', 'https://www.linkedin.com/jobs/1', 'ftp://x.example/a'):
            response = self.post(token, attempt_id, 'posting', claim, status='open', resolved_url=bad)
            self.assertEqual(response.status_code, 400, bad)
        good = 'https://boards.greenhouse.io/acme/jobs/1'
        self.assertEqual(self.post(token, attempt_id, 'posting', claim, status='open',
                                   resolved_url=good).status_code, 200)
        row = self.attempt_row(attempt_id)
        self.assertEqual((row['resolved_url'], row['ats_kind']), (good, 'greenhouse'))

    def test_bad_status_is_400(self):
        token, attempt_id, claim, _ = self.started()
        self.assertEqual(self.post(token, attempt_id, 'posting', claim, status='maybe').status_code, 400)


class ProgressTests(AgentTestCase):
    def test_metadata_is_whitelisted_and_ssn_shapes_are_redacted(self):
        token, attempt_id, claim, _ = self.started()
        response = self.post(token, attempt_id, 'progress', claim,
                             message='Typed 123-45-6789 on page 2', step='contact', page='2',
                             field_count=7, password='hunter2', eeo={'gender': 'male'})
        self.assertEqual(response.status_code, 200)
        event = self.db.execute('SELECT message,metadata_json FROM application_events '
                                'WHERE attempt_id=? ORDER BY id DESC LIMIT 1', (attempt_id,)).fetchone()
        self.assertNotIn('123-45-6789', event['message'])
        self.assertIn('[redacted]', event['message'])
        self.assertEqual(json.loads(event['metadata_json']), {'step': 'contact', 'page': '2', 'field_count': 7})

    def test_progress_extends_the_lease(self):
        token, attempt_id, claim, _ = self.started()
        self.db.execute('UPDATE application_attempts SET lease_expires_at=? WHERE id=?',
                        ((datetime.now(timezone.utc) + timedelta(minutes=1)).replace(
                            tzinfo=None, microsecond=0).isoformat(), attempt_id))
        self.db.commit()
        self.post(token, attempt_id, 'progress', claim, message='page 1')
        row = self.attempt_row(attempt_id)
        self.assertTrue(row['lease_expires_at'] > (datetime.now(timezone.utc) + timedelta(hours=1)).replace(
            tzinfo=None).isoformat())


class PacketTests(AgentTestCase):
    def packet(self):
        return self.started()[3]

    def test_history_is_the_attempt_role_only_and_resume_is_that_roles(self):
        self.set_history('preset:12', [{'title': 'Other', 'employer': 'Beta', 'from': '01/2010', 'to': '01/2011'}])
        packet = self.packet()
        self.assertEqual([r['employer'] for r in packet['work_history']], ['Acme Corp', 'Oshkosh Corporation'])
        self.assertEqual(packet['role'], {'role_key': 'preset:10', 'role_name': 'Sysadmin'})
        self.assertIn('Acme Corp', packet['resume']['text'])
        self.assertNotIn('ACCOUNT RESUME', json.dumps(packet))
        current = role_documents.current_document(self.db, 1, 'preset:10', 'resume', 'txt')
        self.assertEqual(packet['resume']['version'], current['version'])
        self.assertEqual(packet['resume']['document_id'], current['document_id'])
        self.assertTrue(packet['verified_at'])

    def test_eeo_is_absent_unless_opted_in(self):
        self.set_profile(eeo={'opt_in': False})
        self.assertNotIn('eeo', self.packet())

    def test_eeo_present_when_opted_in_with_ui_labels_and_review_required(self):
        self.set_profile(eeo={'opt_in': True, 'gender': 'non_binary', 'race_ethnicity': 'hispanic_or_latino',
                              'veteran': '', 'disability': 'decline'})
        eeo = self.packet()['eeo']
        self.assertTrue(eeo['review_required'])
        self.assertEqual(eeo['values'], {'gender': 'non_binary', 'race_ethnicity': 'hispanic_or_latino',
                                         'disability': 'decline'})
        self.assertEqual(eeo['labels']['gender']['non_binary'], 'Non-binary')
        self.assertEqual(eeo['labels']['race_ethnicity']['hispanic_or_latino'], 'Hispanic or Latino')
        self.assertEqual(eeo['ask_user'], ['veteran'])

    def test_empty_screening_is_ask_user_and_stored_yes_no_is_safe(self):
        self.set_profile(screening={'age_18_plus': 'yes', 'us_person': '', 'shift_availability': '',
                                    'notes': 'private context'})
        screening = self.packet()['screening']
        self.assertEqual(screening['age_18_plus'], {'value': 'yes', 'safe': True, 'ask_user': False, 'reason': ''})
        self.assertTrue(screening['us_person']['ask_user'])
        self.assertFalse(screening['us_person']['safe'])
        self.assertTrue(screening['shift_availability']['ask_user'])
        self.assertFalse(screening['agent_notes']['safe'])

    def test_shareable_profile_fields_and_no_screening_leak(self):
        self.set_profile(screening={'us_person': 'yes'})
        profile = self.packet()['profile']
        self.assertEqual(profile['middle_name'], 'M')
        self.assertNotIn('screening', profile)
        self.assertNotIn('eeo', profile)

    def test_employer_match_nulls_the_three_employment_answers(self):
        self.set_profile(screening={'current_employee': 'no', 'former_employee': 'yes',
                                    'conflict_of_interest': 'no', 'age_18_plus': 'yes'})
        self.add_job('job-2', job(title='Welder', company='Oshkosh'))
        token = self.make_key()
        self.queue('job-2')
        packet = self.claim(token).get_json()['packet']
        self.assertTrue(packet['employer_match']['matched'])
        self.assertEqual(packet['employer_match']['matched_employer'], 'Oshkosh Corporation')
        for key in ('current_employee', 'former_employee', 'conflict_of_interest'):
            entry = packet['screening'][key]
            self.assertIsNone(entry['value'], key)
            self.assertTrue(entry['ask_user'])
            self.assertFalse(entry['safe'])
        self.assertTrue(packet['screening']['age_18_plus']['safe'])

    def test_employer_matching_rules(self):
        db = self.db
        self.set_history('preset:12', [
            {'title': 'T', 'employer': 'GE Aerospace', 'from': '01/2010', 'to': '01/2011'},
            {'title': 'T', 'employer': 'Beta', 'from': '01/2012', 'to': '01/2013'}])
        match = lambda name: role_history.matching_employers(db, 1, name)['matched']
        self.assertTrue(match('Oshkosh'))                      # prefix of 3+ chars
        self.assertTrue(match('Oshkosh Corporation'))          # equal
        self.assertTrue(match('OSHKOSH CORP.'))                # legal suffix and punctuation
        self.assertTrue(match('The Acme Company'))             # leading the, legal suffix
        self.assertFalse(match('Unrelated Inc'))
        self.assertFalse(match('GE'))                          # two characters never prefix-match
        self.assertTrue(match('GE Aerospace'))                 # an exact match still counts
        self.assertFalse(match('Be'))
        self.assertTrue(match('Beta LLC'))
        self.assertFalse(match(''))
        keys = role_history.matching_employers(db, 1, 'Beta')['role_keys']
        self.assertEqual(keys, ['preset:12'])                  # matched from another role

    def test_unconfirmed_history_never_matches(self):
        self.set_history('preset:12', [{'title': 'T', 'employer': 'Zeta', 'from': '01/2010', 'to': '01/2011'}],
                         verified=False)
        self.assertFalse(role_history.matching_employers(self.db, 1, 'Zeta')['matched'])

    def test_stop_list_and_labels_are_present(self):
        packet = self.packet()
        self.assertTrue(any('LinkedIn' in item for item in packet['stop_list']))
        self.assertEqual(packet['labels']['education_level']['bachelor'], "Bachelor's")

    def test_pdf_ids_appear_only_when_a_pdf_exists(self):
        self.assertIsNone(self.packet()['resume']['pdf_document_id'])
        self.db.execute("UPDATE application_attempts SET state='cancelled'")
        self.db.commit()
        role = {'role_key': 'preset:10', 'role_name': 'Sysadmin', 'preset_id': 10}
        pdf = role_documents.save_pdf(self.db, self.path, 1, role, 'resume', b'%PDF-1.4 test', 'r.pdf')
        self.add_job('job-2', job(title='Again'))
        token = self.make_key()
        self.queue('job-2')
        packet = self.claim(token).get_json()['packet']
        self.assertEqual(packet['resume']['pdf_document_id'], pdf['id'])
        self.assertEqual(packet['resume']['pdf_version'], 1)


class ResumeDownloadTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        role = {'role_key': 'preset:10', 'role_name': 'Sysadmin', 'preset_id': 10}
        self.pdf = role_documents.save_pdf(self.db, self.path, 1, role, 'resume', b'%PDF-1.4 mine', 'r.pdf')
        other = {'role_key': 'preset:12', 'role_name': 'Ops', 'preset_id': 12}
        self.other_role_pdf = role_documents.save_pdf(self.db, self.path, 1, other, 'resume', b'%PDF-1.4 ops', 'o.pdf')
        theirs = {'role_key': 'preset:20', 'role_name': 'Other', 'preset_id': 20}
        self.other_user_pdf = role_documents.save_pdf(self.db, self.path, 2, theirs, 'resume', b'%PDF-1.4 x', 'x.pdf')
        self.token, self.attempt_id, self.claim_id, _ = self.started()

    def get(self, document_id, claim=None):
        return self.client.get(
            f'/api/agent/attempts/{self.attempt_id}/resume',
            query_string={'document_id': document_id, 'claim_id': claim or self.claim_id},
            headers=self.bearer(self.token))

    def test_serves_the_roles_own_pdf(self):
        response = self.get(self.pdf['id'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b'%PDF-1.4 mine')

    def test_refuses_another_role_another_user_and_a_wrong_claim(self):
        self.assertEqual(self.get(self.other_role_pdf['id']).status_code, 404)
        self.assertEqual(self.get(self.other_user_pdf['id']).status_code, 404)
        self.assertEqual(self.get(self.pdf['id'], claim='wrong').status_code, 409)


class QuestionBankTests(AgentTestCase):
    def ask(self, token, attempt_id, claim, question, **extra):
        return self.client.post('/api/agent/questions', headers=self.bearer(token), json=dict(
            {'attempt_id': attempt_id, 'claim_id': claim, 'question': question}, **extra))

    def test_sensitive_questions_and_ssn_shaped_answers_are_refused_and_not_stored(self):
        token, attempt_id, claim, _ = self.started()
        for question in ('Enter your Social Security Number', 'Passport number?', "Driver's license no.",
                         'National ID', 'ITIN or tax identification number', 'What is your SSN?'):
            self.assertEqual(self.ask(token, attempt_id, claim, question).status_code, 400, question)
        self.assertEqual(self.ask(token, attempt_id, claim, 'Anything else?', answer='123-45-6789').status_code, 400)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM application_question_bank').fetchone()[0], 0)

    def test_unanswered_question_blocks_and_a_second_post_with_answer_confirms_it(self):
        token, attempt_id, claim, _ = self.started()
        first = self.ask(token, attempt_id, claim, 'Are you willing to relocate?', field_type='yes_no').get_json()
        self.assertEqual(first['question']['status'], 'needs_answer')
        self.assertEqual(first['question']['blocking_attempt_id'], attempt_id)
        again = self.ask(token, attempt_id, claim, 'are you willing to relocate', field_type='yes_no')
        self.assertEqual(again.get_json()['question']['times_seen'], 2)
        answered = self.ask(token, attempt_id, claim, 'Are you willing to relocate?', field_type='yes_no',
                            answer='No').get_json()['question']
        self.assertEqual(answered['status'], 'confirmed')
        self.assertEqual(answered['source'], 'agent_chat')
        self.assertTrue(answered['safe_to_autofill'])
        self.assertIsNone(answered['blocking_attempt_id'])

    def test_a_confirmed_answer_is_not_overwritten_by_the_agent(self):
        token, attempt_id, claim, _ = self.started()
        self.ask(token, attempt_id, claim, 'Preferred pronouns?', answer='they/them')
        row = self.ask(token, attempt_id, claim, 'Preferred pronouns?', answer='he/him').get_json()['question']
        self.assertEqual(row['answer'], 'they/them')
        self.assertEqual(row['times_seen'], 2)

    def test_free_text_is_not_safe_but_yes_no_and_choice_are(self):
        token, attempt_id, claim, _ = self.started()
        text = self.ask(token, attempt_id, claim, 'Why do you want this job?', answer='Because').get_json()['question']
        self.assertFalse(text['safe_to_autofill'])
        choice = self.ask(token, attempt_id, claim, 'How did you hear?', field_type='choice',
                          options=['Referral', 'Web'], answer='Web').get_json()['question']
        self.assertTrue(choice['safe_to_autofill'])
        off_list = self.ask(token, attempt_id, claim, 'Pick one', field_type='choice',
                            options=['A', 'B'], answer='C').get_json()['question']
        self.assertFalse(off_list['safe_to_autofill'])

    def test_a_question_naming_the_company_is_never_safe(self):
        token, attempt_id, claim, _ = self.started()
        row = self.ask(token, attempt_id, claim, 'Are you a current employee of Example Co?',
                       field_type='yes_no', answer='no').get_json()['question']
        self.assertFalse(row['safe_to_autofill'])

    def test_the_bank_lists_only_the_callers_rows_and_the_packet_only_confirmed(self):
        token, attempt_id, claim, _ = self.started()
        self.ask(token, attempt_id, claim, 'Q one', answer='A')
        self.ask(token, attempt_id, claim, 'Q two')
        self.db.execute(
            "INSERT INTO application_question_bank (id,user_id,question_key,question_text,answer_text,"
            "source,status,created_at,updated_at) VALUES ('x',2,'secret','Secret','Mine','user_ui',"
            "'confirmed','n','n')")
        self.db.commit()
        mine = self.client.get('/api/question_bank').get_json()['answers']
        self.assertEqual(sorted(r['question'] for r in mine), ['Q one', 'Q two'])
        self.post(token, attempt_id, 'release', claim)
        packet = self.claim(token).get_json()['packet']
        self.assertEqual([r['question'] for r in packet['bank']], ['Q one'])

    def test_ui_can_correct_tick_reuse_and_delete(self):
        token, attempt_id, claim, _ = self.started()
        row = self.ask(token, attempt_id, claim, 'Why us?', answer='Because').get_json()['question']
        updated = self.client.post(f"/api/question_bank/{row['id']}",
                                   json={'answer': 'Better reason', 'safe_to_autofill': True}).get_json()['answer']
        self.assertEqual((updated['answer'], updated['source'], updated['safe_to_autofill']),
                         ('Better reason', 'user_ui', True))
        self.assertEqual(self.client.post(f"/api/question_bank/{row['id']}", json={'answer': ''}).status_code, 400)
        self.assertEqual(self.client.delete(f"/api/question_bank/{row['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/question_bank/{row['id']}").status_code, 404)

    def test_other_users_rows_are_not_reachable(self):
        self.db.execute(
            "INSERT INTO application_question_bank (id,user_id,question_key,question_text,answer_text,"
            "source,status,created_at,updated_at) VALUES ('x',2,'k','Q','A','user_ui','confirmed','n','n')")
        self.db.commit()
        self.assertEqual(self.client.post('/api/question_bank/x', json={'answer': 'B'}).status_code, 404)
        self.assertEqual(self.client.delete('/api/question_bank/x').status_code, 404)

    def test_confirming_the_last_blocker_requeues_a_waiting_attempt(self):
        token, attempt_id, claim, _ = self.started()
        one = self.ask(token, attempt_id, claim, 'Q one').get_json()['question']
        two = self.ask(token, attempt_id, claim, 'Q two').get_json()['question']
        outcome = self.post(token, attempt_id, 'outcome', claim, outcome='needs_answer',
                            question_ids=[one['id'], two['id']])
        self.assertFalse(outcome.get_json()['requeued'])
        self.assertEqual(self.attempt_row(attempt_id)['state'], 'needs_user_input')
        self.client.post(f"/api/question_bank/{one['id']}", json={'answer': 'A'})
        self.assertEqual(self.attempt_row(attempt_id)['state'], 'needs_user_input')
        self.client.post(f"/api/question_bank/{two['id']}", json={'answer': 'B'})
        row = self.attempt_row(attempt_id)
        self.assertEqual(row['state'], 'queued_for_agent')
        self.assertIsNone(row['claim_id'])

    def test_needs_answer_with_everything_already_answered_requeues_at_once(self):
        token, attempt_id, claim, _ = self.started()
        one = self.ask(token, attempt_id, claim, 'Q one').get_json()['question']
        self.client.post(f"/api/question_bank/{one['id']}", json={'answer': 'A'})  # John answered while held
        outcome = self.post(token, attempt_id, 'outcome', claim, outcome='needs_answer', question_ids=[one['id']])
        self.assertTrue(outcome.get_json()['requeued'])
        self.assertEqual(self.attempt_row(attempt_id)['state'], 'queued_for_agent')

    def test_questions_need_a_live_claim(self):
        token, attempt_id, claim, _ = self.started()
        self.post(token, attempt_id, 'outcome', claim, outcome='needs_login')
        self.assertEqual(self.ask(token, attempt_id, claim, 'Late question').status_code, 409)


class ReviewApprovalOutcomeTests(AgentTestCase):
    def test_approval_is_refused_without_a_review(self):
        token, attempt_id, claim, _ = self.started()
        self.assertEqual(self.post(token, attempt_id, 'approval', claim, note='yes', review_sha='x').status_code, 409)

    def test_approval_is_refused_with_a_missing_or_wrong_hash_or_note(self):
        token, attempt_id, claim, sha = self.reviewed()
        self.assertEqual(self.post(token, attempt_id, 'approval', claim, note='yes').status_code, 409)
        self.assertEqual(self.post(token, attempt_id, 'approval', claim, note='yes', review_sha='bad').status_code, 409)
        self.assertEqual(self.post(token, attempt_id, 'approval', claim, note='', review_sha=sha).status_code, 400)
        self.assertEqual(self.attempt_row(attempt_id)['state'], 'ready_for_review')

    def test_eeo_must_be_listed_before_approval_when_opted_in(self):
        self.set_profile(eeo={'opt_in': True, 'gender': 'male'})
        token, attempt_id, claim, sha = self.reviewed(eeo_listed=False)
        refused = self.post(token, attempt_id, 'approval', claim, note='yes', review_sha=sha)
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.get_json()['code'], 'eeo_not_listed')
        listed = self.post(token, attempt_id, 'review', claim, summary='Fields filled', filled_fields=[],
                           eeo_listed=True).get_json()['review_sha']
        self.assertEqual(self.post(token, attempt_id, 'approval', claim, note='yes', review_sha=listed).status_code, 200)

    def test_approval_is_idempotent_only_for_the_same_note(self):
        token, attempt_id, claim, sha = self.reviewed()
        self.assertEqual(self.post(token, attempt_id, 'approval', claim, note='go', review_sha=sha).status_code, 200)
        self.assertEqual(self.post(token, attempt_id, 'approval', claim, note='go', review_sha=sha).status_code, 200)
        self.assertEqual(self.post(token, attempt_id, 'approval', claim, note='other', review_sha=sha).status_code, 409)
        self.assertEqual(self.attempt_row(attempt_id)['state'], 'awaiting_final_confirmation')

    def test_a_new_review_clears_an_earlier_approval(self):
        token, attempt_id, claim, sha = self.reviewed()
        self.post(token, attempt_id, 'approval', claim, note='go', review_sha=sha)
        self.post(token, attempt_id, 'review', claim, summary='Changed', filled_fields=[])
        row = self.attempt_row(attempt_id)
        self.assertEqual(row['state'], 'ready_for_review')
        self.assertIsNone(row['user_approved_at'])
        stale = self.post(token, attempt_id, 'approval', claim, note='go', review_sha=sha)
        self.assertEqual(stale.status_code, 409)

    def test_submitted_is_refused_without_approval_and_writes_one_tracker_note(self):
        token, attempt_id, claim, sha = self.reviewed()
        refused = self.post(token, attempt_id, 'outcome', claim, outcome='submitted')
        self.assertEqual(refused.status_code, 409)
        self.assertNotIn('notes_log', self.tracker())
        self.post(token, attempt_id, 'approval', claim, note='yes', review_sha=sha)
        with mock.patch.object(api, '_worker_request', side_effect=AssertionError('worker')):
            done = self.post(token, attempt_id, 'outcome', claim, outcome='submitted', message='Thanks')
            repeat = self.post(token, attempt_id, 'outcome', claim, outcome='submitted')
        self.assertEqual(done.status_code, 200)
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(self.attempt_row(attempt_id)['state'], 'submitted')
        record = self.tracker()
        self.assertEqual((record['stage'], record['status']), ('applied', 'applied'))
        self.assertEqual(record['applied_date'], datetime.now().date().isoformat())
        notes = record['notes_log']
        self.assertEqual(len(notes), 1)
        self.assertIn(attempt_id, notes[0]['text'])
        self.assertEqual(notes[0]['source'], 'agent')
        self.assertIsNone(self.attempt_row(attempt_id)['lease_expires_at'])

    def test_tracker_note_names_the_resume_version_from_the_review(self):
        token, attempt_id, claim, _ = self.started()
        doc = role_documents.current_document(self.db, 1, 'preset:10', 'resume', 'txt')
        sha = self.post(token, attempt_id, 'review', claim, summary='ok', filled_fields=[],
                        resume_document_id=doc['document_id']).get_json()['review_sha']
        self.post(token, attempt_id, 'approval', claim, note='yes', review_sha=sha)
        self.post(token, attempt_id, 'outcome', claim, outcome='submitted')
        self.assertIn(f"resume Sysadmin v{doc['version']}", self.tracker()['notes_log'][0]['text'])

    def test_review_rejects_a_document_from_another_role(self):
        token, attempt_id, claim, _ = self.started()
        other = role_documents.sync_resume_text(self.db, 1, 12, 'Ops', 'ops text')
        response = self.post(token, attempt_id, 'review', claim, summary='ok', filled_fields=[],
                             resume_document_id=other['id'])
        self.assertEqual(response.status_code, 400)

    def test_stage_is_never_moved_backward_or_out_of_a_closed_state(self):
        for stage in ('interviewing', 'offer', 'rejected', 'closed'):
            with self.subTest(stage=stage):
                key = f'job-{stage}'
                self.add_job(key, job(title=stage))
                record = self.tracker(key)
                record.update(stage=stage, status='applied', applied_date='2026-09-01')
                self.db.execute('UPDATE application_records SET record_json=? WHERE job_key=?',
                                (json.dumps(record), key))
                self.db.commit()
                token = self.make_key()
                self.queue(key)
                packet = self.claim(token).get_json()['packet']
                attempt_id, claim = packet['attempt']['id'], packet['attempt']['claim_id']
                sha = self.post(token, attempt_id, 'review', claim, summary='ok', filled_fields=[]).get_json()['review_sha']
                self.post(token, attempt_id, 'approval', claim, note='yes', review_sha=sha)
                self.post(token, attempt_id, 'outcome', claim, outcome='submitted')
                after = self.tracker(key)
                self.assertEqual(after['stage'], stage)
                self.assertEqual(after['applied_date'], '2026-09-01')
                self.assertEqual(len(after['notes_log']), 1)

    def test_other_tracker_rows_are_untouched(self):
        self.add_job('bystander', job(title='Bystander'))
        before = self.tracker('bystander')
        token, attempt_id, claim, sha = self.reviewed()
        self.post(token, attempt_id, 'approval', claim, note='yes', review_sha=sha)
        self.post(token, attempt_id, 'outcome', claim, outcome='submitted')
        self.assertEqual(self.tracker('bystander'), before)

    def test_submit_failed_from_awaiting_confirmation(self):
        token, attempt_id, claim = self.approved()
        response = self.post(token, attempt_id, 'outcome', claim, outcome='submit_failed', message='Captcha at submit')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.attempt_row(attempt_id)['state'], 'submission_failed')
        self.assertEqual(self.tracker()['stage'], 'saved')

    def test_a_repeated_outcome_is_idempotent_with_the_same_claim_and_409_with_another(self):
        for outcome, state in (('needs_login', 'needs_login'), ('captcha', 'blocked_captcha'),
                               ('manual', 'manual_required'), ('could_not_fill', 'could_not_fill')):
            with self.subTest(outcome=outcome):
                self.db.execute('DELETE FROM application_attempts')
                self.db.commit()
                token, attempt_id, claim, _ = self.started()
                self.assertEqual(self.post(token, attempt_id, 'outcome', claim, outcome=outcome).status_code, 200)
                self.assertEqual(self.attempt_row(attempt_id)['state'], state)
                self.assertEqual(self.post(token, attempt_id, 'outcome', claim, outcome=outcome).status_code, 200)
                self.assertEqual(self.post(token, attempt_id, 'outcome', 'other', outcome=outcome).status_code, 409)
                row = self.attempt_row(attempt_id)
                self.assertIsNone(row['lease_expires_at'])
                self.assertEqual(row['claim_id'], claim)

    def test_a_different_outcome_after_a_terminal_state_is_409(self):
        token, attempt_id, claim, _ = self.started()
        self.post(token, attempt_id, 'outcome', claim, outcome='manual')
        self.assertEqual(self.post(token, attempt_id, 'outcome', claim, outcome='could_not_fill').status_code, 409)
        self.assertEqual(self.post(token, attempt_id, 'outcome', claim, outcome='nonsense').status_code, 400)

    def test_cancelled_by_the_portal_stops_the_agent(self):
        token, attempt_id, claim, _ = self.started()
        self.client.post(f'/api/application_attempts/{attempt_id}/cancel')
        self.assertEqual(self.post(token, attempt_id, 'progress', claim, message='x').status_code, 409)

    def test_no_agent_endpoint_can_submit(self):
        rules = [str(rule) for rule in self.app.url_map.iter_rules()]
        self.assertFalse([r for r in rules if '/agent' in r and 'submit' in r])


class PrivacyCopyTests(AgentTestCase):
    def test_portal_no_longer_says_self_identification_never_reaches_an_ai_provider(self):
        from pathlib import Path
        page = (Path(__file__).resolve().parents[1] / 'templates' / 'portal.html').read_text(encoding='utf-8')
        self.assertNotIn('never sent to an AI provider', page)
        self.assertIn('desktop session', page)


if __name__ == '__main__':
    unittest.main()
