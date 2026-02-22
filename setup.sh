#!/usr/bin/env bash
# =============================================================================
# setup.sh  —  One-shot bootstrap script for the Wallet Service
# =============================================================================
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#
# What it does:
#   1. Copies .env.example → .env (if .env doesn't exist yet)
#   2. Starts the PostgreSQL container
#   3. Waits for the DB to be ready
#   4. Runs the seed script
#   5. Starts the API container
# =============================================================================

set -euo pipefail

# ── 1. Environment file ───────────────────────────────────────────────────────
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✅  Created .env from .env.example — edit it if you need custom values."
fi

# ── 2. Start the database container ──────────────────────────────────────────
echo "🐳  Starting PostgreSQL container..."
docker compose up -d db

# ── 3. Wait for readiness ─────────────────────────────────────────────────────
echo "⏳  Waiting for PostgreSQL to be ready..."
until docker exec wallet_db pg_isready -U wallet_user -d wallet_db > /dev/null 2>&1; do
    printf '.'
    sleep 1
done
echo ""
echo "✅  PostgreSQL is ready."

# ── 4. seed.sql is applied automatically by docker-entrypoint-initdb.d on first
#      boot, but you can re-run it manually with:
#        docker exec -i wallet_db psql -U wallet_user -d wallet_db < seed.sql
echo "ℹ️   Seed data is applied automatically on first DB startup (seed.sql)."
echo "    To re-seed manually: docker exec -i wallet_db psql -U wallet_user -d wallet_db < seed.sql"

# ── 5. Start the API ──────────────────────────────────────────────────────────
echo "🚀  Starting the Wallet API..."
docker compose up -d api

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Wallet Service is running!                 ║"
echo "║                                              ║"
echo "║   API:   http://localhost:8000               ║"
echo "║   Docs:  http://localhost:8000/docs          ║"
echo "║   ReDoc: http://localhost:8000/redoc         ║"
echo "╚══════════════════════════════════════════════╝"
