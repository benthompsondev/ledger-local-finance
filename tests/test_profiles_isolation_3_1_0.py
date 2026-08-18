"""Profiles must isolate finances, not filter them.

This is the file that decides whether the feature is safe to ship. The
failure it exists to prevent is one person's money appearing on another
person's screen, so most of what follows seeds two obviously different
profiles and then asserts that nothing from one is reachable from the other:
not transactions, not accounts, not plans, not net worth, not preferences,
not the Home and Insights figures built on top of them, and not an import.

The architecture makes that testable rather than hopeful. A profile is a
directory, so "does profile B see profile A's data" is answered by opening
B's database and finding nothing there, which is a much stronger statement
than "no query forgot its WHERE clause".

Every figure here is synthetic.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from utils import profiles


# ── the registry itself ─────────────────────────────────────────────────

def test_an_existing_installation_becomes_the_default_profile(tmp_path):
    """The migration story: an installation that predates profiles.

    A database already sits at the root. Adopting it must not move, copy or
    rewrite it, because that is the one step that could damage the finances
    this feature is supposed to leave alone.
    """
    existing = tmp_path / "finance.db"
    existing.write_bytes(b"pretend sqlite bytes")
    before = existing.read_bytes()
    before_mtime = existing.stat().st_mtime_ns

    registry = profiles.ensure_registry(tmp_path)

    assert registry["active"] == profiles.DEFAULT_PROFILE_ID
    assert profiles.active_profile_dir(tmp_path) == tmp_path
    assert existing.read_bytes() == before, "the database was rewritten"
    assert existing.stat().st_mtime_ns == before_mtime, "it was touched"


def test_the_default_profile_has_a_sensible_name_and_can_be_renamed(tmp_path):
    profiles.ensure_registry(tmp_path)
    assert profiles.active_profile(tmp_path)["name"] == "My Finances"

    profiles.rename_profile(tmp_path, profiles.DEFAULT_PROFILE_ID, "Ben")
    assert profiles.active_profile(tmp_path)["name"] == "Ben"


def test_a_missing_registry_is_read_as_one_default_profile(tmp_path):
    """Every pre-profile installation looks exactly like this."""
    assert not (tmp_path / profiles.REGISTRY_NAME).exists()

    assert profiles.active_profile_id(tmp_path) == profiles.DEFAULT_PROFILE_ID
    assert profiles.active_profile_dir(tmp_path) == tmp_path


def test_a_corrupt_registry_falls_back_rather_than_stranding_the_user(tmp_path):
    (tmp_path / profiles.REGISTRY_NAME).write_text("{not json", encoding="utf-8")

    assert profiles.active_profile_id(tmp_path) == profiles.DEFAULT_PROFILE_ID


def test_a_new_profile_gets_its_own_directory_and_starts_empty(tmp_path):
    profiles.ensure_registry(tmp_path)
    (tmp_path / "finance.db").write_bytes(b"default data")

    created = profiles.create_profile(tmp_path, "Tori's Finances")
    directory = profiles.profile_dir(tmp_path, created["id"])

    assert directory != tmp_path
    assert directory.parent.name == profiles.PROFILES_DIRNAME
    assert directory.is_dir()
    assert not (directory / "finance.db").exists()


def test_profiles_cannot_share_a_name(tmp_path):
    profiles.ensure_registry(tmp_path)
    profiles.create_profile(tmp_path, "Test - RBC")

    with pytest.raises(profiles.ProfileError):
        profiles.create_profile(tmp_path, "test - rbc")


def test_switching_changes_which_directory_is_open(tmp_path):
    profiles.ensure_registry(tmp_path)
    created = profiles.create_profile(tmp_path, "Test - TD")

    assert profiles.active_profile_dir(tmp_path) == tmp_path
    profiles.switch_profile(tmp_path, created["id"])
    assert profiles.active_profile_dir(tmp_path) == profiles.profile_dir(
        tmp_path, created["id"]
    )


# ── deletion, where ambiguity would be dangerous ────────────────────────

def test_the_first_profile_cannot_be_deleted(tmp_path):
    """Its directory is the data root, so deleting it means deleting
    everything, including the other profiles and the registry."""
    profiles.ensure_registry(tmp_path)
    (tmp_path / "finance.db").write_bytes(b"real finances")

    with pytest.raises(profiles.ProfileError):
        profiles.delete_profile(tmp_path, profiles.DEFAULT_PROFILE_ID)

    assert (tmp_path / "finance.db").exists()


def test_the_open_profile_cannot_be_deleted(tmp_path):
    profiles.ensure_registry(tmp_path)
    created = profiles.create_profile(tmp_path, "Scratch")
    profiles.switch_profile(tmp_path, created["id"])

    with pytest.raises(profiles.ProfileError):
        profiles.delete_profile(tmp_path, created["id"])


def test_deleting_a_profile_removes_only_its_own_directory(tmp_path):
    profiles.ensure_registry(tmp_path)
    (tmp_path / "finance.db").write_bytes(b"default data")
    keep = profiles.create_profile(tmp_path, "Keep")
    drop = profiles.create_profile(tmp_path, "Drop")
    (profiles.profile_dir(tmp_path, keep["id"]) / "finance.db").write_bytes(b"keep")
    (profiles.profile_dir(tmp_path, drop["id"]) / "finance.db").write_bytes(b"drop")

    profiles.delete_profile(tmp_path, drop["id"])

    assert not profiles.profile_dir(tmp_path, drop["id"]).exists()
    assert (profiles.profile_dir(tmp_path, keep["id"]) / "finance.db").exists()
    assert (tmp_path / "finance.db").read_bytes() == b"default data"
    assert [p["id"] for p in profiles.list_profiles(tmp_path)["profiles"]] == [
        profiles.DEFAULT_PROFILE_ID, keep["id"],
    ]


def test_a_malformed_id_cannot_point_deletion_at_the_data_root(tmp_path):
    """The id reaches this from a request payload, so it is not trusted."""
    profiles.ensure_registry(tmp_path)
    (tmp_path / "finance.db").write_bytes(b"real finances")
    registry = json.loads((tmp_path / profiles.REGISTRY_NAME).read_text())
    registry["profiles"].append({"id": "../..", "name": "Escape"})
    registry["active"] = profiles.DEFAULT_PROFILE_ID
    (tmp_path / profiles.REGISTRY_NAME).write_text(json.dumps(registry))

    with pytest.raises(profiles.ProfileError):
        profiles.delete_profile(tmp_path, "../..")

    assert (tmp_path / "finance.db").exists()
    assert tmp_path.exists()


# ── the real thing: two profiles with different finances ────────────────

@pytest.fixture()
def two_profiles(tmp_path, monkeypatch):
    """Two profiles holding obviously different money.

    Seeded through the real engine rather than by writing files, so this
    exercises the same path the app uses: point the data root at a temp
    directory, choose a profile, import the modules, write finances.
    """
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    profiles.ensure_registry(tmp_path)
    second = profiles.create_profile(tmp_path, "Tori's Finances")

    def seed(profile_id: str, payload: dict) -> None:
        monkeypatch.setenv(profiles.ACTIVE_OVERRIDE_ENV, profile_id)
        import utils.platform_utils as pu
        import utils.database as db
        importlib.reload(pu)
        importlib.reload(db)
        db.init_db()
        conn = db.get_connection()
        from tests.conftest import add_tx
        for row in payload["transactions"]:
            add_tx(conn, **row)
        db.insert_account_balance({
            "account_name": payload["account"], "account_kind": "chequing",
            "balance": payload["balance"], "currency": "CAD",
            "as_of_date": "2026-08-01",
        }, conn=conn)
        db.upsert_monthly_plan({
            "month": "2026-08", "mode": "normal",
            "income_target": payload["balance"], "spending_target": 100.0,
            "savings_target": payload["savings"], "fixed_obligations": 0.0,
            "flexible_allowance": 100.0, "detected_commitments_at_save": 0.0,
            "fixed_override_reason": "", "notes": payload["account"],
        }, conn=conn)
        from utils.net_worth import save_entry
        save_entry("2026-08", cash=payload["balance"], investments=0,
                   other_assets=0, liabilities=0, note="", conn=conn)
        conn.commit()
        conn.close()

    seed(profiles.DEFAULT_PROFILE_ID, {
        "account": "BEN CHEQUING", "balance": 5000.0, "savings": 500.0,
        "transactions": [dict(day="2026-08-04", desc="BEN ONLY MERCHANT",
                              amount=111.11, direction="debit",
                              category="Groceries")],
    })
    seed(second["id"], {
        "account": "TORI CHEQUING", "balance": 900.0, "savings": 90.0,
        "transactions": [dict(day="2026-08-05", desc="TORI ONLY MERCHANT",
                              amount=222.22, direction="debit",
                              category="Shopping")],
    })
    return {"root": tmp_path, "second": second["id"], "monkeypatch": monkeypatch}


def _open(root: Path, profile_id: str, monkeypatch):
    """Open one profile the way a fresh engine process would."""
    monkeypatch.setenv("LEDGER_DATA_DIR", str(root))
    monkeypatch.setenv(profiles.ACTIVE_OVERRIDE_ENV, profile_id)
    import utils.platform_utils as pu
    import utils.database as db
    importlib.reload(pu)
    importlib.reload(db)
    return db


def test_the_two_profiles_use_different_databases(two_profiles):
    root, second = two_profiles["root"], two_profiles["second"]
    mp = two_profiles["monkeypatch"]

    first_db = _open(root, profiles.DEFAULT_PROFILE_ID, mp).DB_PATH
    second_db = _open(root, second, mp).DB_PATH

    assert first_db != second_db
    assert first_db.parent == root
    assert second_db.parent == profiles.profile_dir(root, second)
    assert first_db.is_file() and second_db.is_file()


def test_transactions_do_not_leak(two_profiles):
    root, second = two_profiles["root"], two_profiles["second"]
    mp = two_profiles["monkeypatch"]

    for profile_id, mine, theirs in (
        (profiles.DEFAULT_PROFILE_ID, "BEN ONLY MERCHANT", "TORI ONLY MERCHANT"),
        (second, "TORI ONLY MERCHANT", "BEN ONLY MERCHANT"),
    ):
        db = _open(root, profile_id, mp)
        conn = db.get_connection()
        rows = [
            str(r[0]) for r in conn.execute(
                "SELECT raw_description FROM transactions"
            ).fetchall()
        ]
        conn.close()
        assert any(mine in r for r in rows), f"{profile_id} lost its own data"
        assert not any(theirs in r for r in rows), (
            f"{profile_id} can see the other profile's transactions"
        )


def test_accounts_plans_and_net_worth_do_not_leak(two_profiles):
    root, second = two_profiles["root"], two_profiles["second"]
    mp = two_profiles["monkeypatch"]

    seen = {}
    for profile_id in (profiles.DEFAULT_PROFILE_ID, second):
        db = _open(root, profile_id, mp)
        conn = db.get_connection()
        accounts = [
            str(r[0]) for r in conn.execute(
                "SELECT account_name FROM account_balances"
            ).fetchall()
        ]
        plans = [
            float(r[0]) for r in conn.execute(
                "SELECT savings_target FROM monthly_plans"
            ).fetchall()
        ]
        net = [
            float(r[0]) for r in conn.execute(
                "SELECT cash FROM net_worth_entries"
            ).fetchall()
        ]
        conn.close()
        seen[profile_id] = (accounts, plans, net)

    ben_accounts, ben_plans, ben_net = seen[profiles.DEFAULT_PROFILE_ID]
    tori_accounts, tori_plans, tori_net = seen[second]

    assert ben_accounts == ["BEN CHEQUING"]
    assert tori_accounts == ["TORI CHEQUING"]
    assert ben_plans == [500.0] and tori_plans == [90.0]
    assert ben_net == [5000.0] and tori_net == [900.0]


def test_home_and_insights_figures_do_not_leak(two_profiles):
    """The screens, not just the tables underneath them."""
    root, second = two_profiles["root"], two_profiles["second"]
    mp = two_profiles["monkeypatch"]

    totals = {}
    for profile_id in (profiles.DEFAULT_PROFILE_ID, second):
        _open(root, profile_id, mp)
        import utils.insights as insights
        importlib.reload(insights)
        conn = __import__("utils.database", fromlist=["x"]).get_connection()
        months = insights.monthly_aggregates(conn=conn)
        conn.close()
        totals[profile_id] = round(
            sum(float(m.get("spending") or 0) for m in months), 2
        )

    assert totals[profiles.DEFAULT_PROFILE_ID] == pytest.approx(111.11, abs=0.01)
    assert totals[second] == pytest.approx(222.22, abs=0.01)


def test_an_import_only_reaches_the_open_profile(two_profiles):
    root, second = two_profiles["root"], two_profiles["second"]
    mp = two_profiles["monkeypatch"]

    db = _open(root, second, mp)
    conn = db.get_connection()
    from tests.conftest import add_tx
    add_tx(conn, day="2026-08-09", desc="IMPORTED INTO TORI", amount=44.0,
           direction="debit", category="Shopping")
    conn.commit()
    conn.close()

    db = _open(root, profiles.DEFAULT_PROFILE_ID, mp)
    conn = db.get_connection()
    rows = [
        str(r[0]) for r in conn.execute(
            "SELECT raw_description FROM transactions"
        ).fetchall()
    ]
    conn.close()
    assert not any("IMPORTED INTO TORI" in r for r in rows)


def test_backups_and_exports_land_inside_the_open_profile(two_profiles):
    root, second = two_profiles["root"], two_profiles["second"]
    mp = two_profiles["monkeypatch"]

    _open(root, second, mp)
    import utils.platform_utils as pu
    importlib.reload(pu)

    assert pu.get_backup_dir().parent == profiles.profile_dir(root, second)
    assert pu.get_exports_dir().parent == profiles.profile_dir(root, second)
    # The AI provider and key describe the installation, not one set of
    # finances, so that file stays at the root.
    assert pu.get_config_path().parent == root


def test_the_engine_binding_opens_the_active_profile(two_profiles):
    """The route the app actually takes, which is not the one above.

    Every other test here reloads utils.database and reads DB_PATH. The
    desktop engine does not: it calls _bind_database() and assigns DB_PATH
    itself. That function used to read LEDGER_DATA_DIR directly, which is
    the data root rather than the open profile, so every request went to the
    default profile's database whatever was selected. Switching looked like
    it did nothing and a second profile's imports landed somewhere nobody
    was reading. The module-level tests all passed throughout, which is why
    this one exists.
    """
    import sys

    root, second = two_profiles["root"], two_profiles["second"]
    mp = two_profiles["monkeypatch"]
    engine_dir = str(Path(__file__).resolve().parents[1] / "desktop" / "engine")
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)

    seen = {}
    for profile_id in (profiles.DEFAULT_PROFILE_ID, second):
        mp.setenv("LEDGER_DATA_DIR", str(root))
        mp.setenv(profiles.ACTIVE_OVERRIDE_ENV, profile_id)
        import utils.platform_utils as pu
        import utils.database as db
        importlib.reload(pu)
        importlib.reload(db)
        import ledger_engine
        importlib.reload(ledger_engine)
        ledger_engine._bind_database()
        conn = db.get_connection()
        rows = [
            str(r[0]) for r in conn.execute(
                "SELECT raw_description FROM transactions"
            ).fetchall()
        ]
        conn.close()
        seen[profile_id] = (db.DB_PATH, rows)

    default_path, default_rows = seen[profiles.DEFAULT_PROFILE_ID]
    second_path, second_rows = seen[second]

    assert default_path.parent == root
    assert second_path.parent == profiles.profile_dir(root, second)
    assert any("BEN ONLY" in r for r in default_rows)
    assert not any("TORI ONLY" in r for r in default_rows)
    assert any("TORI ONLY" in r for r in second_rows)
    assert not any("BEN ONLY" in r for r in second_rows)


def test_no_second_route_to_the_database_bypasses_profiles(two_profiles):
    """Guard against another _bind_database appearing.

    The leak was a module resolving the data location on its own instead of
    asking. Anything that joins a database filename onto LEDGER_DATA_DIR is
    doing that again.
    """
    import re

    engine = (
        Path(__file__).resolve().parents[1]
        / "desktop" / "engine" / "ledger_engine.py"
    ).read_text(encoding="utf-8")

    binder = engine[engine.index("def _bind_database"):]
    binder = binder[:binder.index("\ndef ")]
    assert "get_data_dir()" in binder, (
        "the engine must resolve the database through the profile-aware "
        "path helper"
    )
    assert not re.search(r"_data_root\(\)\s*/\s*filename", binder), (
        "the engine is joining the database onto the data root again, which "
        "skips the active profile"
    )


def test_closing_and_reopening_keeps_the_chosen_profile(two_profiles):
    """The registry is on disk, so the choice survives a restart. Each engine
    request is its own process, which is exactly a restart."""
    root, second = two_profiles["root"], two_profiles["second"]
    mp = two_profiles["monkeypatch"]

    mp.delenv(profiles.ACTIVE_OVERRIDE_ENV, raising=False)
    profiles.switch_profile(root, second)

    assert profiles.active_profile_id(root) == second
    assert profiles.active_profile(root)["name"] == "Tori's Finances"
