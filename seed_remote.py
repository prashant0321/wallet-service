"""
Run this script once after connecting Railway PostgreSQL to seed initial data.
Usage:
    set DATABASE_URL=postgresql://...
    python seed_remote.py
"""
import os
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.models import Base, AssetType, Account, Wallet, Transaction

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("ERROR: DATABASE_URL environment variable is not set.")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

print("Creating tables...")
Base.metadata.create_all(bind=engine)

def utcnow():
    return datetime.now(timezone.utc)

with Session() as db:
    # Skip if already seeded
    if db.query(AssetType).count() > 0:
        print("Database already seeded. Skipping.")
        raise SystemExit(0)

    print("Seeding asset types...")
    gc  = AssetType(id=uuid.uuid4(), name="Gold Coins",      symbol="GC",  description="Primary in-game currency")
    dia = AssetType(id=uuid.uuid4(), name="Diamonds",        symbol="DIA", description="Premium currency")
    lp  = AssetType(id=uuid.uuid4(), name="Loyalty Points",  symbol="LP",  description="Earned through gameplay")
    db.add_all([gc, dia, lp])
    db.flush()

    print("Seeding system accounts...")
    treasury = Account(id=uuid.uuid4(), username="system_treasury",   email="treasury@system.internal",   is_system=True)
    bonus    = Account(id=uuid.uuid4(), username="system_bonus_pool", email="bonus@system.internal",      is_system=True)
    revenue  = Account(id=uuid.uuid4(), username="system_revenue",    email="revenue@system.internal",    is_system=True)
    db.add_all([treasury, bonus, revenue])
    db.flush()

    print("Seeding user accounts...")
    alice   = Account(id=uuid.uuid4(), username="alice",   email="alice@example.com")
    bob     = Account(id=uuid.uuid4(), username="bob",     email="bob@example.com")
    charlie = Account(id=uuid.uuid4(), username="charlie", email="charlie@example.com")
    db.add_all([alice, bob, charlie])
    db.flush()

    SYSTEM_BALANCE = Decimal("99999999")
    USER_BALANCE   = Decimal("0")

    print("Seeding wallets...")
    wallets = []
    for account in [treasury, bonus, revenue]:
        for asset in [gc, dia, lp]:
            wallets.append(Wallet(id=uuid.uuid4(), account_id=account.id, asset_type_id=asset.id, balance=SYSTEM_BALANCE))
    for account in [alice, bob, charlie]:
        for asset in [gc, dia, lp]:
            wallets.append(Wallet(id=uuid.uuid4(), account_id=account.id, asset_type_id=asset.id, balance=USER_BALANCE))
    db.add_all(wallets)
    db.flush()

    print("Writing opening ledger entries...")
    ref = uuid.uuid4()
    for w in wallets:
        if w.balance > 0:
            db.add(Transaction(
                id=uuid.uuid4(),
                reference_id=ref,
                transaction_type="TOPUP",
                wallet_id=w.id,
                amount=w.balance,
                balance_after=w.balance,
                description="Opening balance",
            ))

    db.commit()
    print("Seed complete.")
