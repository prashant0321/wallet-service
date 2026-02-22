# 💰 Wallet Service

A production-grade, closed-loop virtual currency service for high-traffic applications (gaming platforms, loyalty reward systems, etc.).

Manages application-specific credits ("Gold Coins", "Diamonds", "Loyalty Points") with full data integrity, concurrency safety, and idempotent APIs.

---

## Table of Contents

1. [Tech Stack & Why](#1-tech-stack--why)
2. [Architecture Overview](#2-architecture-overview)
3. [Quick Start (Docker)](#3-quick-start-docker)
4. [Database Setup & Seeding](#4-database-setup--seeding)
5. [API Reference](#5-api-reference)
6. [Concurrency Strategy](#6-concurrency-strategy)
7. [Idempotency Strategy](#7-idempotency-strategy)
8. [Double-Entry Bookkeeping](#8-double-entry-bookkeeping)
9. [Running Tests](#9-running-tests)
10. [Project Structure](#10-project-structure)
11. [Pre-seeded Data (Quick Reference)](#11-pre-seeded-data-quick-reference)

---

## 1. Tech Stack & Why

| Layer | Choice | Reason |
|---|---|---|
| **Language** | Python 3.12 | Rich ecosystem, expressive, fast iteration |
| **API Framework** | FastAPI | Async-ready, auto-generates OpenAPI docs, Pydantic validation |
| **ORM** | SQLAlchemy 2.0 | Mature, full control over SQL including `SELECT FOR UPDATE` |
| **Database** | PostgreSQL 16 | ACID transactions, row-level locking, `CHECK` constraints, battle-tested for financial workloads |
| **Container** | Docker + Docker Compose | Reproducible environment, one-command startup |
| **Testing** | pytest + FastAPI TestClient | Fast in-memory SQLite for unit tests, no network required |

**Why PostgreSQL over SQLite/MySQL?**
- Native `SELECT ... FOR UPDATE` (essential for pessimistic locking)
- `CHECK` constraints enforced at the DB layer (safety net)
- Full ACID compliance including serialisable isolation
- Proven in financial and fintech applications worldwide

---

## 2. Architecture Overview

```
┌─────────────┐    HTTP    ┌─────────────────────────────────┐
│   Clients   │ ─────────▶ │  FastAPI (app/main.py)          │
└─────────────┘            │  Routers: /wallet/*             │
                           └──────────────┬──────────────────┘
                                          │ calls
                           ┌──────────────▼──────────────────┐
                           │  Service Layer (app/service.py)  │
                           │  - top_up()                      │
                           │  - issue_bonus()                 │
                           │  - spend()                       │
                           │  - get_balance()                 │
                           └──────────────┬──────────────────┘
                                          │ SQLAlchemy ORM
                           ┌──────────────▼──────────────────┐
                           │  PostgreSQL                      │
                           │  ┌────────────┐ ┌────────────┐  │
                           │  │  accounts  │ │asset_types │  │
                           │  └────────────┘ └────────────┘  │
                           │  ┌────────────┐ ┌────────────┐  │
                           │  │  wallets   │ │transactions│  │
                           │  └────────────┘ └────────────┘  │
                           │  ┌──────────────────────────┐   │
                           │  │    idempotency_keys       │   │
                           │  └──────────────────────────┘   │
                           └─────────────────────────────────┘
```

**Three system accounts** act as sources/sinks:
| Account | Role |
|---|---|
| `system_treasury` | Source for top-ups (backed by real payments) |
| `system_bonus_pool` | Source for free credit grants |
| `system_revenue` | Destination for user spends |

---

## 3. Quick Start (Docker)

**Prerequisites:** Docker Desktop installed and running.

### Windows
```bat
setup.bat
```

### macOS / Linux
```bash
chmod +x setup.sh
./setup.sh
```

Both scripts will:
1. Create `.env` from `.env.example`
2. Start the PostgreSQL container
3. Auto-apply `seed.sql` on first DB boot
4. Start the API on `http://localhost:8000`

**Open the interactive API docs:**
```
http://localhost:8000/docs
```

---

## 4. Database Setup & Seeding

### Automatic (Docker)
The `seed.sql` file is mounted into `/docker-entrypoint-initdb.d/` — PostgreSQL runs it automatically on the **first** container start.

### Manual (against an existing PostgreSQL instance)
```bash
# 1. Create the database
psql -U postgres -c "CREATE USER wallet_user WITH PASSWORD 'wallet_pass';"
psql -U postgres -c "CREATE DATABASE wallet_db OWNER wallet_user;"

# 2. Run the seed script
psql -U wallet_user -d wallet_db -f seed.sql
```

### Re-seeding with Docker
```bash
docker exec -i wallet_db psql -U wallet_user -d wallet_db < seed.sql
```

### What the seed creates
| Category | Items |
|---|---|
| **Asset Types** | Gold Coins (GC), Diamonds (DIA), Loyalty Points (LP) |
| **System Accounts** | `system_treasury`, `system_bonus_pool`, `system_revenue` |
| **User Accounts** | `alice` (500 GC, 50 DIA, 200 LP), `bob` (1000 GC, 350 LP), `charlie` (250 GC, 10 DIA) |

---

## 5. API Reference

### Base URL
```
http://localhost:8000
```

### Endpoints

#### `GET /health`
Health check.

---

#### `GET /wallet/asset-types`
List all active asset types.

---

#### `GET /wallet/accounts`
List user accounts. Add `?include_system=true` to include system accounts.

---

#### `GET /wallet/balance/{account_id}/{asset_type_id}`
Get the current balance for a user + asset type.

**Response:**
```json
{
  "account_id": "c1000000-0000-0000-0000-000000000001",
  "username": "alice",
  "asset_type": "Gold Coins",
  "symbol": "GC",
  "balance": "500.0000"
}
```

---

#### `GET /wallet/transactions/{account_id}/{asset_type_id}`
Paginated transaction history (newest first).

Query params: `limit` (1–100, default 20), `offset` (default 0).

---

#### `POST /wallet/topup`
**Flow 1 — Wallet Top-up (Purchase)**

Credits virtual currency to a user's wallet. Assumes real-money payment already processed.

**Request:**
```json
{
  "user_account_id": "c1000000-0000-0000-0000-000000000001",
  "asset_type_id":   "a1000000-0000-0000-0000-000000000001",
  "amount": "100",
  "payment_reference": "PAY-12345",
  "description": "Purchased 100 Gold Coins"
}
```

**Headers (optional):**
```
Idempotency-Key: <unique-uuid>
```

**Response (201):**
```json
{
  "status": "success",
  "reference_id": "...",
  "transaction_type": "TOPUP",
  "amount": "100",
  "balance_after": "600.0000",
  "message": "Successfully credited 100 GC to your wallet."
}
```

---

#### `POST /wallet/bonus`
**Flow 2 — Bonus / Incentive**

System issues free credits to a user (referral bonus, level-up reward, etc.).

**Request:**
```json
{
  "user_account_id": "c1000000-0000-0000-0000-000000000001",
  "asset_type_id":   "a1000000-0000-0000-0000-000000000001",
  "amount": "50",
  "reason": "Referral bonus — invited 3 friends",
  "description": "Referral reward"
}
```

---

#### `POST /wallet/spend`
**Flow 3 — Purchase / Spend**

Deducts credits from a user's wallet for an in-app purchase.

Returns `402 Payment Required` if the user has insufficient funds.

**Request:**
```json
{
  "user_account_id": "c1000000-0000-0000-0000-000000000001",
  "asset_type_id":   "a1000000-0000-0000-0000-000000000001",
  "amount": "30",
  "item_reference": "ITEM-SWORD-001",
  "description": "Bought Iron Sword"
}
```

---

## 6. Concurrency Strategy

### Problem
Under high traffic, two requests can simultaneously read a user's balance (e.g. both see 50 GC), both decide the spend is valid, and both deduct — resulting in a negative balance.

### Solution: Pessimistic Row-Level Locking (`SELECT ... FOR UPDATE`)

Every mutating operation acquires an **exclusive row lock** on the wallet row(s) before reading the balance:

```sql
SELECT * FROM wallets
WHERE account_id = $1 AND asset_type_id = $2
FOR UPDATE;
```

**What this guarantees:**
- Only one transaction holds the lock at a time
- Any concurrent transaction that targets the same wallet **blocks** until the first commits or rolls back
- The balance check and balance update happen atomically — there is no window for a race condition

### Deadlock Prevention
When an operation touches two wallets (e.g. top-up touches Treasury + User), we always lock in a **consistent global order** (system wallet first, then user wallet). This prevents the classic circular-wait deadlock.

### Database-level Safety Net
Even if a bug bypasses the application-level check, the PostgreSQL `CHECK` constraint enforces non-negative balances:
```sql
CONSTRAINT ck_wallet_balance_non_negative CHECK (balance >= 0)
```

### Connection Pooling
The engine is configured with `pool_size=20, max_overflow=10` so the service can handle up to 30 concurrent DB connections without exhausting resources.

---

## 7. Idempotency Strategy

### Problem
Network failures cause clients to retry requests. Without idempotency, a retry could double-credit or double-debit a wallet.

### Solution: Idempotency Key Table

Every mutating endpoint accepts an optional `Idempotency-Key` header.

**Flow:**
1. Client generates a unique key (UUID) and includes it in the request header.
2. **Before** executing any logic, the service checks the `idempotency_keys` table for this key.
3. **If found:** Return the cached response immediately — **no side effects**.
4. **If not found:** Execute the business logic and store the result under the key in the **same database transaction**.

Because the key is stored inside the same ACID transaction as the wallet update, it is only visible if the whole operation committed — there is no partial-success state.

Keys expire after 24 hours (configurable via `IDEMPOTENCY_KEY_TTL_HOURS`).

**Example retry-safe call:**
```bash
curl -X POST http://localhost:8000/wallet/topup \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000" \
  -d '{"user_account_id": "...", "asset_type_id": "...", "amount": "100"}'
```

A duplicate call with the same key returns `200 OK` (not `201 Created`) with the original response body — safe to retry as many times as needed.

---

## 8. Double-Entry Bookkeeping

Every business event produces **two ledger entries** sharing a `reference_id`:

| Event | Debit | Credit |
|---|---|---|
| Top-up (purchase) | Treasury wallet (`-100 GC`) | User wallet (`+100 GC`) |
| Bonus | Bonus Pool wallet (`-50 GC`) | User wallet (`+50 GC`) |
| Spend | User wallet (`-30 GC`) | Revenue wallet (`+30 GC`) |

The sum of all `amount` values across `transactions` should always equal **zero** — the ledger is self-balancing, which makes auditing straightforward.

---

## 9. Running Tests

### With Docker (recommended)
```bash
docker compose run --rm api pytest
```

### Locally
```bash
pip install -r requirements.txt
pytest
```

Tests use an **in-memory SQLite database** — no PostgreSQL required for unit tests. The test suite covers:
- Balance credits and debits
- Insufficient-funds rejection
- Non-negative balance guarantee
- Two-entry ledger verification
- Idempotency (duplicate key returns cached response)
- HTTP status codes for all endpoints
- 402 on insufficient funds

---

## 10. Project Structure

```
Wallet/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app + lifespan
│   ├── config.py        # Pydantic settings (reads .env)
│   ├── database.py      # SQLAlchemy engine + session factory
│   ├── models.py        # ORM models (AssetType, Account, Wallet, Transaction, IdempotencyKey)
│   ├── schemas.py       # Pydantic request/response models
│   ├── service.py       # Core business logic (top_up, issue_bonus, spend, …)
│   ├── exceptions.py    # Custom exception hierarchy
│   └── routers/
│       ├── __init__.py
│       └── wallet.py    # FastAPI router — all /wallet/* endpoints
├── tests/
│   ├── __init__.py
│   └── test_wallet.py   # Unit + HTTP tests
├── seed.sql             # Schema DDL + seed data
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── setup.sh             # Linux/macOS bootstrap
├── setup.bat            # Windows bootstrap
├── .env.example
├── .gitignore
├── pytest.ini
└── README.md
```

---

## 11. Pre-seeded Data (Quick Reference)

Use these IDs directly in API calls after running the seed:

### Asset Types
| Name | Symbol | ID |
|---|---|---|
| Gold Coins | GC | `a1000000-0000-0000-0000-000000000001` |
| Diamonds | DIA | `a1000000-0000-0000-0000-000000000002` |
| Loyalty Points | LP | `a1000000-0000-0000-0000-000000000003` |

### User Accounts
| Username | ID | GC | DIA | LP |
|---|---|---|---|---|
| alice | `c1000000-0000-0000-0000-000000000001` | 500 | 50 | 200 |
| bob | `c1000000-0000-0000-0000-000000000002` | 1000 | 0 | 350 |
| charlie | `c1000000-0000-0000-0000-000000000003` | 250 | 10 | 0 |

### Example: Check Alice's Gold Coin balance
```bash
curl http://localhost:8000/wallet/balance/c1000000-0000-0000-0000-000000000001/a1000000-0000-0000-0000-000000000001
```

### Example: Alice earns 100 GC (top-up)
```bash
curl -X POST http://localhost:8000/wallet/topup \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(python -c 'import uuid; print(uuid.uuid4())')" \
  -d '{
    "user_account_id": "c1000000-0000-0000-0000-000000000001",
    "asset_type_id":   "a1000000-0000-0000-0000-000000000001",
    "amount": "100",
    "description": "Purchased Gold Coins pack"
  }'
```

### Example: Alice spends 30 GC
```bash
curl -X POST http://localhost:8000/wallet/spend \
  -H "Content-Type: application/json" \
  -d '{
    "user_account_id": "c1000000-0000-0000-0000-000000000001",
    "asset_type_id":   "a1000000-0000-0000-0000-000000000001",
    "amount": "30",
    "item_reference": "ITEM-SWORD-001",
    "description": "Bought Iron Sword"
  }'
```
