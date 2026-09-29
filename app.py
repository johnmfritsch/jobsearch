import os
import secrets
from datetime import timedelta

from flask import Flask

from config import Config
import database

# Load config
config = Config.get_config()


# URL Prefix Middleware — carried over verbatim from GardenBuddy. Sets SCRIPT_NAME
# so url_for() emits prefixed URLs; strips PATH_INFO so routes are defined bare.
class PrefixMiddleware:
    def __init__(self, application, prefix=''):
        self.app = application
        self.prefix = prefix

    def __call__(self, environ, start_response):
        if environ['PATH_INFO'].startswith(self.prefix):
            environ['PATH_INFO'] = environ['PATH_INFO'][len(self.prefix):]
            environ['SCRIPT_NAME'] = self.prefix
            return self.app(environ, start_response)
        else:
            start_response('404 Not Found', [('Content-Type', 'text/plain')])
            return [b'Not Found']


# App factory
def create_app():
    application = Flask(__name__)
    application.config['DATABASE'] = config.DATABASE
    application.config['TEMPLATES_AUTO_RELOAD'] = True
    # api.py passes this to the pipeline subprocess so main.py resolves the same
    # environment the web app is running as.
    application.config['JOBSEARCH_ENV'] = getattr(config, 'ENV', 'development')
    # Werkzeug's own form-body cap defaults to 500 KB — raise it before any
    # view code runs, or uploads/large forms fail with a raw 413.
    application.config['MAX_FORM_MEMORY_SIZE'] = 40 * 1024 * 1024
    # Application automation is deliberately opt-in. Document generation and
    # attempt tracking work without either flag; form preparation/submission do
    # not become active merely because the worker code is installed.
    application.config['APPLICATION_PREP_ENABLED'] = (
        os.environ.get('JOBSEARCH_APPLICATION_PREP_ENABLED', '').lower() == 'true'
    )
    application.config['APPLICATION_SUBMIT_ENABLED'] = (
        os.environ.get('JOBSEARCH_APPLICATION_SUBMIT_ENABLED', '').lower() == 'true'
    )
    default_worker_port = '3010' if getattr(config, 'ENV', '') == 'production' else '3011'
    application.config['APPLICATION_WORKER_URL'] = os.environ.get(
        'JOBSEARCH_APPLICATION_WORKER_URL', f'http://127.0.0.1:{default_worker_port}'
    ).rstrip('/')

    secret_key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'secret_key.txt')
    if not os.path.exists(secret_key_path):
        # This key signs session cookies and password-reset tokens, so it must
        # never be world-readable. os.open with an explicit 0600 beats
        # open()-then-chmod: there is no window where the key sits readable.
        # O_EXCL also settles the race between Gunicorn workers, which each run
        # create_app() and could otherwise both mint a key — leaving one worker
        # reading a half-written file.
        try:
            fd = os.open(secret_key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # another worker won the race; read its key below
        else:
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_hex(32))
    with open(secret_key_path, 'r') as f:
        application.config['SECRET_KEY'] = f.read().strip()

    application.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
    # Cookies are scoped by hostname, not port. DEV and PROD share a hostname,
    # so the default Flask cookie name would make their logins overwrite each
    # other even though the apps listen on different ports.
    cookie_environment = (
        'prod' if application.config['JOBSEARCH_ENV'] == 'production' else 'dev'
    )
    application.config['SESSION_COOKIE_NAME'] = (
        f'jobsearch_{cookie_environment}_session'
    )
    application.config['SESSION_COOKIE_PATH'] = config.URL_PREFIX
    application.config['SESSION_COOKIE_HTTPONLY'] = True
    application.config['SESSION_COOKIE_SECURE'] = True
    # Lax rather than Strict: the password-reset link arrives from an email
    # client, and Strict would drop the cookie on that first cross-site
    # navigation. Lax still blocks cross-site POSTs, which is the CSRF case.
    application.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    os.makedirs(os.path.dirname(config.DATABASE), exist_ok=True)

    from health import health_bp
    from auth import auth_bp
    from api import api_bp
    from portal import portal_bp
    application.register_blueprint(health_bp)
    application.register_blueprint(auth_bp)
    application.register_blueprint(api_bp)
    application.register_blueprint(portal_bp)

    application.teardown_appcontext(database.close_request_db)

    return application


# Bootstrap
app = create_app()
app.wsgi_app = PrefixMiddleware(app.wsgi_app, prefix=config.URL_PREFIX)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7788, debug=config.DEBUG)
