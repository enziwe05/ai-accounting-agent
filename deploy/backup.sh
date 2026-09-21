#!/usr/bin/env bash
# Nightly backup of ALL company databases into one compressed file, keeping the
# last 14 days. Set up as a cron job in DEPLOY.md.
#
# Assumes the project lives at /opt/bookkeeper. Change PROJECT_DIR if not.
set -euo pipefail

PROJECT_DIR=/opt/bookkeeper
BACKUP_DIR="$PROJECT_DIR/backups"
KEEP_DAYS=14
STAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUP_DIR"

# Dump every database straight out of the running MySQL container. The container
# already knows MYSQL_ROOT_PASSWORD from env/db.env, so no password is written here.
docker compose -f "$PROJECT_DIR/docker-compose.yml" exec -T db \
  sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --all-databases --single-transaction --routines --events' \
  | gzip > "$BACKUP_DIR/all-databases-$STAMP.sql.gz"

# Remove backups older than KEEP_DAYS.
find "$BACKUP_DIR" -name 'all-databases-*.sql.gz' -mtime +"$KEEP_DAYS" -delete

echo "Backup written: $BACKUP_DIR/all-databases-$STAMP.sql.gz"
