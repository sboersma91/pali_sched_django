#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[backend-tests] Running Django backend suite (browser tests excluded)."
cd "${repo_root}/scheduler_project"
python manage.py test scheduler_app members
