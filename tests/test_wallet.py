"""
Unit & integration tests for the Wallet Service.

Tests run against a real PostgreSQL instance (or SQLite for unit tests).
Uses pytest-asyncio and the FastAPI TestClient.
"""
import json
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import get_db
from app.models import Base, Account, AssetType, Wallet, Transaction
from app.exceptions import InsufficientFundsError


# ──────────────────────────────────────────────────────────────────────────────
# Test fixtures — SQLite in-memory DB
# ──────────────────────────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite:///:memory:"

# SQLite doesn't support FOR UPDATE — we patch it for unit tests
import sqlalchemy
_orig_compile = None


@pytest.fixture(scope="session")
def engine_fixture():
    """Create an in-memory SQLite engine and build all tables."""
    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def db_session(engine_fixture):
    """Provide a clean database session per test, rolled back afterwards."""
    connection = engine_fixture.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(bind=connection, autocommit=False, autoflush=False)
    session = TestSession()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def seed_data(db_session):
    """Insert minimal seed data into the test DB."""
    # Asset types
    gc = AssetType(
        id=uuid.UUID("a1000000-0000-0000-0000-000000000001"),
        name="Gold Coins", symbol="GC", is_active=True,
    )
    db_session.add(gc)

    # System accounts
    treasury = Account(
        id=uuid.UUID("b1000000-0000-0000-0000-000000000001"),
        username="system_treasury", is_system=True, is_active=True,
    )
    bonus_pool = Account(
        id=uuid.UUID("b1000000-0000-0000-0000-000000000002"),
        username="system_bonus_pool", is_system=True, is_active=True,
    )
    revenue = Account(
        id=uuid.UUID("b1000000-0000-0000-0000-000000000003"),
        username="system_revenue", is_system=True, is_active=True,
    )
    db_session.add_all([treasury, bonus_pool, revenue])

    # User account
    alice = Account(
        id=uuid.UUID("c1000000-0000-0000-0000-000000000001"),
        username="alice", email="alice@test.com", is_system=False, is_active=True,
    )
    db_session.add(alice)

    # Wallets
    treasury_wallet = Wallet(
        id=uuid.UUID("w1000000-0000-0000-0000-000000000001"),
        account_id=treasury.id, asset_type_id=gc.id,
        balance=Decimal("99999999"),
    )
    bonus_wallet = Wallet(
        id=uuid.UUID("w1000000-0000-0000-0000-000000000004"),
        account_id=bonus_pool.id, asset_type_id=gc.id,
        balance=Decimal("99999999"),
    )
    revenue_wallet = Wallet(
        id=uuid.UUID("w1000000-0000-0000-0000-000000000007"),
        account_id=revenue.id, asset_type_id=gc.id,
        balance=Decimal("0"),
    )
    alice_wallet = Wallet(
        id=uuid.UUID("w2000000-0000-0000-0000-000000000001"),
        account_id=alice.id, asset_type_id=gc.id,
        balance=Decimal("500"),
    )
    db_session.add_all([treasury_wallet, bonus_wallet, revenue_wallet, alice_wallet])
    db_session.flush()

    return {
        "gc": gc,
        "treasury": treasury,
        "bonus_pool": bonus_pool,
        "revenue": revenue,
        "alice": alice,
        "treasury_wallet": treasury_wallet,
        "bonus_wallet": bonus_wallet,
        "revenue_wallet": revenue_wallet,
        "alice_wallet": alice_wallet,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Service-layer unit tests
# ──────────────────────────────────────────────────────────────────────────────

class TestTopUp:
    def test_top_up_increases_balance(self, db_session, seed_data):
        from app.service import top_up
        result = top_up(
            db=db_session,
            user_account_id=seed_data["alice"].id,
            asset_type_id=seed_data["gc"].id,
            amount=Decimal("100"),
            description="Test top-up",
        )
        db_session.flush()

        assert result["transaction_type"] == "TOPUP"
        assert Decimal(result["amount"]) == Decimal("100")
        assert Decimal(result["balance_after"]) == Decimal("600")  # 500 + 100

        # Verify wallet balance updated
        alice_wallet = db_session.get(Wallet, seed_data["alice_wallet"].id)
        assert alice_wallet.balance == Decimal("600")

    def test_top_up_creates_two_ledger_entries(self, db_session, seed_data):
        from app.service import top_up
        result = top_up(
            db=db_session,
            user_account_id=seed_data["alice"].id,
            asset_type_id=seed_data["gc"].id,
            amount=Decimal("50"),
        )
        db_session.flush()

        ref_id = uuid.UUID(result["reference_id"])
        entries = db_session.query(Transaction).filter(
            Transaction.reference_id == ref_id
        ).all()
        assert len(entries) == 2
        amounts = {e.amount for e in entries}
        assert Decimal("50") in amounts
        assert Decimal("-50") in amounts

    def test_top_up_idempotency(self, db_session, seed_data):
        from app.service import top_up
        from app.exceptions import DuplicateIdempotentRequestError
        key = f"idem-topup-{uuid.uuid4()}"

        result1 = top_up(
            db=db_session,
            user_account_id=seed_data["alice"].id,
            asset_type_id=seed_data["gc"].id,
            amount=Decimal("100"),
            idempotency_key=key,
        )
        db_session.flush()

        with pytest.raises(DuplicateIdempotentRequestError) as exc_info:
            top_up(
                db=db_session,
                user_account_id=seed_data["alice"].id,
                asset_type_id=seed_data["gc"].id,
                amount=Decimal("100"),
                idempotency_key=key,
            )

        assert exc_info.value.cached_response["reference_id"] == result1["reference_id"]


class TestBonus:
    def test_bonus_increases_balance(self, db_session, seed_data):
        from app.service import issue_bonus
        result = issue_bonus(
            db=db_session,
            user_account_id=seed_data["alice"].id,
            asset_type_id=seed_data["gc"].id,
            amount=Decimal("75"),
            reason="Referral reward",
        )
        db_session.flush()

        assert result["transaction_type"] == "BONUS"
        assert Decimal(result["balance_after"]) == Decimal("575")  # 500 + 75


class TestSpend:
    def test_spend_decreases_balance(self, db_session, seed_data):
        from app.service import spend
        result = spend(
            db=db_session,
            user_account_id=seed_data["alice"].id,
            asset_type_id=seed_data["gc"].id,
            amount=Decimal("30"),
            description="Bought power-up",
        )
        db_session.flush()

        assert result["transaction_type"] == "SPEND"
        assert Decimal(result["balance_after"]) == Decimal("470")  # 500 - 30

    def test_spend_rejects_insufficient_funds(self, db_session, seed_data):
        from app.service import spend
        with pytest.raises(InsufficientFundsError) as exc_info:
            spend(
                db=db_session,
                user_account_id=seed_data["alice"].id,
                asset_type_id=seed_data["gc"].id,
                amount=Decimal("99999"),  # more than the 500 balance
            )
        assert "500" in str(exc_info.value)

    def test_spend_balance_never_goes_negative(self, db_session, seed_data):
        """After a refused spend, wallet balance must be unchanged."""
        from app.service import spend
        alice_wallet_before = db_session.get(Wallet, seed_data["alice_wallet"].id)
        balance_before = alice_wallet_before.balance

        with pytest.raises(InsufficientFundsError):
            spend(
                db=db_session,
                user_account_id=seed_data["alice"].id,
                asset_type_id=seed_data["gc"].id,
                amount=Decimal("1000000"),
            )

        db_session.refresh(alice_wallet_before)
        assert alice_wallet_before.balance == balance_before

    def test_spend_credits_revenue_wallet(self, db_session, seed_data):
        from app.service import spend
        revenue_before = db_session.get(Wallet, seed_data["revenue_wallet"].id).balance

        spend(
            db=db_session,
            user_account_id=seed_data["alice"].id,
            asset_type_id=seed_data["gc"].id,
            amount=Decimal("100"),
        )
        db_session.flush()

        revenue_after = db_session.get(Wallet, seed_data["revenue_wallet"].id).balance
        assert revenue_after == revenue_before + Decimal("100")


class TestGetBalance:
    def test_get_balance_returns_correct_amount(self, db_session, seed_data):
        from app.service import get_balance
        wallet, account, asset = get_balance(
            db=db_session,
            account_id=seed_data["alice"].id,
            asset_type_id=seed_data["gc"].id,
        )
        assert wallet.balance == Decimal("500")
        assert account.username == "alice"
        assert asset.symbol == "GC"

    def test_get_balance_unknown_wallet_raises(self, db_session, seed_data):
        from app.service import get_balance
        from app.exceptions import WalletNotFoundError
        with pytest.raises(WalletNotFoundError):
            get_balance(
                db=db_session,
                account_id=uuid.uuid4(),   # non-existent
                asset_type_id=seed_data["gc"].id,
            )


# ──────────────────────────────────────────────────────────────────────────────
# HTTP API tests (FastAPI TestClient)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(db_session, seed_data):
    """Override DB dependency with the test session."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestAPIHealth:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


class TestAPIBalance:
    def test_get_balance_ok(self, client, seed_data):
        resp = client.get(
            f"/wallet/balance/{seed_data['alice'].id}/{seed_data['gc'].id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "alice"
        assert data["symbol"] == "GC"
        assert float(data["balance"]) == 500.0

    def test_get_balance_not_found(self, client, seed_data):
        resp = client.get(f"/wallet/balance/{uuid.uuid4()}/{seed_data['gc'].id}")
        assert resp.status_code == 404


class TestAPITopUp:
    def test_topup_ok(self, client, seed_data):
        resp = client.post("/wallet/topup", json={
            "user_account_id": str(seed_data["alice"].id),
            "asset_type_id": str(seed_data["gc"].id),
            "amount": "100",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["transaction_type"] == "TOPUP"
        assert float(data["balance_after"]) == 600.0

    def test_topup_idempotency_header(self, client, seed_data):
        key = f"test-{uuid.uuid4()}"
        payload = {
            "user_account_id": str(seed_data["alice"].id),
            "asset_type_id": str(seed_data["gc"].id),
            "amount": "50",
        }
        r1 = client.post("/wallet/topup", json=payload, headers={"Idempotency-Key": key})
        r2 = client.post("/wallet/topup", json=payload, headers={"Idempotency-Key": key})

        assert r1.status_code == 201
        assert r2.status_code == 200  # duplicate → 200 not 201
        assert r1.json()["reference_id"] == r2.json()["reference_id"]


class TestAPISpend:
    def test_spend_ok(self, client, seed_data):
        resp = client.post("/wallet/spend", json={
            "user_account_id": str(seed_data["alice"].id),
            "asset_type_id": str(seed_data["gc"].id),
            "amount": "30",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert float(data["balance_after"]) == 470.0

    def test_spend_insufficient_funds(self, client, seed_data):
        resp = client.post("/wallet/spend", json={
            "user_account_id": str(seed_data["alice"].id),
            "asset_type_id": str(seed_data["gc"].id),
            "amount": "999999",
        })
        assert resp.status_code == 402
        assert resp.json()["detail"]["code"] == "INSUFFICIENT_FUNDS"


class TestAPIBonus:
    def test_bonus_ok(self, client, seed_data):
        resp = client.post("/wallet/bonus", json={
            "user_account_id": str(seed_data["alice"].id),
            "asset_type_id": str(seed_data["gc"].id),
            "amount": "25",
            "reason": "Level-up reward",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["transaction_type"] == "BONUS"
        assert float(data["balance_after"]) == 525.0
