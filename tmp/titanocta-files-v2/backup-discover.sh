#!/bin/bash
# backup-discover.sh - Auto-discover all SQLite databases under TitanFlow + Octa paths

TITANFLOW_DIR="${TITANFLOW_DIR:-$HOME/.titanflow}"
OCTA_DIR="${OCTA_DIR:-$HOME/.octa}"
LOG_FILE="${TITANFLOW_DIR}/logs/backup.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [DISCOVER] $1" | tee -a "$LOG_FILE"
}

ROOT_DIRS=("$TITANFLOW_DIR" "$OCTA_DIR")
if [[ -n "${OPENCLAW_DIR:-}" ]]; then
    ROOT_DIRS+=("$OPENCLAW_DIR")
fi

log "Starting database discovery in: ${ROOT_DIRS[*]}"

# Find all SQLite databases
DB_PATTERNS=("*.sqlite" "*.sqlite3" "db.sqlite*" "*_sqlite*" "*.db")
FOUND_DBS=()

for root in "${ROOT_DIRS[@]}"; do
    [[ -d "$root" ]] || continue
    for pattern in "${DB_PATTERNS[@]}"; do
        while IFS= read -r -d '' db; do
            if [[ "$db" == *"backup-key.age"* ]]; then
                continue
            fi
            FOUND_DBS+=("$db")
        done < <(find "$root" -maxdepth 5 -type f -name "$pattern" -print0 2>/dev/null || true)
    done
done

# Remove duplicates and output only the list (no logs)
if [[ ${#FOUND_DBS[@]} -gt 0 ]]; then
    printf '%s\n' "${FOUND_DBS[@]}" | sort -u
fi

log "Found ${#FOUND_DBS[@]} database(s)"
log "Database discovery complete"
