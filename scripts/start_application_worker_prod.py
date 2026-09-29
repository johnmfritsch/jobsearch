#!/usr/bin/env python3
"""Build and start the disabled-by-default PROD application worker container."""

import json
import os
import subprocess

APP_DIR = '/volume1/Web/JobSearch'
PORT = '3010'
CONTAINER = 'jobsearch-application-worker-prod'
IMAGE = 'jobsearch-application-worker'
PID_FILE = os.path.join(APP_DIR, 'application_worker_prod.pid')


def run(*args, capture=False):
    return subprocess.run(args, check=True, text=True,
                          capture_output=capture)


def main():
    if os.path.exists(PID_FILE):
        try:
            pid = int(open(PID_FILE, encoding='utf-8').read().strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            os.remove(PID_FILE)
        else:
            raise SystemExit(f'Application worker is already running (PID {pid})')
    with open(os.path.join(APP_DIR, 'secret_key.txt'), encoding='utf-8') as handle:
        secret = handle.read().strip()
    run('docker', 'build', '-t', IMAGE, os.path.join(APP_DIR, 'application_worker'))
    subprocess.run(['docker', 'rm', '-f', CONTAINER], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = run(
        'docker', 'run', '-d', '--name', CONTAINER, '--restart', 'no',
        '-p', f'127.0.0.1:{PORT}:{PORT}',
        '-e', f'APPLICATION_WORKER_PORT={PORT}',
        '-e', f'APPLICATION_SIGNING_SECRET={secret}',
        '-e', 'APPLICATION_ATS_ALLOWLIST=ashby,zoho_recruit,workday,greenhouse,lever',
        '-v', os.path.join(APP_DIR, 'data', 'application_artifacts') + ':/artifacts:ro',
        IMAGE, capture=True,
    )
    container_id = result.stdout.strip()
    inspect = run('docker', 'inspect', container_id, capture=True)
    pid = int(json.loads(inspect.stdout)[0]['State']['Pid'])
    with open(PID_FILE, 'w', encoding='utf-8') as handle:
        handle.write(str(pid))
    print(f'JobSearch PROD application worker running on localhost:{PORT}; PID {pid}')


if __name__ == '__main__':
    main()
