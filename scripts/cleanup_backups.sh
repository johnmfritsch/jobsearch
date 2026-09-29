#!/opt/bin/bash
#
# cleanup_backups.sh — JobSearch backup retention
#
# 1. Prod deploy tarballs (prod backups/): keep newest KEEP_TARBALLS
# 2. Per-file .backup_* copies (dev + prod backups/): delete older than KEEP_FILE_DAYS days
#
# Called automatically at the end of deploy_to_prod.sh; safe to run manually:
#   /opt/bin/bash /volume1/Web/JobSearch_dev/scripts/cleanup_backups.sh

TARBALL_DIR="/volume1/Web/JobSearch/backups"
FILE_BACKUP_DIRS="/volume1/Web/JobSearch_dev/backups /volume1/Web/JobSearch/backups"
KEEP_TARBALLS=5
KEEP_FILE_DAYS=30

echo "Backup cleanup (keep $KEEP_TARBALLS tarballs, $KEEP_FILE_DAYS days of file backups)..."

# --- Prod deploy tarballs: keep newest N ---
if [ -d "$TARBALL_DIR" ]; then
    ls -1t "$TARBALL_DIR"/jobsearch_prod_*.tar.gz 2>/dev/null | tail -n +"$((KEEP_TARBALLS + 1))" | while read -r f; do
        rm -f "$f" && echo "  pruned tarball: $(basename "$f")"
    done
    COUNT=$(ls -1 "$TARBALL_DIR"/jobsearch_prod_*.tar.gz 2>/dev/null | wc -l)
    echo "  tarballs remaining: $COUNT"
fi

# --- Per-file .backup_* copies: delete older than N days ---
for d in $FILE_BACKUP_DIRS; do
    [ -d "$d" ] || continue
    N=$(find "$d" -maxdepth 1 -name "*.backup_*" -mtime +"$KEEP_FILE_DAYS" 2>/dev/null | wc -l)
    if [ "$N" -gt 0 ]; then
        find "$d" -maxdepth 1 -name "*.backup_*" -mtime +"$KEEP_FILE_DAYS" -exec rm -f {} \;
        echo "  deleted $N file backup(s) older than ${KEEP_FILE_DAYS}d in $d"
    fi
done
echo "  Backup cleanup done."
