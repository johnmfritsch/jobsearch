"""Migration 012 — Persist a distinct display color for each saved role."""

import colorsys
import json

VERSION = '012'
DESCRIPTION = 'Add persistent colors to search presets'

ROLE_COLORS = (
    '#2563EB', '#7C3AED', '#0F766E', '#B45309', '#BE123C',
    '#0369A1', '#4D7C0F', '#A21CAF', '#C2410C', '#4338CA',
    '#047857', '#9F1239',
)


def _next_generated_color(seed, used):
    for attempt in range(360):
        hue = ((seed * 137.508) + (attempt * 29)) % 360 / 360
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 0.68)
        color = f'#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}'
        if color.upper() not in used:
            return color
    return '#64748B'


def up(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(search_presets)')}
    if 'color' not in columns:
        db.execute('ALTER TABLE search_presets ADD COLUMN color TEXT')

    users = [row[0] for row in db.execute(
        'SELECT DISTINCT user_id FROM search_presets ORDER BY user_id')]
    for user_id in users:
        presets = list(db.execute(
            'SELECT id,color FROM search_presets WHERE user_id=? ORDER BY id',
            (user_id,)))
        used = {str(row[1]).upper() for row in presets if row[1]}
        available = [color for color in ROLE_COLORS if color.upper() not in used]
        for row in presets:
            if row[1]:
                continue
            color = (available.pop(0) if available else
                     _next_generated_color(len(used), used))
            db.execute('UPDATE search_presets SET color=? WHERE id=?', (color, row[0]))
            used.add(color.upper())

    for profile in db.execute('SELECT user_id,config_json FROM user_profiles'):
        config = json.loads(profile[1] or '{}')
        active_name = str(config.get('active_search_preset_name') or '')
        if not active_name or active_name.casefold() == 'default':
            continue
        preset = db.execute(
            'SELECT id,color FROM search_presets '
            'WHERE user_id=? AND name=? COLLATE NOCASE',
            (profile[0], active_name)).fetchone()
        if preset:
            config['active_search_preset_id'] = preset[0]
            config['active_search_preset_color'] = preset[1]
            db.execute('UPDATE user_profiles SET config_json=? WHERE user_id=?',
                       (json.dumps(config, ensure_ascii=False), profile[0]))
    db.commit()


def verify(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(search_presets)')}
    if 'color' not in columns:
        return False
    missing = db.execute(
        "SELECT count(*) FROM search_presets WHERE color IS NULL OR color=''"
    ).fetchone()[0]
    return missing == 0
