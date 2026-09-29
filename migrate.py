#!/usr/bin/env python3
"""
JobSearch Migration Runner
Automatically discovers and runs database migrations in order

--db/--migrations default to THIS app's own environment (via config.py's
JOBSEARCH_ENV resolution — the same rule database.py follows), not a fixed
path. Before this, the defaults were hardcoded to JobSearch_dev's absolute
path, so running `migrate.py status` from PROD with no flags silently
reported DEV's migration status instead of erroring, and sqlite auto-creates
a fresh empty file on connect, so a bare `migrate.py up` from PROD could
silently create and migrate a stray dev-named database instead of touching
the real one. Explicit --db/--migrations still override this, unchanged.
"""
import os
import sqlite3
import sys
import importlib.util
from datetime import datetime
from pathlib import Path

class MigrationRunner:
    def __init__(self, db_path, migrations_dir):
        self.db_path = db_path
        self.migrations_dir = Path(migrations_dir)
        self.db = None

    def connect(self):
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self._ensure_migrations_table()

    def _ensure_migrations_table(self):
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT UNIQUE NOT NULL,
                description TEXT,
                applied_at TEXT NOT NULL,
                execution_time_ms INTEGER
            )
        ''')
        self.db.execute('CREATE INDEX IF NOT EXISTS idx_migrations_version ON schema_migrations(version)')
        self.db.commit()

    def discover_migrations(self):
        migrations = []
        if not self.migrations_dir.exists():
            return migrations
        for file in sorted(self.migrations_dir.glob('[0-9][0-9][0-9]_*.py')):
            try:
                spec = importlib.util.spec_from_file_location(file.stem, file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                migrations.append({
                    'file': file,
                    'version': module.VERSION,
                    'description': module.DESCRIPTION,
                    'up': module.up,
                    'down': getattr(module, 'down', None),
                    'verify': getattr(module, 'verify', None)
                })
            except Exception as e:
                print(f"  x Error loading {file.name}: {e}")
        return migrations

    def get_applied_migrations(self):
        cursor = self.db.execute('SELECT version FROM schema_migrations ORDER BY version')
        return [row['version'] for row in cursor.fetchall()]

    def get_pending_migrations(self):
        all_migrations = self.discover_migrations()
        applied = set(self.get_applied_migrations())
        return [m for m in all_migrations if m['version'] not in applied]

    def run_migration(self, migration, dry_run=False):
        version = migration['version']
        description = migration['description']
        print(f"\n{'[DRY RUN] ' if dry_run else ''}Running migration {version}: {description}")
        if dry_run:
            print("  (Migration would run here)")
            return True
        start_time = datetime.now()
        try:
            migration['up'](self.db)
            if migration['verify']:
                if not migration['verify'](self.db):
                    raise Exception("Migration verification failed")
            end_time = datetime.now()
            execution_time = int((end_time - start_time).total_seconds() * 1000)
            self.db.execute('''
                INSERT INTO schema_migrations (version, description, applied_at, execution_time_ms)
                VALUES (?, ?, ?, ?)
            ''', (version, description, datetime.now().isoformat(), execution_time))
            self.db.commit()
            print(f"  OK Completed in {execution_time}ms")
            return True
        except Exception as e:
            print(f"  x Failed: {e}")
            self.db.rollback()
            return False

    def run_all_pending(self, dry_run=False):
        pending = self.get_pending_migrations()
        if not pending:
            print("  No pending migrations")
            return True
        print(f"\n  Found {len(pending)} pending migration(s)")
        for migration in pending:
            if not self.run_migration(migration, dry_run):
                print("\n  x Migration failed, stopping")
                return False
        return True

    def status(self):
        all_migrations = self.discover_migrations()
        applied = set(self.get_applied_migrations())
        if not all_migrations:
            print("  No migrations found")
            return
        for migration in all_migrations:
            version = migration['version']
            description = migration['description']
            status = 'Applied' if version in applied else 'Pending'
            symbol = 'v' if version in applied else 'o'
            print(f"  {symbol} {status}  {version}: {description}")
        pending_count = len([m for m in all_migrations if m['version'] not in applied])
        print(f"\n  {len(applied)} applied, {pending_count} pending")

    def close(self):
        if self.db:
            self.db.close()

def _env_default_db():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import Config
    return Config.get_config().DATABASE


def _env_default_migrations():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'migrations')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='JobSearch Migration Runner')
    parser.add_argument('command', choices=['status', 'up'], help='Command to run')
    parser.add_argument('--dry-run', action='store_true', help='Show what would happen without executing')
    parser.add_argument('--db', default=None,
                         help="Database path (default: this app's own JOBSEARCH_ENV-resolved DB, "
                              "same rule database.py uses)")
    parser.add_argument('--migrations', default=None,
                         help='Migrations directory (default: ./migrations next to this script)')
    args = parser.parse_args()
    db_path = args.db if args.db is not None else _env_default_db()
    migrations_dir = args.migrations if args.migrations is not None else _env_default_migrations()

    runner = MigrationRunner(db_path, migrations_dir)
    try:
        runner.connect()
        if args.command == 'status':
            runner.status()
            return 0
        elif args.command == 'up':
            success = runner.run_all_pending(args.dry_run)
            return 0 if success else 1
    except Exception as e:
        print(f"\n  x Error: {e}")
        return 1
    finally:
        runner.close()

if __name__ == '__main__':
    sys.exit(main())
