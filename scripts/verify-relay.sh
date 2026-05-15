#!/usr/bin/env bash
set -euo pipefail

if ! command -v usbrelay >/dev/null 2>&1; then
  echo "usbrelay not found in PATH" >&2
  exit 1
fi

usbrelay

