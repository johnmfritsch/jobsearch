from flask import Blueprint

health_bp = Blueprint('health', __name__)


@health_bp.route('/health')
def health():
    """Lightweight liveness/readiness probe — no auth, no DB, no template.
    Used by the restart readiness poll and any keep-alive watchdog to keep workers warm."""
    return 'ok', 200, {'Cache-Control': 'no-store'}
