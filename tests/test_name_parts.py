"""First / middle / last name handling for the application profile (backlog 19.2 follow-up)."""
import sqlite3
import unittest

import auto_apply
from agent_support import AgentTestCase


class NamePartsTests(unittest.TestCase):
    def split(self, full, **extra):
        return auto_apply.name_parts(dict({'full_name': full}, **extra))

    def test_a_plain_three_word_name_splits_cleanly(self):
        parts = self.split('John M. Fritsch')
        self.assertEqual((parts['first'], parts['middle'], parts['last'], parts['suffix']),
                         ('John', 'M.', 'Fritsch', ''))
        self.assertFalse(parts['ambiguous'])
        self.assertEqual(parts['source'], 'derived')

    def test_two_words_use_a_stored_middle_name(self):
        parts = self.split('John Fritsch', middle_name='Michael')
        self.assertEqual((parts['first'], parts['middle'], parts['last']), ('John', 'Michael', 'Fritsch'))
        self.assertFalse(parts['ambiguous'])

    def test_suffixes_are_pulled_off(self):
        parts = self.split('John M. Fritsch Jr.')
        self.assertEqual((parts['last'], parts['suffix']), ('Fritsch', 'Jr.'))
        self.assertFalse(parts['ambiguous'])

    def test_guesses_are_marked_ambiguous(self):
        for name in ('Cher', 'Fritsch, John M.', 'Mary Jo Anne Smith', 'Juan de la Cruz', 'Ludwig van Beethoven', ''):
            with self.subTest(name=name):
                self.assertTrue(self.split(name)['ambiguous'], name)

    def test_stored_parts_win_and_are_not_ambiguous(self):
        parts = auto_apply.name_parts({'full_name': 'Something Else', 'first_name': 'Mary Jo',
                                       'middle_name': '', 'last_name': 'Smith'})
        self.assertEqual((parts['first'], parts['last'], parts['source'], parts['ambiguous']),
                         ('Mary Jo', 'Smith', 'stored', False))
        self.assertTrue(auto_apply.name_parts({'first_name': 'Cher'})['ambiguous'])


class SaveNameTests(AgentTestCase):
    def test_full_name_is_composed_from_the_three_parts(self):
        saved = auto_apply.save_profile(self.db, 1, {'first_name': ' John ', 'middle_name': 'M.',
                                                      'last_name': 'Fritsch'})['profile']
        self.assertEqual(saved['full_name'], 'John M. Fritsch')
        self.assertEqual((saved['first_name'], saved['middle_name'], saved['last_name']),
                         ('John', 'M.', 'Fritsch'))
        empty_middle = auto_apply.save_profile(self.db, 1, {'first_name': 'A', 'middle_name': '',
                                                             'last_name': 'B'})['profile']
        self.assertEqual(empty_middle['full_name'], 'A B')

    def test_the_parts_win_over_a_stale_full_name_from_an_old_tab(self):
        saved = auto_apply.save_profile(self.db, 1, {'full_name': 'Old Name', 'first_name': 'New',
                                                      'last_name': 'Name'})['profile']
        self.assertEqual(saved['full_name'], 'New Name')

    def test_an_older_tab_that_only_sends_full_name_still_saves(self):
        saved = auto_apply.save_profile(self.db, 1, {'full_name': 'Only Name'})['profile']
        self.assertEqual(saved['full_name'], 'Only Name')
        self.assertNotIn('first_name', saved)

    def test_the_packet_carries_name_parts_for_a_profile_with_only_a_full_name(self):
        auto_apply.save_profile(self.db, 1, {'full_name': 'John M. Fritsch', 'email': 'j@example.com'})
        packet = self.started()[3]
        self.assertEqual({k: packet['name_parts'][k] for k in ('first', 'middle', 'last', 'ambiguous', 'source')},
                         {'first': 'John', 'middle': 'M.', 'last': 'Fritsch', 'ambiguous': False,
                          'source': 'derived'})

    def test_the_packet_uses_stored_parts_and_shares_them(self):
        auto_apply.save_profile(self.db, 1, {'first_name': 'John', 'middle_name': 'M.', 'last_name': 'Fritsch'})
        packet = self.started()[3]
        self.assertEqual(packet['name_parts']['source'], 'stored')
        self.assertEqual((packet['profile']['first_name'], packet['profile']['last_name'], packet['profile']['full_name']),
                         ('John', 'Fritsch', 'John M. Fritsch'))


if __name__ == '__main__':
    unittest.main()
