#!/usr/bin/env bash
set -euo pipefail
demo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
docker build --tag hitl-demo-backend:1.0.0 "$demo_dir/backend"
docker image inspect hitl-demo-backend:1.0.0 --format 'Built {{.Id}}'
