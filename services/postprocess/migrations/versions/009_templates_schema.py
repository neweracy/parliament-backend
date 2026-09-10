"""Templates schema: persisted Hansard document templates.

Creates the ``template`` table backing the Templates page, which previously
rendered hardcoded frontend fixtures. Header lines and sections are stored as
JSONB so the template body structure stays editable without further migrations.

A partial unique index enforces the "at most one default template" invariant at
the database level, and the four templates the Templates page currently shows
are seeded so behaviour is unchanged after the migration.

Revision ID: 009
Revises: 008
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_SPEAKER_FORMAT = "{name} ({constituency}):"


def _section(
    section_id: str,
    kind: str,
    content: str,
    *,
    uppercase: bool,
    bold: bool,
) -> dict[str, Any]:
    """Build one template body section."""
    return {
        "id": section_id,
        "type": kind,
        "content": content,
        "uppercase": uppercase,
        "bold": bold,
    }


def _headings(*titles: str) -> list[dict[str, Any]]:
    """Build uppercase bold heading sections, numbered from "1"."""
    return [
        _section(str(ordinal), "heading", title, uppercase=True, bold=True)
        for ordinal, title in enumerate(titles, start=1)
    ]


def upgrade() -> None:
    op.create_table(
        "template",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "type",
            sa.Text,
            nullable=False,
            server_default=sa.text("'Custom'"),
        ),
        sa.Column(
            "icon_key",
            sa.Text,
            nullable=False,
            server_default=sa.text("'file-text'"),
        ),
        sa.Column(
            "is_default",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "usage_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "header_lines",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "sections",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "speaker_format",
            sa.Text,
            nullable=False,
            server_default=sa.text("'{name} ({constituency}):'"),
        ),
        sa.Column(
            "show_timestamps",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "show_constituency",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "paragraph_numbering",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
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
            "type IN ('Plenary', 'Committee', 'Special', 'Custom')",
            name="template_type_check",
        ),
        sa.CheckConstraint(
            "icon_key IN ('book-open', 'users', 'gavel', 'file-text')",
            name="template_icon_key_check",
        ),
        sa.CheckConstraint(
            "usage_count >= 0",
            name="template_usage_count_check",
        ),
    )

    # At most one default template: the index key is the constant expression
    # ((true)), so every row matching the partial WHERE is_default predicate
    # collides on the same key. Rows with is_default = false are excluded from
    # the index entirely and stay unconstrained.
    op.execute(
        "CREATE UNIQUE INDEX ux_template_single_default ON template ((true)) WHERE is_default"
    )

    op.create_index("ix_template_type", "template", ["type"])

    # --- Seed the templates the Templates page currently renders ---
    # Lightweight table definition for bulk_insert. JSONB-typed columns let
    # SQLAlchemy serialise the Python structures, avoiding manual quoting.
    template_table = sa.table(
        "template",
        sa.column("name", sa.Text),
        sa.column("description", sa.Text),
        sa.column("type", sa.Text),
        sa.column("icon_key", sa.Text),
        sa.column("is_default", sa.Boolean),
        sa.column("usage_count", sa.Integer),
        sa.column("header_lines", postgresql.JSONB),
        sa.column("sections", postgresql.JSONB),
        sa.column("speaker_format", sa.Text),
        sa.column("show_timestamps", sa.Boolean),
        sa.column("show_constituency", sa.Boolean),
        sa.column("paragraph_numbering", sa.Boolean),
    )

    op.bulk_insert(
        template_table,
        [
            {
                "name": "Official Hansard \u2014 Plenary",
                "description": (
                    "Standard template for full House plenary sittings. "
                    "Includes Prayers, Statements, Questions, Motions, and "
                    "Bills sections."
                ),
                "type": "Plenary",
                "icon_key": "book-open",
                "is_default": True,
                "usage_count": 142,
                "header_lines": [
                    "PARLIAMENT OF GHANA",
                    "OFFICIAL REPORT (HANSARD)",
                    "{parliament}",
                    "{date}",
                    "{sessionType} \u2022 Presiding: {presidingOfficer}",
                ],
                "sections": [
                    _section("1", "heading", "PRAYERS", uppercase=True, bold=True),
                    _section(
                        "2",
                        "body",
                        "The House met at {startTime}.",
                        uppercase=False,
                        bold=False,
                    ),
                    _section("3", "divider", "", uppercase=False, bold=False),
                    _section("4", "heading", "STATEMENTS", uppercase=True, bold=True),
                    _section("5", "heading", "QUESTIONS", uppercase=True, bold=True),
                    _section("6", "heading", "MOTIONS", uppercase=True, bold=True),
                    _section("7", "heading", "BILLS", uppercase=True, bold=True),
                ],
                "speaker_format": DEFAULT_SPEAKER_FORMAT,
                "show_timestamps": False,
                "show_constituency": True,
                "paragraph_numbering": False,
            },
            {
                "name": "Committee Report",
                "description": (
                    "Template for committee hearings and deliberations. "
                    "Includes attendance, agenda items, testimony, and "
                    "recommendations."
                ),
                "type": "Committee",
                "icon_key": "users",
                "is_default": False,
                "usage_count": 67,
                "header_lines": [
                    "PARLIAMENT OF GHANA",
                    "COMMITTEE REPORT",
                    "{committee}",
                    "{date}",
                    "Chairperson: {presidingOfficer}",
                ],
                "sections": _headings(
                    "ATTENDANCE",
                    "AGENDA",
                    "PROCEEDINGS",
                    "RECOMMENDATIONS",
                ),
                "speaker_format": DEFAULT_SPEAKER_FORMAT,
                "show_timestamps": False,
                "show_constituency": True,
                "paragraph_numbering": False,
            },
            {
                "name": "Special Sitting",
                "description": (
                    "Template for emergency and special sittings. Streamlined "
                    "format with focus on the specific matter at hand."
                ),
                "type": "Special",
                "icon_key": "gavel",
                "is_default": False,
                "usage_count": 12,
                "header_lines": [
                    "PARLIAMENT OF GHANA",
                    "SPECIAL SITTING",
                    "OFFICIAL REPORT (HANSARD)",
                    "{date}",
                    "Presiding: {presidingOfficer}",
                ],
                "sections": _headings(
                    "MATTER BEFORE THE HOUSE",
                    "PROCEEDINGS",
                    "RESOLUTION",
                ),
                "speaker_format": DEFAULT_SPEAKER_FORMAT,
                "show_timestamps": False,
                "show_constituency": True,
                "paragraph_numbering": False,
            },
            {
                "name": "Votes & Proceedings",
                "description": (
                    "Summary record of decisions taken, motions passed, and "
                    "divisions called during a sitting."
                ),
                "type": "Custom",
                "icon_key": "file-text",
                "is_default": False,
                "usage_count": 34,
                "header_lines": [
                    "PARLIAMENT OF GHANA",
                    "VOTES AND PROCEEDINGS",
                    "{date}",
                ],
                "sections": _headings(
                    "BUSINESS TRANSACTED",
                    "MOTIONS",
                    "DIVISIONS",
                    "PAPERS LAID",
                ),
                "speaker_format": DEFAULT_SPEAKER_FORMAT,
                "show_timestamps": False,
                "show_constituency": True,
                "paragraph_numbering": False,
            },
        ],
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_template_single_default")
    op.drop_index("ix_template_type", table_name="template")
    op.drop_table("template")
