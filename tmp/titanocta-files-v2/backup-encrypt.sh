#!/bin/bash
# backup-encrypt.sh - Encrypt backup archive using age

set -euo pipefail

TITANFLOW_DIR="${TITANFLOW_DIR:-$HOME/.titanflow}"
LOG_FILE="${TITANFLOW_DIR}/logs/backup.log"
KEY_FILE="${TITANFLOW_DIR}/backup-key.age"
RECIPIENT_FILE="${TITANFLOW_DIR}/backup-recipient.age"
BACKUP_DIR="${TITANFLOW_DIR}/backups"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ENCRYPT] $1" | tee -a "$LOG_FILE"
}

error_exit() {
    log "ERROR: $1"
    exit 1
}

# Generate age key if it doesn't exist
if [[ ! -f "$KEY_FILE" ]]; then
    log "Generating new age encryption key..."
    age-keygen -o "$KEY_FILE" 2>&1 | tee -a "$LOG_FILE" || error_exit "Failed to generate age key"
    chmod 600 "$KEY_FILE"
    
    # Extract public key to recipient file
    grep "^# public key:" "$KEY_FILE" | awk '{print $4}' > "$RECIPIENT_FILE"
    log "Age key generated. Public key: $(cat "$RECIPIENT_FILE")"
else
    log "Using existing age key from $KEY_FILE"
    # Ensure recipient file exists
    if [[ ! -f "$RECIPIENT_FILE" ]]; then
        grep "^# public key:" "$KEY_FILE" | awk '{print $4}' > "$RECIPIENT_FILE"
    fi
fi

# Check arguments
if [[ $# -lt 1 ]]; then
    error_exit "Usage: $0 <input-archive> [output-file]"
fi

INPUT_ARCHIVE="$1"
OUTPUT_FILE="${2:-$INPUT_ARCHIVE.age}"

if [[ ! -f "$INPUT_ARCHIVE" ]]; then
    error_exit "Input archive not found: $INPUT_ARCHIVE"
fi

if [[ ! -f "$RECIPIENT_FILE" ]]; then
    error_exit "Recipient file not found: $RECIPIENT_FILE"
fi

log "Encrypting $INPUT_ARCHIVE -> $OUTPUT_FILE"

# Encrypt using age with recipient file
age -R "$RECIPIENT_FILE" -o "$OUTPUT_FILE" "$INPUT_ARCHIVE" 2>&1 | tee -a "$LOG_FILE" || error_exit "Encryption failed"

if [[ -f "$OUTPUT_FILE" ]]; then
    log "Encryption complete: $OUTPUT_FILE"
    # Remove unencrypted archive
    rm -f "$INPUT_ARCHIVE"
    log "Removed unencrypted archive: $INPUT_ARCHIVE"
else
    error_exit "Encrypted file was not created"
fi

echo "$OUTPUT_FILE"
