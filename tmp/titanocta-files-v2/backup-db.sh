#!/bin/bash
#===============================================================================
# TitanFlow Database Backup - Consolidated Script
# Hourly backup of all SQLite databases with age encryption
#===============================================================================

set -euo pipefail

TITANFLOW_DIR="${TITANFLOW_DIR:-$HOME/.titanflow}"
OCTA_DIR="${OCTA_DIR:-$HOME/.octa}"
BACKUP_DIR="${TITANFLOW_DIR}/backups"
LOG_FILE="${TITANFLOW_DIR}/logs/backup.log"
MAX_BACKUPS=7

# Ensure directories
mkdir -p "$BACKUP_DIR"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [BACKUP] $1" | tee -a "$LOG_FILE"
}

error_exit() {
    log "ERROR: $1"
    exit 1
}

# Check age key
KEY_FILE="${TITANFLOW_DIR}/backup-key.age"
if [[ ! -f "$KEY_FILE" ]]; then
    log "Generating age key..."
    age-keygen -o "$KEY_FILE" 2>> "$LOG_FILE" || error_exit "Failed to generate key"
    chmod 600 "$KEY_FILE"
fi

PUBLIC_KEY=$(grep "^# public key:" "$KEY_FILE" | awk '{print $4}')

# Discover databases (only non-empty)
log "Discovering databases..."
DB_LIST=()
while IFS= read -r -d '' db; do
    if [[ -s "$db" ]]; then
        DB_LIST+=("$db")
    fi
done < <(
    find "$TITANFLOW_DIR" "$OCTA_DIR" -type f \
      \( -name "*.sqlite" -o -name "*.sqlite3" -o -name "db.sqlite*" -o -name "*.db" \) \
      -print0 2>/dev/null | grep -vz ".git/" | grep -vz "node_modules/"
)

if [[ ${#DB_LIST[@]} -eq 0 ]]; then
    log "No databases found!"
    exit 1
fi

log "Found ${#DB_LIST[@]} databases"

# Create archive
TIMESTAMP=$(date '+%Y-%m-%d_%H%M')
ARCHIVE="${BACKUP_DIR}/titanflow-db-${TIMESTAMP}.tar.gz"

log "Creating archive..."
tar -czf "$ARCHIVE" "${DB_LIST[@]}" 2>> "$LOG_FILE" || error_exit "Failed to create archive"

log "Archive created: $(basename "$ARCHIVE") ($(du -h "$ARCHIVE" | cut -f1))"

# Encrypt
ENCRYPTED="${ARCHIVE}.age"
log "Encrypting..."

age -r "$PUBLIC_KEY" -o "$ENCRYPTED" "$ARCHIVE" 2>> "$LOG_FILE" || error_exit "Encryption failed"

rm -f "$ARCHIVE"
log "Encrypted: $(basename "$ENCRYPTED") ($(du -h "$ENCRYPTED" | cut -f1))"

# Prune
log "Pruning to last $MAX_BACKUPS backups..."
mapfile -t BACKUPS < <(ls -1t "$BACKUP_DIR"/*.age 2>/dev/null || true)
COUNT=${#BACKUPS[@]}

if [[ $COUNT -gt $MAX_BACKUPS ]]; then
    REMOVE=$((COUNT - MAX_BACKUPS))
    for ((i=MAX_BACKUPS; i<COUNT; i++)); do
        rm -f "${BACKUPS[$i]}"
    done
    log "Pruned $REMOVE old backups"
fi

log "Backup complete: $(ls -1t "$BACKUP_DIR"/*.age | head -1)"

# Git sync to Sarge
log "Syncing to git remote..."
cd "$BACKUP_DIR" || error_exit "Cannot cd to backup dir"
git add *.age 2>> "$LOG_FILE" || true
git commit -m "Backup $(date '+%Y-%m-%d %H:%M')" 2>> "$LOG_FILE" || true
git push origin master 2>> "$LOG_FILE" || log "WARNING: Git push failed"
log "Git sync complete"

# Telegram notification
# SECURITY: hardcoded bot token removed. Any previously exposed token must be rotated.
TELEGRAM_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
MSG="✅ TitanFlow backup complete: $(basename "$ENCRYPTED")"

if [[ -n "$TELEGRAM_TOKEN" && -n "$TELEGRAM_CHAT_ID" ]]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=${MSG}" > /dev/null 2>&1 || true
else
    log "Telegram notification skipped (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)"
fi
