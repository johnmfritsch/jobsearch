"""Page routes for the JobSearch portal.

Every page is served through Flask and gated by @login_required. That is the
structural fix for the old design's second auth surface: results used to be
generated as static HTML under /JobSearch/<User>/ and served straight off disk
by Apache, so anyone with the URL could read them. There is no longer a path
to a result page that does not pass through this decorator.
"""
from flask import Blueprint, redirect, render_template, url_for

from auth import current_user, login_required

portal_bp = Blueprint('portal', __name__)


@portal_bp.route('/')
def index():
    if current_user():
        return redirect(url_for('portal.dashboard'))
    return redirect(url_for('auth.login'))


@portal_bp.route('/dashboard')
@login_required
def dashboard():
    user = current_user()
    return render_template('portal.html',
                           display_name=user['display_name'],
                           profile_key=user['profile_key'])
