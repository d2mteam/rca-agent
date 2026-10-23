#!/usr/bin/env bash
# Prepare configuration only; do not deploy workloads or overwrite existing state.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
env_file="${1:-.env}"
data_dir="${2:-$repo_dir/.hitl}"
console_port="${3:-8765}"
[[ $(uname -s) == Linux ]] || { echo 'This installation requires native Linux Docker Engine.' >&2; exit 1; }
[[ ! -e "$env_file" ]] || { echo "$env_file already exists; keeping it unchanged."; exit 0; }
[[ "$data_dir" == /* && "$data_dir" != / ]] || { echo 'Data directory must be an absolute path below /.' >&2; exit 1; }
[[ "$console_port" =~ ^[0-9]+$ ]] && (( console_port >= 1024 && console_port <= 65535 )) || { echo 'Port must be 1024..65535.' >&2; exit 1; }
docker info >/dev/null
docker compose version >/dev/null
endpoint="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
[[ "$endpoint" == unix://* ]] || { echo 'Only a local Unix Docker socket is supported.' >&2; exit 1; }
socket_path="${endpoint#unix://}"
[[ -S "$socket_path" ]] || { echo "Docker socket not found: $socket_path" >&2; exit 1; }
umask 077
mkdir -p "$data_dir/codex"
data_dir="$(cd "$data_dir" && pwd -P)"
chmod 700 "$data_dir/codex"
# Dotenv uses single quotes; reject characters that would change its syntax.
for value in "$data_dir" "$socket_path"; do
    [[ "$value" != *\'* && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || { echo 'Unsupported quote/newline in path.' >&2; exit 1; }
done
cat > "$env_file" <<EOF
HITL_UID=$(id -u)
HITL_GID=$(id -g)
DOCKER_GID=$(stat -c '%g' "$socket_path")
DOCKER_SOCKET='$socket_path'
HITL_DATA_DIR='$data_dir'
HITL_PORT=$console_port
HITL_CODEX_MODEL=
EOF
printf 'Created %s. Console URL: http://127.0.0.1:%s\n' "$env_file" "$console_port"
