#!/usr/bin/env bash
set -euo pipefail

REPORT="/tmp/titan-health-report.md"
TMP_REPORT="$(mktemp -t titan-health-report.XXXXXX)"
: > "$REPORT"
: > "$TMP_REPORT"

WARN=0
CRIT=0

now_utc() {
  date -u "+%Y-%m-%d %H:%M:%S UTC"
}

write_detail() {
  printf "%s\n" "$*" | tee -a "$TMP_REPORT"
}

ssh_run() {
  local host="$1"; shift
  timeout 5s ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "$host" "$@"
}

mark_warn() {
  WARN=$((WARN + 1))
}

mark_crit() {
  CRIT=$((CRIT + 1))
}

fmt_host() {
  local host="$1"
  if [[ -n "$host" ]]; then
    printf " (%s)" "$host"
  fi
}

percent_used_root() {
  ssh_run "$1" "df -P / | awk 'NR==2 {gsub(/%/, \"\", \$5); print \$5}'" 2>/dev/null || echo ""
}

ollama_status() {
  ssh_run "$1" "command -v ollama >/dev/null 2>&1 && (systemctl is-active ollama 2>/dev/null || true)" 2>/dev/null || true
}

ollama_models() {
  ssh_run "$1" "command -v ollama >/dev/null 2>&1 && ollama ps 2>/dev/null | tail -n +2 | awk '{print \$1}' | xargs" 2>/dev/null || true
}

gpu_vram() {
  ssh_run "$1" "if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits; elif command -v rocm-smi >/dev/null 2>&1; then rocm-smi --showmeminfo vram --json 2>/dev/null | head -c 200; else echo ''; fi" 2>/dev/null || true
}

service_active() {
  local host="$1" svc="$2"
  ssh_run "$host" "systemctl is-active --quiet '$svc' && echo active || echo inactive" 2>/dev/null || echo "inactive"
}

docker_names() {
  ssh_run "$1" "command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}'" 2>/dev/null || true
}

recent_restarts() {
  local host="$1" svc="$2"
  ssh_run "$host" "journalctl -u '$svc' --since '24 hours ago' -o cat 2>/dev/null | grep -E 'Started|Starting|Stopped|Restart' | wc -l" 2>/dev/null || echo "0"
}

check_node() {
  local name="$1" host="$2" role="$3"
  if [[ -z "$host" ]]; then
    write_detail "## ${name} — Unknown"
    write_detail "- Host not configured"
    write_detail ""
    mark_warn
    return 1
  fi

  if ! ssh_run "$host" "echo ok" >/dev/null 2>&1; then
    write_detail "## ${name}$(fmt_host "$host") — Offline"
    write_detail "- SSH unavailable"
    write_detail ""
    mark_crit
    return 1
  fi

  write_detail "## ${name}$(fmt_host "$host") — Online"

  local used
  used="$(percent_used_root "$host")"
  if [[ -n "$used" ]]; then
    write_detail "- Disk: ${used}% used (/)"
    if (( used > DISK_WARN_PCT )); then
      write_detail "  - Warning: disk usage above ${DISK_WARN_PCT}%"
      mark_warn
    fi
  else
    write_detail "- Disk: unknown"
    mark_warn
  fi

  case "$role" in
    sarge)
      local ollama
      ollama="$(ollama_status "$host")"
      if [[ "$ollama" == "active" ]]; then
        write_detail "- Ollama: active"
      else
        write_detail "- Ollama: inactive"
        mark_warn
      fi

      local models
      models="$(ollama_models "$host")"
      if [[ -n "$models" ]]; then
        write_detail "- Models loaded: ${models}"
      else
        write_detail "- Models loaded: none"
      fi
      write_detail "- Tokens/sec (last): n/a"

      local vram
      vram="$(gpu_vram "$host")"
      if [[ -n "$vram" ]]; then
        write_detail "- GPU VRAM: ${vram}"
      else
        write_detail "- GPU VRAM: unknown"
        mark_warn
      fi

      local restarts
      restarts="$(recent_restarts "$host" "ollama")"
      if (( restarts > 0 )); then
        write_detail "- Restarts (last 24h): ollama=${restarts}"
        mark_warn
      else
        write_detail "- Restarts (last 24h): none"
      fi
      ;;

    shadow)
      local names
      names="$(docker_names "$host")"
      if [[ -n "$names" ]]; then
        write_detail "- Docker: running"
        for svc in qdrant milvus glance vikunja; do
          if echo "$names" | grep -iq "$svc"; then
            write_detail "  - ${svc}: running"
          else
            write_detail "  - ${svc}: not detected"
            mark_warn
          fi
        done
      else
        write_detail "- Docker: not available"
        mark_warn
      fi
      ;;

    stream)
      local tech adg
      tech="$(service_active "$host" "technitium")"
      adg="$(service_active "$host" "adguardhome")"
      write_detail "- Technitium: ${tech}"
      write_detail "- AdGuard: ${adg}"
      if [[ "$tech" != "active" ]] || [[ "$adg" != "active" ]]; then
        mark_warn
      fi

      local ollama
      ollama="$(ollama_status "$host")"
      if [[ -n "$ollama" ]]; then
        write_detail "- Ollama: ${ollama}"
      else
        write_detail "- Ollama: not detected"
      fi
      ;;

    shark)
      local ollama
      ollama="$(ollama_status "$host")"
      if [[ "$ollama" == "active" ]]; then
        write_detail "- Ollama: active"
      else
        write_detail "- Ollama: inactive"
        mark_warn
      fi
      local models
      models="$(ollama_models "$host")"
      if [[ -n "$models" ]]; then
        write_detail "- Models loaded: ${models}"
      else
        write_detail "- Models loaded: none"
      fi
      write_detail "- Tokens/sec (last): n/a"
      ;;

    share)
      write_detail "- Unraid array: check not configured"
      mark_warn
      ;;
  esac

  write_detail ""
  return 0
}

# Optional config
CONFIG_FILE="/etc/titan/health.env"
if [[ -f "$CONFIG_FILE" ]]; then
  while IFS= read -r line; do
    line="${line%%#*}"
    line="${line%"${line##*[![:space:]]}"}"
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" ]] && continue
    if [[ "$line" =~ ^[A-Z_][A-Z0-9_]*= ]]; then
      export "$line"
    fi
  done < "$CONFIG_FILE"
fi

DISK_WARN_PCT="${DISK_WARN_PCT:-85}"

SARGE_HOST="${TITAN_SARGE_HOST:-}"
SHADOW_HOST="${TITAN_SHADOW_HOST:-}"
STREAM_HOST="${TITAN_STREAM_HOST:-}"
SHARK_HOST="${TITAN_SHARK_HOST:-}"
SHARE_HOST="${TITAN_SHARE_HOST:-}"

write_detail "# TitanArray Health Report v1.0"
write_detail "Generated: $(now_utc)"
write_detail ""

TOTAL=0
ONLINE=0
OFFLINE=0

check_node "TitanSarge" "$SARGE_HOST" "sarge" && ONLINE=$((ONLINE+1)) || OFFLINE=$((OFFLINE+1))
TOTAL=$((TOTAL+1))
check_node "TitanShadow" "$SHADOW_HOST" "shadow" && ONLINE=$((ONLINE+1)) || OFFLINE=$((OFFLINE+1))
TOTAL=$((TOTAL+1))
check_node "TitanStream" "$STREAM_HOST" "stream" && ONLINE=$((ONLINE+1)) || OFFLINE=$((OFFLINE+1))
TOTAL=$((TOTAL+1))
check_node "TitanShark" "$SHARK_HOST" "shark" && ONLINE=$((ONLINE+1)) || OFFLINE=$((OFFLINE+1))
TOTAL=$((TOTAL+1))
check_node "TitanShare" "$SHARE_HOST" "share" && ONLINE=$((ONLINE+1)) || OFFLINE=$((OFFLINE+1))
TOTAL=$((TOTAL+1))

SUMMARY="Summary: total=${TOTAL}, online=${ONLINE}, offline=${OFFLINE}, warnings=${WARN}, critical=${CRIT}"
printf "%s\n\n" "$SUMMARY" > "$REPORT"
cat "$TMP_REPORT" >> "$REPORT"
cat "$REPORT"
rm -f "$TMP_REPORT"

if (( CRIT > 0 )); then
  exit 2
fi
if (( WARN > 0 )); then
  exit 1
fi
exit 0
