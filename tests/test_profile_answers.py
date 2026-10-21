"""Backlog 19.1: account-level screening/EEO and role-level work history."""
import importlib.util
import json
import sqlite3
import unittest
from pathlib import Path

import auto_apply
import role_history

ROOT = Path(__file__).resolve().parents[1]


def load_migration(version):
    path = next((ROOT / 'migrations').glob(f'{version}_*.py'))
    spec = importlib.util.spec_from_file_location(f'migration_{version}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def job(employer='Acme', title='Analyst', start='01/2015', end='06/2018', **extra):
    row = {'title': title, 'employer': employer, 'from': start, 'to': end, 'current': False}
    row.update(extra)
    return row


class Base(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, profile_key TEXT);
            CREATE TABLE search_presets (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
                resume_text TEXT NOT NULL DEFAULT '', color TEXT,
                config_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '');
            CREATE TABLE search_runs (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                test_mode INTEGER NOT NULL, state TEXT NOT NULL, status_json TEXT NOT NULL,
                started_at TEXT NOT NULL);
            CREATE TABLE job_results (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL,
                job_key TEXT NOT NULL, job_json TEXT NOT NULL, score REAL);
            CREATE TABLE application_records (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                job_key TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(user_id,job_key));
            INSERT INTO users(id,profile_key) VALUES (1,'one'),(2,'two');
            INSERT INTO search_presets(id,user_id,name) VALUES (10,1,'Sysadmin'),(11,1,'Dev'),(20,2,'Other');
        ''')
        load_migration('013').up(self.db)
        load_migration('022').up(self.db)
        self.default = {'role_key': 'default', 'role_name': 'Default', 'preset_id': None}
        self.sysadmin = {'role_key': 'preset:10', 'role_name': 'Sysadmin', 'preset_id': 10}
        self.dev = {'role_key': 'preset:11', 'role_name': 'Dev', 'preset_id': 11}

    def tearDown(self):
        self.db.close()


class AccountProfileTests(Base):
    def test_old_shape_profile_and_flat_fields_still_work(self):
        self.db.execute('INSERT INTO application_profiles(user_id,profile_json,updated_at) '
                        "VALUES (1,'{\"full_name\":\"A\"}','x')")
        self.assertEqual(auto_apply.load_profile(self.db, 1)['profile'], {'full_name': 'A'})
        saved = auto_apply.save_profile(self.db, 1, {'full_name': 'B' * 400, 'middle_name': 'M' * 200})
        self.assertEqual(len(saved['profile']['full_name']), 160)
        self.assertEqual(len(saved['profile']['middle_name']), 80)
        self.assertNotIn('screening', saved['profile'])

    def test_screening_round_trip_and_validation(self):
        screening = {'current_employee': 'no', 'us_person': 'YES', 'age_18_plus': 'yes',
                     'conflict_of_interest': '', 'shift_availability': 'All shifts', 'notes': 'n'}
        saved = auto_apply.save_profile(self.db, 1, {'screening': screening})['profile']['screening']
        self.assertEqual(saved['us_person'], 'yes')
        self.assertEqual(saved['conflict_of_interest'], '')
        self.assertEqual(saved['former_employee'], '')
        with self.assertRaises(ValueError):
            auto_apply.save_profile(self.db, 1, {'screening': {'us_person': 'maybe'}})
        long = auto_apply.save_profile(self.db, 1, {'screening': {'notes': 'x' * 900}})
        self.assertEqual(len(long['profile']['screening']['notes']), 500)

    def test_sections_are_preserved_when_omitted_and_replaced_when_present(self):
        auto_apply.save_profile(self.db, 1, {
            'screening': {'us_person': 'yes'}, 'eeo': {'opt_in': True, 'gender': 'male'}})
        kept = auto_apply.save_profile(self.db, 1, {'full_name': 'Only name'})['profile']
        self.assertEqual(kept['screening']['us_person'], 'yes')
        self.assertEqual(kept['eeo']['gender'], 'male')
        replaced = auto_apply.save_profile(self.db, 1, {'screening': {'us_person': 'no'}})['profile']
        self.assertEqual(replaced['screening']['us_person'], 'no')
        self.assertEqual(replaced['eeo']['gender'], 'male')

    def test_eeo_requires_opt_in_and_known_choices(self):
        off = auto_apply.save_profile(self.db, 1, {'eeo': {'opt_in': False, 'gender': 'male'}})
        self.assertEqual(off['profile']['eeo'], {'opt_in': False})
        on = auto_apply.save_profile(self.db, 1, {'eeo': {
            'opt_in': True, 'gender': 'male', 'race_ethnicity': 'white',
            'veteran': 'not_a_veteran', 'disability': ''}})
        self.assertEqual(on['profile']['eeo']['race_ethnicity'], 'white')
        self.assertEqual(on['profile']['eeo']['disability'], '')
        with self.assertRaises(ValueError):
            auto_apply.save_profile(self.db, 1, {'eeo': {'opt_in': True, 'gender': 'robot'}})
        again = auto_apply.save_profile(self.db, 1, {'eeo': {'opt_in': False}})
        self.assertEqual(again['profile']['eeo'], {'opt_in': False})

    def test_top_level_demographic_keys_are_still_not_stored(self):
        saved = auto_apply.save_profile(self.db, 1, {'gender': 'x', 'veteran': 'y'})
        self.assertNotIn('gender', saved['profile'])
        self.assertNotIn('veteran', saved['profile'])

    def test_shareable_profile_has_only_contact_fields(self):
        auto_apply.save_profile(self.db, 1, {
            'full_name': 'A', 'middle_name': 'M', 'email': 'a@b.c',
            'screening': {'us_person': 'yes'}, 'eeo': {'opt_in': True, 'gender': 'male'}})
        stored = auto_apply.load_profile(self.db, 1)['profile']
        shared = auto_apply.shareable_profile(stored)
        self.assertEqual(shared, {'full_name': 'A', 'middle_name': 'M', 'email': 'a@b.c'})
        self.assertNotIn('male', json.dumps(shared))

    def test_redact_profile_masks_values_but_not_opt_in(self):
        auto_apply.save_profile(self.db, 1, {
            'full_name': 'A', 'screening': {'us_person': 'yes', 'notes': 'secret note'},
            'eeo': {'opt_in': True, 'gender': 'male', 'veteran': ''}})
        redacted = auto_apply.redact_profile(auto_apply.load_profile(self.db, 1)['profile'])
        text = json.dumps(redacted)
        for leaked in ('male', 'secret note', 'yes'):
            self.assertNotIn(leaked, text)
        self.assertEqual(redacted['eeo']['opt_in'], True)
        self.assertEqual(redacted['eeo']['gender'], auto_apply.REDACTED)
        self.assertEqual(redacted['eeo']['veteran'], '')
        self.assertEqual(redacted['full_name'], 'A')

    def test_saving_writes_no_event_with_profile_values(self):
        auto_apply.save_profile(self.db, 1, {'eeo': {'opt_in': True, 'gender': 'male'}})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM application_events').fetchone()[0], 0)

    def test_profiles_are_user_scoped(self):
        auto_apply.save_profile(self.db, 1, {'screening': {'us_person': 'yes'}})
        self.assertEqual(auto_apply.load_profile(self.db, 2)['profile'], {})


class WorkHistoryValidationTests(Base):
    def save(self, rows, education=(), role=None, verified=False):
        return role_history.save_history(
            self.db, 1, role or self.sysadmin,
            {'work_history': rows, 'education': list(education), 'verified': verified})

    def test_valid_round_trip_assigns_stable_ids(self):
        saved = self.save([job(), job('Beta', 'Dev', '07/2018', '', current=True)])
        rows = saved['work_history']
        self.assertEqual([r['employer'] for r in rows], ['Acme', 'Beta'])
        self.assertTrue(all(r['id'] for r in rows))
        self.assertEqual(rows[1]['to'], '')
        again = role_history.get_history(self.db, 1, 'preset:10')
        self.assertEqual([r['id'] for r in again['work_history']], [r['id'] for r in rows])
        resaved = self.save(again['work_history'])
        self.assertEqual([r['id'] for r in resaved['work_history']], [r['id'] for r in rows[:2]])

    def test_each_rejection_names_the_row(self):
        cases = {
            'start date must be MM/YYYY': [job(start='2015')],
            'start date must be MM/YYYY ': [job(start='13/2015')],
            'end date must be MM/YYYY': [job(end='')],
            'start date is after the end date': [job(start='05/2018', end='01/2015')],
            'job title is required': [job(title='')],
            'employer is required': [job(employer='')],
            'out of range': [job(start='01/1900')],
        }
        for fragment, rows in cases.items():
            with self.assertRaises(ValueError, msg=fragment) as ctx:
                self.save(rows)
            self.assertIn('Work history row 1', str(ctx.exception))
            self.assertIn(fragment.strip(), str(ctx.exception))
        self.assertEqual(role_history.get_history(self.db, 1, 'preset:10')['work_history'], [])

    def test_row_limits(self):
        with self.assertRaises(ValueError):
            self.save([job(f'E{i}', start='01/2000', end='02/2000') for i in range(16)])
        long = self.save([job(title='T' * 400, description='D' * 5000)])['work_history'][0]
        self.assertEqual(len(long['title']), 160)
        self.assertEqual(len(long['description']), 2000)

    def test_overlap_is_a_warning_not_an_error(self):
        saved = self.save([job('A', start='01/2015', end='12/2017'), job('B', start='06/2017', end='12/2019')])
        self.assertEqual(len(saved['work_history']), 2)
        self.assertTrue(any('overlap' in w for w in saved['warnings']))

    def test_same_employer_back_to_back_roles_do_not_warn(self):
        saved = self.save([job('A', 'Dev', '01/2015', '12/2016'), job('A', 'Senior', '01/2016', '12/2018')])
        self.assertFalse(any('overlap' in w for w in saved['warnings']))

    def test_education_validation(self):
        edu = {'level': 'master', 'school': 'Cedar Crest', 'degree': 'MBA', 'graduated': '2009'}
        saved = self.save([], [edu])
        self.assertEqual(saved['education'][0]['level'], 'master')
        for bad in ({'level': 'wizard', 'school': 'X'}, {'level': 'master', 'school': ''},
                    {'level': 'master', 'school': 'X', 'graduated': '20'}):
            with self.assertRaises(ValueError):
                self.save([], [bad])
        with self.assertRaises(ValueError):
            self.save([], [dict(edu) for _ in range(9)])

    def test_verified_flag_is_kept_only_when_stated_and_dates_unchanged(self):
        first = self.save([job()], verified=True)
        stamp = first['verified_at']
        self.assertTrue(stamp)
        unchanged = self.save(first['work_history'], verified=True)
        self.assertEqual(unchanged['verified_at'], stamp)
        unticked = self.save(first['work_history'], verified=False)
        self.assertEqual(unticked['verified_at'], '')

    def test_history_is_user_scoped_and_role_scoped(self):
        self.save([job()])
        self.assertEqual(role_history.get_history(self.db, 2, 'preset:10')['work_history'], [])
        self.assertEqual(role_history.get_history(self.db, 1, 'preset:11')['work_history'], [])

    def test_deleting_a_role_removes_its_history(self):
        self.save([job()])
        self.save([job('Z')], role=self.dev)
        role_history.delete_for_role(self.db, 1, 'preset:10')
        self.assertEqual(role_history.get_history(self.db, 1, 'preset:10')['work_history'], [])
        self.assertEqual(len(role_history.get_history(self.db, 1, 'preset:11')['work_history']), 1)

    def test_user_delete_cascades(self):
        self.save([job()])
        self.db.execute('DELETE FROM users WHERE id=1')
        self.assertEqual(self.db.execute(
            'SELECT COUNT(*) FROM role_application_history').fetchone()[0], 0)


class CopyAndUndoTests(Base):
    def setUp(self):
        super().setUp()
        role_history.save_history(self.db, 1, self.sysadmin, {'work_history': [
            job('Acme', 'Admin', '01/2010', '01/2013'), job('Beta', 'Lead', '02/2013', '02/2016')],
            'education': [{'level': 'bachelor', 'school': 'State U'}]})

    def test_preview_writes_nothing(self):
        preview = role_history.copy_preview(self.db, 1, self.sysadmin, self.dev, 'merge')
        self.assertEqual(len(preview['work_history']), 2)
        self.assertEqual(role_history.get_history(self.db, 1, 'preset:11')['work_history'], [])

    def test_merge_adds_missing_and_flags_conflicts(self):
        role_history.save_history(self.db, 1, self.dev, {'work_history': [
            job('Acme', 'Admin', '03/2010', '01/2013'), job('Gamma', 'Dev', '01/2020', '01/2021')]})
        preview = role_history.copy_preview(self.db, 1, self.sysadmin, self.dev, 'merge')
        by = {(r['employer'], r['status']) for r in preview['work_history']}
        self.assertIn(('Gamma', 'kept'), by)
        self.assertIn(('Acme', 'kept'), by)
        self.assertIn(('Acme', 'conflict'), by)
        self.assertIn(('Beta', 'new'), by)
        conflict = next(r for r in preview['work_history'] if r['status'] == 'conflict')
        self.assertEqual(conflict['copied_from'], 'Sysadmin')
        self.assertEqual(preview['will_remove'], {'work_history': 0, 'education': 0})

    def test_replace_shows_what_will_go(self):
        role_history.save_history(self.db, 1, self.dev, {'work_history': [job('Gamma')]})
        preview = role_history.copy_preview(self.db, 1, self.sysadmin, self.dev, 'replace')
        self.assertEqual([r['employer'] for r in preview['work_history']], ['Acme', 'Beta'])
        self.assertEqual(preview['will_remove']['work_history'], 1)

    def test_preview_rows_must_still_pass_server_validation(self):
        preview = role_history.copy_preview(self.db, 1, self.sysadmin, self.dev, 'replace')
        rows = preview['work_history']
        rows[0]['from'] = 'garbage'
        with self.assertRaises(ValueError):
            role_history.save_history(self.db, 1, self.dev, {'work_history': rows})
        rows[0]['from'] = '01/2010'
        rows.pop()
        saved = role_history.save_history(self.db, 1, self.dev, {'work_history': rows})
        self.assertEqual(len(saved['work_history']), 1)
        self.assertEqual(saved['work_history'][0]['copied_from'], 'Sysadmin')

    def test_copy_rejects_same_role_and_bad_mode(self):
        with self.assertRaises(ValueError):
            role_history.copy_preview(self.db, 1, self.sysadmin, self.sysadmin, 'merge')
        with self.assertRaises(ValueError):
            role_history.copy_preview(self.db, 1, self.sysadmin, self.dev, 'wipe')

    def test_undo_restores_previous_and_is_reversible(self):
        original = role_history.get_history(self.db, 1, 'preset:10')['work_history']
        role_history.save_history(self.db, 1, self.sysadmin, {'work_history': [job('Only')]})
        undone = role_history.undo_history(self.db, 1, self.sysadmin)
        self.assertEqual([r['employer'] for r in undone['work_history']], [r['employer'] for r in original])
        redone = role_history.undo_history(self.db, 1, self.sysadmin)
        self.assertEqual([r['employer'] for r in redone['work_history']], ['Only'])
        with self.assertRaises(LookupError):
            role_history.undo_history(self.db, 1, self.dev)

    def test_cross_role_date_mismatch_warns_and_mentions_copy(self):
        saved = role_history.save_history(self.db, 1, self.dev, {'work_history': [
            job('Acme', 'Admin', '03/2010', '01/2013', copied_from='Sysadmin')]})
        warning = ' '.join(saved['warnings'])
        self.assertIn('Acme', warning)
        self.assertIn('Sysadmin', warning)
        self.assertIn('copied from Sysadmin', warning)

    def test_different_positions_at_one_employer_do_not_warn(self):
        saved = role_history.save_history(self.db, 1, self.dev, {'work_history': [
            job('Acme', 'Other title', '03/2010', '01/2013')]})
        self.assertEqual(saved['warnings'], [])


class ResumeDraftTests(unittest.TestCase):
    RESUME = '\n'.join([
        'Jane Doe', 'EXPERIENCE', 'Senior Analyst | Acme Corp  Allentown, PA', 'Oct 2018 - Apr 2025',
        '- Did things', 'Developer, Beta Inc', '02/2016 - 10/2018', 'Built stuff',
        'Systems Admin | Gamma LLC | 2010 - 2013', 'EDUCATION', 'MBA, Cedar Crest College 2009'])

    def test_draft_rows_and_issues(self):
        draft = role_history.draft_from_resume(self.RESUME)
        work = draft['work_history']
        self.assertEqual([(r['title'], r['employer']) for r in work],
                         [('Senior Analyst', 'Acme Corp'), ('Developer', 'Beta Inc'),
                          ('Systems Admin', 'Gamma LLC')])
        self.assertEqual((work[0]['from'], work[0]['to'], work[0]['location']),
                         ('10/2018', '04/2025', 'Allentown, PA'))
        self.assertEqual(work[0]['issues'], [])
        self.assertTrue(any('start month missing' in i for i in work[2]['issues']))
        self.assertEqual(draft['education'][0]['level'], 'master')
        self.assertEqual(draft['education'][0]['graduated'], '2009')

    def test_employer_heading_above_several_positions(self):
        text = '\n'.join([
            'PROFESSIONAL EXPERIENCE', 'Acme Corp | Allentown, PA', 'Senior Analyst | 2018 – 2025',
            '• did things', 'wrapped sentence continues here.', 'Developer | 2016 – 2018', '• built',
            'Beta Hospital | Easton, PA', 'Subject Expert – Interfaces | 1992 – 2009',
            'EDUCATION', 'Bachelor of Science (BS), Computer Science | State University, Center Valley, PA'])
        draft = role_history.draft_from_resume(text)
        self.assertEqual(
            [(r['title'], r['employer'], r['location']) for r in draft['work_history']],
            [('Senior Analyst', 'Acme Corp', 'Allentown, PA'), ('Developer', 'Acme Corp', 'Allentown, PA'),
             ('Subject Expert – Interfaces', 'Beta Hospital', 'Easton, PA')])
        edu = draft['education'][0]
        self.assertEqual((edu['level'], edu['school'], edu['degree'], edu['field'], edu['location']),
                         ('bachelor', 'State University', 'Bachelor of Science (BS)',
                          'Computer Science', 'Center Valley, PA'))

    def test_present_is_current_and_empty_text_is_safe(self):
        work = role_history.draft_from_resume('Dev | Co\nJan 2020 - Present')['work_history']
        self.assertTrue(work[0]['current'])
        self.assertEqual(role_history.draft_from_resume('')['work_history'], [])
        self.assertEqual(role_history.draft_from_resume(None)['education'], [])

    def test_a_draft_incomplete_row_cannot_be_saved_until_fixed(self):
        draft = role_history.draft_from_resume(self.RESUME)['work_history'][2]
        with self.assertRaises(ValueError):
            role_history.clean_history({'work_history': [draft]})


class MigrationTests(unittest.TestCase):
    def test_up_and_down(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        migration = load_migration('022')
        migration.up(db)
        migration.up(db)  # idempotent
        self.assertTrue(db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='role_application_history'").fetchone())
        migration.down(db)
        self.assertFalse(db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='role_application_history'").fetchone())


if __name__ == '__main__':
    unittest.main()
