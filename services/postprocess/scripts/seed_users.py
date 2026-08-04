"""Seed or remove development fixture users in the users table.

Usage:
    python scripts/seed_users.py          # Upsert development fixtures
    python scripts/seed_users.py --remove # Remove development fixtures

Reads DATABASE_URL and BCRYPT_COST from environment or .env file.

SECURITY:
    - Refuses to run when NODE_ENV=production.
    - All passwords are hashed with bcrypt at the configured cost before storage.
    - Complete rollback on any hash failure; no partial seeds.
    - Accounts are marked as development/demo fixtures (is_dev_fixture=true).
    - No password, hash, or partial value is disclosed in error messages.
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Production refusal — terminate before any database connection or write
# ---------------------------------------------------------------------------
_NODE_ENV = os.environ.get("NODE_ENV", "")
if _NODE_ENV == "production":
    print(
        "ERROR: seed_users.py is a development-only tool and cannot run "
        "when NODE_ENV=production. Aborting without changes.",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Environment and path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
POSTPROCESS_ROOT = SCRIPT_DIR.parent

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv

    env_path = POSTPROCESS_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

import bcrypt  # noqa: E402
import psycopg  # noqa: E402

# ---------------------------------------------------------------------------
# Bcrypt cost validation (accept only 12, 13, or 14)
# ---------------------------------------------------------------------------
_ALLOWED_COSTS = {"12", "13", "14"}
_BCRYPT_COST_RAW = os.environ.get("BCRYPT_COST", "12")

if _BCRYPT_COST_RAW not in _ALLOWED_COSTS:
    print(
        "ERROR: BCRYPT_COST must be 12, 13, or 14 (canonical decimal). "
        "Got invalid value. Aborting.",
        file=sys.stderr,
    )
    sys.exit(1)

BCRYPT_COST: int = int(_BCRYPT_COST_RAW)

# ---------------------------------------------------------------------------
# Fixture definitions (approved test accounts)
# ---------------------------------------------------------------------------
SEED_USERS = [
    {
        "id": "usr-admin-001",
        "email": "admin@parliament.gov.gh",
        "name": "Kwame Adjei",
        "role": "Admin",
        "status": "Active",
        "department": "IT Department",
        "password": "admin123",
    },
    {
        "id": "usr-chief-editor-001",
        "email": "chief.editor@parliament.gov.gh",
        "name": "Sarah Mensah",
        "role": "Chief Editor",
        "status": "Active",
        "department": "Hansard Department",
        "password": "editor123",
    },
    {
        "id": "usr-supervisor-001",
        "email": "supervisor@parliament.gov.gh",
        "name": "Kofi Arhin",
        "role": "Supervisor",
        "status": "Active",
        "department": "Hansard Department",
        "password": "super123",
    },
    {
        "id": "usr-editor-001",
        "email": "editor@parliament.gov.gh",
        "name": "Ama Boateng",
        "role": "Editor",
        "status": "Active",
        "department": "Hansard Department",
        "password": "editor123",
    },
    {
        "id": "usr-viewer-001",
        "email": "viewer@parliament.gov.gh",
        "name": "Nana Agyeman",
        "role": "Viewer",
        "status": "Active",
        "department": "Clerk's Office",
        "password": "viewer123",
    },
]

SEED_IDS = [u["id"] for u in SEED_USERS]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt at the configured cost.

    Raises on any failure — caller is responsible for rollback.
    Never includes password or hash material in raised exceptions.
    """
    try:
        hashed = bcrypt.hashpw(
            plain.encode("utf-8"),
            bcrypt.gensalt(rounds=BCRYPT_COST),
        )
        return hashed.decode("utf-8")
    except Exception:
        # Re-raise a sanitized error — no password/hash content
        raise RuntimeError("Password hashing failed for a fixture account") from None


def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print(
            "ERROR: DATABASE_URL is not set. Export it or add to .env",
            file=sys.stderr,
        )
        sys.exit(1)
    return url


# ---------------------------------------------------------------------------
# Seed operation — transactional with full rollback on any failure
# ---------------------------------------------------------------------------

# Upsert SQL: if account exists by email, update the hash and fixture flag.
# This allows re-running the seed to reset passwords.
_UPSERT_SQL = """
    INSERT INTO users
        (id, email, name, role, status, department, last_active, password_hash, is_dev_fixture)
    VALUES
        (%(id)s, %(email)s, %(name)s, %(role)s, %(status)s, %(department)s,
         now(), %(password_hash)s, true)
    ON CONFLICT (email) DO UPDATE SET
        password_hash = EXCLUDED.password_hash,
        name = EXCLUDED.name,
        role = EXCLUDED.role,
        status = EXCLUDED.status,
        department = EXCLUDED.department,
        is_dev_fixture = true,
        last_active = now()
"""


def seed(conn):
    """Upsert development fixture accounts with bcrypt-hashed passwords.

    All inserts run in a single transaction. If any hash operation fails,
    the entire seed is rolled back and no changes are committed.
    """
    # Pre-hash all passwords BEFORE touching the database.
    # If any hash fails, we abort without any database writes.
    hashed_users = []
    try:
        for user in SEED_USERS:
            password_hash = _hash_password(user["password"])
            params = {k: v for k, v in user.items() if k != "password"}
            params["password_hash"] = password_hash
            hashed_users.append(params)
    except RuntimeError:
        # Rollback: since we haven't started any DB work, just report and exit
        print(
            "ERROR: Failed to hash credentials for fixture accounts. "
            "No changes were made to the database.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Execute all upserts in a single transaction
    try:
        with conn.cursor() as cur:
            for params in hashed_users:
                cur.execute(_UPSERT_SQL, params)
        conn.commit()
    except Exception:
        conn.rollback()
        print(
            "ERROR: Database operation failed during seed. "
            "All changes have been rolled back. No partial data was written.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Success output — no passwords or hashes disclosed
    print(f"Seeded {len(SEED_USERS)} development fixture account(s):")
    print()
    for u in SEED_USERS:
        print(f"  {u['role']:14s} -- {u['name']} <{u['email']}> [dev fixture]")
    print()
    print(f"All passwords hashed with bcrypt (cost factor {BCRYPT_COST}).")
    print("Accounts marked as is_dev_fixture=true.")


# ---------------------------------------------------------------------------
# Remove operation
# ---------------------------------------------------------------------------


def remove(conn):
    """Remove all seeded development fixture users."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM users WHERE id = ANY(%s) RETURNING id, name",
            (SEED_IDS,),
        )
        deleted = cur.fetchall()
    conn.commit()
    if deleted:
        print(f"Removed {len(deleted)} development fixture user(s):")
        for row in deleted:
            print(f"  {row[0]} ({row[1]})")
    else:
        print("No development fixture users found to remove.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main():
    action = "--remove" in sys.argv or "--clean" in sys.argv

    database_url = _get_database_url()

    # Use autocommit=False so we have explicit transaction control
    with psycopg.connect(database_url, autocommit=False) as conn:
        if action:
            remove(conn)
        else:
            seed(conn)


if __name__ == "__main__":
    main()
