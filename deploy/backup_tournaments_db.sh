#!/bin/bash
# Backup script for tournaments.db
# Creates timestamped compressed backups and maintains retention policy

DB_PATH="/home/discord/radio-ravnica/data/tournaments.db"
BACKUP_DIR="/home/discord/radio-ravnica/data/backups"
RETENTION_DAYS=30

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Create timestamped backup
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/tournaments.db.backup_$TIMESTAMP.gz"

# Backup the database (compress on the fly)
gzip -c "$DB_PATH" > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "Backup created: $BACKUP_FILE"

    # Remove backups older than RETENTION_DAYS
    find "$BACKUP_DIR" -name "tournaments.db.backup_*.gz" -mtime +$RETENTION_DAYS -delete
    echo "Cleaned up backups older than $RETENTION_DAYS days"
else
    echo "Error: Failed to create backup of $DB_PATH" >&2
    exit 1
fi
