#!/usr/bin/env python3
"""
stamp_changelog.py — Promote [Unreleased] to a versioned entry in CHANGELOG.md.

Usage: python3 stamp_changelog.py <path/to/CHANGELOG.md>

Called by deploy_to_prod.sh at deploy time.
- If [Unreleased] has content: promotes it to a versioned entry, inserts fresh empty [Unreleased] at top.
- If [Unreleased] is empty: prints "Nothing to stamp" and exits cleanly.

Version scheme (x.y.z), starting at v0.0.1:
- < 10 bullet items  → patch increment:  0.0.1 → 0.0.2
- >= 10 bullet items → minor increment:  0.x.y → 0.(x+1).0
"""
import re
import sys
from datetime import date


def count_bullets(lines):
    """Count non-empty bullet items in the section."""
    return sum(1 for l in lines if re.match(r'^- \S', l))


def has_content(lines):
    """Return True if there is at least one non-empty bullet item."""
    return count_bullets(lines) > 0


def parse_version(text):
    """Return the highest (major, minor, patch) tuple found in the changelog."""
    hits = re.findall(r'^## \[v(\d+)\.(\d+)(?:\.(\d+))?\b', text, re.MULTILINE)
    if not hits:
        return None
    versions = [(int(a), int(b), int(c) if c else 0) for a, b, c in hits]
    return max(versions)


def next_version(text, bullet_count):
    """Compute the next version string based on bullet count."""
    current = parse_version(text)
    if current is None:
        return 'v0.0.1'
    major, minor, patch = current
    if bullet_count >= 10:
        return f'v{major}.{minor + 1}.0'
    else:
        return f'v{major}.{minor}.{patch + 1}'


def bump_reason(bullet_count):
    """Human-readable explanation for deploy previews."""
    if bullet_count >= 10:
        return f'Minor bump: {bullet_count} release items meets the 10-item threshold'
    return f'Patch bump: {bullet_count} release items is below the 10-item threshold'


def stamp(changelog_path):
    with open(changelog_path, 'r', encoding='utf-8') as f:
        text = f.read()

    lines = text.splitlines(keepends=True)

    unreleased_start = None
    next_section_start = None

    for i, line in enumerate(lines):
        if re.match(r'^## \[Unreleased\]', line):
            unreleased_start = i
        elif unreleased_start is not None and re.match(r'^## \[', line):
            next_section_start = i
            break

    if unreleased_start is None:
        print('  No [Unreleased] section found — skipping changelog stamp.')
        return

    unreleased_body = lines[unreleased_start + 1:next_section_start]

    if not has_content(unreleased_body):
        print('  Nothing to stamp — [Unreleased] section is empty.')
        return

    bullets = count_bullets(unreleased_body)
    version = next_version(text, bullets)
    today = date.today().strftime('%B %-d, %Y') if sys.platform != 'win32' else date.today().strftime('%B %d, %Y').replace(' 0', ' ')
    new_heading = f'## [{version} — {today}]\n'

    fresh_unreleased = (
        '## [Unreleased]\n'
        '### Added\n'
        '### Fixed\n'
        '### Changed\n'
        '\n'
        '---\n'
        '\n'
    )

    before = ''.join(lines[:unreleased_start])
    versioned_block = new_heading + ''.join(unreleased_body)
    after = ''.join(lines[next_section_start:]) if next_section_start else ''

    new_text = before + fresh_unreleased + versioned_block + after

    with open(changelog_path, 'w', encoding='utf-8') as f:
        f.write(new_text)

    print(f'  Stamped [{version} — {today}] in {changelog_path}')


def preview(changelog_path):
    """Print what version would be stamped without modifying the file."""
    with open(changelog_path, 'r', encoding='utf-8') as f:
        text = f.read()

    lines = text.splitlines(keepends=True)

    unreleased_start = None
    next_section_start = None
    for i, line in enumerate(lines):
        if re.match(r'^## \[Unreleased\]', line):
            unreleased_start = i
        elif unreleased_start is not None and re.match(r'^## \[', line):
            next_section_start = i
            break

    if unreleased_start is None:
        print('  No [Unreleased] section found.')
        return

    unreleased_body = lines[unreleased_start + 1:next_section_start]

    if not has_content(unreleased_body):
        print('  [Unreleased] is empty — nothing will be stamped.')
        return

    bullets = count_bullets(unreleased_body)
    version = next_version(text, bullets)
    today = date.today().strftime('%B %-d, %Y') if sys.platform != 'win32' else date.today().strftime('%B %d, %Y').replace(' 0', ' ')
    print(f'  Changelog will be stamped as: [{version} — {today}] ({bullets} change(s))')
    print(f'  {bump_reason(bullets)}')
    print('  Pending changes:')
    for line in unreleased_body:
        stripped = line.rstrip()
        if stripped:
            print(f'    {stripped}')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--preview':
        preview(sys.argv[2])
    elif len(sys.argv) == 2:
        stamp(sys.argv[1])
    else:
        print(f'Usage: {sys.argv[0]} [--preview] <changelog_path>')
        sys.exit(1)
