#!/usr/bin/env python3
"""JobSearch — shared Gunicorn launcher for BOTH dev and prod.

Usage:
    python3 start_jobsearch.py dev
    python3 start_jobsearch.py prod

ONE script so the Gunicorn arg list (workers/threads/timeout/--max-requests/
--preload) lives in a single place and can never drift between environments —
change it here and the deploy carries it to prod automatically. Everything that
differs between dev and prod is a single CONFIG entry keyed by the env argument.

IMPORTANT — why the env arg selects BOTH the directory AND JOBSEARCH_ENV together:
config.py picks the database and URL prefix from the JOBSEARCH_ENV / URL_PREFIX
environment variables. If JOBSEARCH_ENV disagreed with the app directory this
launches from, prod code could open the dev DB (or vice-versa). Binding them to
one CONFIG entry makes that mismatch impossible.

Callers (all pass ONLY the env arg — no env vars required, so the reboot boot
hook `custom_startup.sh` works unchanged in spirit):
  - scripts/restart_jobsearch_{dev,prod}.sh
  - /volume1/util/system/custom_startup.sh   (NAS auto-start after reboot)
"""
import os
import sys
import subprocess

# ── Per-environment configuration (the ONLY things that differ) ───────────────
CONFIG = {
    'dev': {
        'app_dir':    '/volume1/Web/JobSearch_dev',
        'port':       '7788',
        'url_prefix': '/JobSearch_dev',
        'app_env':    'development',
        'pid_name':   'jobsearch_dev.pid',
    },
    'prod': {
        'app_dir':    '/volume1/Web/JobSearch',
        'port':       '7787',
        'url_prefix': '/JobSearch',
        'app_env':    'production',
        'pid_name':   'jobsearch_prod.pid',
    },
}

# ── Shared, environment-independent settings ──────────────────────────────────
PYTHON   = '/volume1/.@plugins/AppCentral/python3/bin/python3'
# The ADM plugin can replace bin/ wrappers during updates.  Resolve Gunicorn
# through this interpreter so the persistent user-installed module is used.
GUNICORN = (PYTHON, '-m', 'gunicorn')
CERT     = '/usr/local/AppCentral/httpd-2.4.43/data/certificate/server.crt'
KEY      = '/usr/local/AppCentral/httpd-2.4.43/data/certificate/server.key'


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in CONFIG:
        sys.stderr.write('Usage: start_jobsearch.py {dev|prod}\n')
        sys.exit(2)

    env = sys.argv[1]
    cfg = CONFIG[env]
    app_dir  = cfg['app_dir']
    pid_file = os.path.join(app_dir, cfg['pid_name'])

    # config.py reads these to select DB + URL prefix — set BEFORE launching gunicorn.
    os.environ['JOBSEARCH_ENV'] = cfg['app_env']
    os.environ['URL_PREFIX'] = cfg['url_prefix']

    try:
        proc = subprocess.Popen([
            *GUNICORN,
            '--bind',        '0.0.0.0:' + cfg['port'],
            '--workers',     '4',
            '--threads',     '4',
            '--worker-class','gthread',
            '--certfile',    CERT,
            '--keyfile',     KEY,
            '--timeout',     '120',
            # Recycle each worker after ~1000 requests (+/- jitter) so a slowly-
            # accumulating stuck thread / resource leak can't wedge a worker forever.
            # --preload means the recycled worker re-forks from the already-imported
            # master, so cold-start cost is minimal.
            '--max-requests','1000',
            '--max-requests-jitter','100',
            '--preload',
            '--chdir',       app_dir,
            'app:app',
        ], env={**os.environ, 'PYTHONPATH': app_dir})
        # Write Gunicorn MASTER pid (proc.pid), not a wrapper pid, so stop scripts
        # kill the process that actually holds the port.
        with open(pid_file, 'w') as f:
            f.write(str(proc.pid))
        print(f'JobSearch {env} server running on '
              f'https://0.0.0.0:{cfg["port"]} (Gunicorn). PID {proc.pid}')
        proc.wait()
    finally:
        if os.path.exists(pid_file):
            os.remove(pid_file)


if __name__ == '__main__':
    main()
