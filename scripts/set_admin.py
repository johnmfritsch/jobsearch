#!/usr/bin/env python3
"""Grant or revoke the admin flag (users.is_admin) for one account.

The target database is chosen by --env (or JOBSEARCH_ENV), NOT by which copy of
the script you run. It works from either directory:

  python3 scripts/set_admin.py someone@example.com --env production
  python3 scripts/set_admin.py someone@example.com --env development --revoke

Idempotent. Refuses to touch a database that does not exist or has no users
table, so a wrong path can never create an empty file or change anything.
"""
import argparse
import os
import sqlite3
import sys

APPS = {
    'production': '/volume1/Web/JobSearch/data/jobsearch.db',
    'development': '/volume1/Web/JobSearch_dev/data/jobsearch_dev.db',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('email')
    parser.add_argument('--env', choices=sorted(APPS),
                        default=os.environ.get('JOBSEARCH_ENV', 'development'))
    parser.add_argument('--revoke', action='store_true')
    args = parser.parse_args()
    path = APPS[args.env]
    if not os.path.isfile(path):
        print(f'No database at {path}')
        return 2
    value = 0 if args.revoke else 1
    db = sqlite3.connect(path)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
            print(f'{path} has no users table; nothing changed')
            return 2
        row = db.execute('SELECT id,is_admin FROM users WHERE lower(email)=lower(?)',
                         (args.email.strip(),)).fetchone()
        if row is None:
            print(f'No account with that email in {path}')
            return 1
        print(f'{args.env} ({path}): user id {row[0]} is_admin {row[1]} -> {value}')
        db.execute('UPDATE users SET is_admin=? WHERE id=?', (value, row[0]))
        db.commit()
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
