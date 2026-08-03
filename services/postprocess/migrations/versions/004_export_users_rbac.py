"""Export configuration, users table, and RBAC config.

Revision ID: 004
Revises: 003
Create Date: 2025-07-14

Adds:
- export_config JSONB column to app_settings with PDF/DOCX/naming defaults
- users table for local user cache (Cognito sub as PK)
- rbac_config table for updatable role-permission mappings
- Seed data for five default RBAC roles

Requirements: 3.1, 3.2, 3.4, 5.1, 5.4, 6.6
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Add export_config JSONB column to app_settings ---
    op.add_column(
        "app_settings",
        sa.Column(
            "export_config",
            sa.JSON,
            nullable=False,
            server_default=sa.text(
                """'{
  "pdf": {
    "pageSize": "A4",
    "marginsCm": 2.5,
    "fontFamily": "Times New Roman",
    "fontSizePt": 12,
    "pageNumbers": true,
    "parliamentCrest": true,
    "timestamps": false
  },
  "docx": {
    "hansardStyles": true,
    "trackChanges": false,
    "includeMetadata": true
  },
  "namingPattern": "Hansard_{sessionType}_{date}_{committee}"
}'::jsonb"""
            ),
        ),
    )

    # --- users table ---
    op.create_table(
        "users",
        sa.Column("id", sa.Text, primary_key=True),  # Cognito sub (UUID)
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "role",
            sa.Text,
            nullable=False,
            server_default=sa.text("'Viewer'"),
        ),
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            server_default=sa.text("'Pending'"),
        ),
        sa.Column("department", sa.Text, nullable=True),
        sa.Column("last_active", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('Admin', 'Chief Editor', 'Supervisor', 'Editor', 'Viewer')",
            name="ck_users_role",
        ),
        sa.CheckConstraint(
            "status IN ('Active', 'Inactive', 'Pending')",
            name="ck_users_status",
        ),
    )

    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_status", "users", ["status"])
    op.create_index("ix_users_email", "users", ["email"])

    # --- rbac_config table ---
    op.create_table(
        "rbac_config",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column(
            "permissions",
            sa.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("role", name="uq_rbac_role"),
    )

    # --- Seed default role-permission rows ---
    op.execute("""
        INSERT INTO rbac_config (role, permissions) VALUES
        ('Admin', ARRAY[
            'manage_users','system_config','create_sitting','assign_editor',
            'certify_record','manage_templates','export_hansard','view_audit_trail',
            'review_record','approve_certification','edit_record','upload_audio',
            'rename_speakers','submit_for_review','export_drafts','view_records',
            'search_hansard','export_published'
        ]),
        ('Chief Editor', ARRAY[
            'manage_users','create_sitting','assign_editor','certify_record',
            'manage_templates','export_hansard','view_audit_trail','review_record',
            'edit_record','upload_audio','rename_speakers','submit_for_review',
            'export_drafts','view_records','search_hansard','export_published'
        ]),
        ('Supervisor', ARRAY[
            'review_record','approve_certification','export_hansard',
            'view_audit_trail','export_drafts','view_records',
            'search_hansard','export_published'
        ]),
        ('Editor', ARRAY[
            'edit_record','upload_audio','rename_speakers','submit_for_review',
            'export_drafts','view_records','search_hansard','export_published'
        ]),
        ('Viewer', ARRAY[
            'view_records','search_hansard','export_published'
        ])
    """)


def downgrade() -> None:
    op.drop_table("rbac_config")
    op.drop_table("users")
    op.drop_column("app_settings", "export_config")
