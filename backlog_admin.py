"""Admin-only Backlog page, rendered by the shared backlog-kit.

The kit (/volume1/util/backlog_kit, installed for the NAS user with pip) owns the item
parser, the validation notices and the page markup; GardenBuddy was its model. The item
files (backlog/items/<id>.md) and backlog.config.json live ONLY in dev, and DEV and PROD
both read that copy by absolute path, so there is exactly one backlog and no promotion
step. This module no longer has a parser of its own (removed 2026-10-02, backlog #24).

Read-only: nothing here writes to the backlog.
"""
from pathlib import Path

from flask import Blueprint, abort, current_app, render_template

from auth import current_user, login_required

backlog_bp = Blueprint('backlog_admin', __name__)

DEFAULT_BACKLOG_ROOT = '/volume1/Web/JobSearch_dev'


def build_page(root):
    """The kit's markup for this backlog; errors are returned, never raised."""
    result = {'error': None, 'backlog_html': ''}
    try:
        # Imported here so a missing or broken kit shows the page's error alert
        # instead of stopping the whole app from starting.
        from backlog_kit import render
        from backlog_kit.config import load_config
        items_dir = Path(root) / 'backlog' / 'items'
        if not items_dir.is_dir():
            raise FileNotFoundError(f'{items_dir} does not exist')
        config, kit_root = load_config(root)
        data = render.collect(kit_root, config)
        result['backlog_html'] = render.render_fragment(data, config, show_heading=False)
    except Exception as exc:  # shown on the page, never a 500
        result['error'] = f'{type(exc).__name__}: {exc}'
    return result


def is_admin(user):
    return bool(user and user['is_admin'])


@backlog_bp.route('/backlog')
@login_required
def backlog():
    if not is_admin(current_user()):
        abort(403)
    root = current_app.config.get('BACKLOG_ROOT', DEFAULT_BACKLOG_ROOT)
    return render_template('admin_backlog.html', **build_page(root))
