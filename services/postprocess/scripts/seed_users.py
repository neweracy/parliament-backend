"""Seed or remove sample users (one per role) in the users table.

Usage:
    python scripts/seed_users.py          # Insert sample users
    python scripts/seed_users.py --remove # Remove sample users

Reads DATABASE_URL from environment or .env file.
"""

import os
import sys
from pathlib import Path

import bcrypt

# Allow running from the scripts/ directory or the postprocess root
SCRIPT_DIR = Path(__file__).resolve().parent
POSTPROCESS_ROOT = SCRIPT_DIR.parent

# Try loading .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    env_path = POSTPROCESS_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

import psycopg

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


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt (cost factor 10)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set. Export it or add to .env", file=sys.stderr)
        sys.exit(1)
    return url


def seed(conn):
    """Insert sample users with hashed passwords (skip if already exist)."""
    sql = """
        INSERT INTO users (id, email, name, role, status, department, last_active, password_hash)
        VALUES (%(id)s, %(email)s, %(name)s, %(role)s, %(status)s, %(department)s, now(), %(password_hash)s)
        ON CONFLICT (id) DO UPDATE SET password_hash = EXCLUDED.password_hash
    """
    with conn.cursor() as cur:
        for user in SEED_USERS:
            params = {k: v for k, v in user.items() if k != "password"}
            params["password_hash"] = hash_password(user["password"])
            cur.execute(sql, params)
    conn.commit()
    print(f"Seeded {len(SEED_USERS)} users (one per role) with passwords:")
    print()
    for u in SEED_USERS:
        print(f"  {u['role']:14s} -- {u['name']} <{u['email']}>  password: {u['password']}")
    print()

def remove(conn):
    """Remove all seeded sample users."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM users WHERE id = ANY(%s) RETURNING id, name",
            (SEED_IDS,),
        )
        deleted = cur.fetchall()
    conn.commit()
    if deleted:
        print(f"Removed {len(deleted)} seeded user(s):")
        for row in deleted:
            print(f"  {row[0]} ({row[1]})")
    else:
        print("No seeded users found to remove.")


def main():
    action = "--remove" in sys.argv or "--clean" in sys.argv

    database_url = get_database_url()
    with psycopg.connect(database_url) as conn:
        if action:
            remove(conn)
        else:
            seed(conn)


if __name__ == "__main__":
    main()
