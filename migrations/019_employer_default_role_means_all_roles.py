"""Migration 019 -- retire the "Default" pseudo-role for priority employers.

017 modeled per-role membership as (source_id, preset_id) rows with
preset_id=0 standing in for the unsaved "Default" role. In the UI that
shipped, "Default" read to users as "applies broadly" -- not "applies only to
the one specific unsaved session state" -- so anyone who checked it (John did,
for 3 real employers) got behavior that looked wrong the moment they switched
to a named role. The fix drops the pseudo-role: "applies everywhere" is now
only ever the source's own enabled_all_roles column. This backfills existing
preset_id=0 membership into that column, matching what those users actually
expected, then removes the now-meaningless rows.
"""
from datetime import datetime

VERSION = '019'
DESCRIPTION = 'Employer "Default" role membership now means enabled_all_roles'


def up(db):
    now = datetime.utcnow().isoformat()
    source_ids = [row['source_id'] for row in db.execute(
        'SELECT DISTINCT source_id FROM employer_source_roles WHERE preset_id=0')]
    for source_id in source_ids:
        db.execute('UPDATE employer_sources SET enabled_all_roles=1,updated_at=? WHERE id=?',
                   (now, source_id))
    db.execute('DELETE FROM employer_source_roles WHERE preset_id=0')
    db.commit()


def verify(db):
    remaining = db.execute(
        'SELECT COUNT(*) FROM employer_source_roles WHERE preset_id=0').fetchone()[0]
    return remaining == 0


def down(db):
    # The preset_id=0 rows this removed can't be reconstructed (their source
    # rows may have since been edited or gained new role_ids since the
    # backfill ran) -- nothing to safely reverse.
    pass
