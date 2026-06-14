#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

skip_browser_tests() {
  echo "[browser-tests] SKIPPED: $1"
  echo "[browser-tests] $2"
  if [[ "${REQUIRE_BROWSER_TESTS:-0}" == "1" ]]; then
    echo "[browser-tests] FAILED: REQUIRE_BROWSER_TESTS=1, so skipping is not allowed."
    exit 1
  fi
  exit 0
}

if ! python -c 'from importlib.util import find_spec; raise SystemExit(0 if find_spec("playwright") else 1)'; then
  skip_browser_tests "Playwright is not installed." \
    "Install it with: python -m pip install -r requirements-dev.txt"
fi

if ! python - <<'PY'
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        browser.close()
except PlaywrightError:
    raise SystemExit(1)
PY
then
  skip_browser_tests "Playwright Chromium is not installed or cannot launch." \
    "Install it with: python -m playwright install chromium"
fi

echo "[browser-tests] Running Playwright interaction suite with Chromium."
cd "${repo_root}/scheduler_project"
python manage.py test browser_tests --verbosity 2
echo "[browser-tests] PASSED: Playwright interaction suite executed."
