#!/opt/bin/bash
# Retain the five newest PROD deploy tarballs and 30 days of per-file DEV backups.

set -euo pipefail

DEV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROD_DIR="$(dirname "$DEV_DIR")/PROD"
TARBALL_DIR="$PROD_DIR/backups"
KEEP_TARBALLS=5
KEEP_FILE_DAYS=30

echo "Backup cleanup (keep $KEEP_TARBALLS deploy tarballs; $KEEP_FILE_DAYS days of file backups)..."

if [ -d "$TARBALL_DIR" ]; then
    # BusyBox find lacks GNU -printf; ls -t is sufficient for these timestamped files.
    mapfile -t tarballs < <(ls -1t "$TARBALL_DIR"/PROD_backup_*.tar.gz 2>/dev/null || true)
    for ((i=KEEP_TARBALLS; i<${#tarballs[@]}; i++)); do
        rm -f -- "${tarballs[$i]}"
        echo "  pruned tarball: $(basename "${tarballs[$i]}")"
    done
    echo "  deploy tarballs remaining: $(( ${#tarballs[@]} < KEEP_TARBALLS ? ${#tarballs[@]} : KEEP_TARBALLS ))"
fi

for backup_dir in "$DEV_DIR/backups" "$PROD_DIR/backups"; do
    [ -d "$backup_dir" ] || continue
    old_count=$(find "$backup_dir" -maxdepth 1 -type f -name '*.backup_*' -mtime "+$KEEP_FILE_DAYS" -print | wc -l)
    if [ "$old_count" -gt 0 ]; then
        find "$backup_dir" -maxdepth 1 -type f -name '*.backup_*' -mtime "+$KEEP_FILE_DAYS" -delete
        echo "  removed $old_count old file backup(s) from $backup_dir"
    fi
done
