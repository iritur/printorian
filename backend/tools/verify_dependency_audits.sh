#!/usr/bin/env bash
set -euo pipefail

# Helper script to run dependency audit checks locally. Mirrors CI steps.

echo "Running backend pip-audit..."
python -m pip install --upgrade pip
python -m pip install pip-audit
pip-audit

echo "Running frontend npm audit..."
cd ..
cd frontend
npm ci --silent
npm audit --audit-level=high
cd ..

echo "Dependency audit checks passed"