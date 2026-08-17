#!/opt/bin/bash
# Read-only release gate: syntax checks and a required Unreleased changelog entry.

set -euo pipefail

BASE_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-/opt/bin/python3}"
CHANGELOG="$BASE_DIR/CHANGELOG.md"
DEV_STATIC="/volume1/Web/johnmfritsch/JobSearch/DEV/static"
PROD_STATIC="/volume1/Web/johnmfritsch/JobSearch/static"

fail() { echo "  [FAIL] $*" >&2; exit 1; }

[ -d "$BASE_DIR" ] || fail "Directory not found: $BASE_DIR"
[ -f "$CHANGELOG" ] || fail "Missing CHANGELOG.md"
[ -x "$PYTHON" ] || fail "Python not found: $PYTHON"

echo "  Checking DEV/PROD static-asset isolation..."
for directory in "$DEV_STATIC" "$PROD_STATIC"; do
    [ -d "$directory" ] || fail "Static directory not found: $directory"
    [ ! -L "$directory" ] || fail "Static directory must not be a symlink: $directory"
done
for asset in jobsearch.js modal.css; do
    dev_asset="$DEV_STATIC/$asset"
    prod_asset="$PROD_STATIC/$asset"
    [ -f "$dev_asset" ] || fail "Missing DEV static asset: $dev_asset"
    [ -f "$prod_asset" ] || fail "Missing PROD static asset: $prod_asset"
    [ ! -L "$dev_asset" ] || fail "DEV static asset must not be a symlink: $dev_asset"
    [ ! -L "$prod_asset" ] || fail "PROD static asset must not be a symlink: $prod_asset"
    dev_inode=$(/bin/ls -i "$dev_asset" | /usr/bin/awk '{print $1}')
    prod_inode=$(/bin/ls -i "$prod_asset" | /usr/bin/awk '{print $1}')
    [ "$dev_inode" != "$prod_inode" ] || fail "DEV and PROD must not hard-link the same static asset: $asset"
done
echo "  [OK] DEV and PROD static assets are isolated copies"

echo "  Checking Python syntax..."
"$PYTHON" - "$BASE_DIR" <<'PY'
import sys
from pathlib import Path

base = Path(sys.argv[1])
excluded = {"backups", "logs", "TRASH", "__pycache__", ".git"}
files = [p for p in base.rglob("*.py") if not any(part in excluded for part in p.parts)]
failed = []
for path in files:
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        failed.append(f"{path.relative_to(base)}: {exc}")
if failed:
    print("\n".join(failed), file=sys.stderr)
    raise SystemExit(1)
print(f"  [OK] {len(files)} Python file(s) parsed")
PY

echo "  Checking shell syntax..."
shell_count=0
while IFS= read -r -d '' script; do
    /opt/bin/bash -n "$script"
    shell_count=$((shell_count + 1))
done < <(find "$BASE_DIR" -type f -name '*.sh' -not -path "$BASE_DIR/backups/*" -print0)
echo "  [OK] $shell_count shell script(s) parsed"

if ! /opt/bin/grep -A 80 '^## \[Unreleased\]' "$CHANGELOG" | /opt/bin/grep -qE '^- \S'; then
    fail "CHANGELOG.md has no Unreleased bullet. Add a user-facing Added, Fixed, or Changed entry before deploying."
fi

echo "  [OK] Unreleased changelog entry present"
