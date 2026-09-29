"""Pure shared helpers for priority-employer adapters.

They deliberately do not import the registry or dispatcher: adapters can use
them for attribution checks without creating an import cycle.
"""
import re

from bs4 import BeautifulSoup

from profile_store import normalize_employer_name


def employer_name_matches(candidate, source):
    """Whether a page-reported organization agrees with a saved employer."""
    value = normalize_employer_name(candidate)
    if not value:
        return False
    expected = [source.get('company_name', '')] + list(source.get('company_aliases') or [])
    for name in expected:
        key = normalize_employer_name(name)
        if key and (value == key or value.startswith(key + ' ') or key.startswith(value + ' ')):
            return True
    return False


def html_to_text(value, limit=30000):
    """Keep paragraphs, headings, and list items readable in normalized jobs."""
    soup = BeautifulSoup(str(value or ''), 'html.parser')
    for tag in soup.find_all(['br']):
        tag.replace_with('\n')
    for tag in soup.find_all(['li']):
        tag.insert_before('• ')
        tag.append('\n')
    for tag in soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        tag.append('\n')
    lines = [re.sub(r'\s+', ' ', line).strip() for line in soup.get_text('\n').splitlines()]
    lines = [line for line in lines if line]
    merged, index = [], 0
    while index < len(lines):
        if lines[index] == '•' and index + 1 < len(lines):
            merged.append('• ' + lines[index + 1])
            index += 2
        else:
            merged.append(lines[index])
            index += 1
    return '\n'.join(merged)[:limit]
