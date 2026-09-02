"""blocking_key fuer die Dublettensuche

Revision ID: 0002_blocking_key
Revises: 0001_initial
Create Date: 2026-09-01

Neue indizierte Spalte, ueber die Stufe 3 der Dublettenerkennung ihre
Kandidaten einschraenkt. Der Backfill berechnet den Schluessel fuer
Bestandsdaten aus den bereits normalisierten Spalten - dieselbe Regel wie
``tender_ai.models.common.blocking_key``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_blocking_key"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

#: muss zu tender_ai.models.common.BLOCKING_* passen
AUTHORITY_CHARS = 24
TITLE_WORDS = 3


def upgrade() -> None:
    with op.batch_alter_table("tenders") as batch:
        batch.add_column(sa.Column("blocking_key", sa.String(length=255), nullable=True))
    op.create_index("ix_tenders_blocking_key", "tenders", ["blocking_key"])

    # Backfill aus den vorhandenen normalisierten Spalten.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, title_normalized, authority_normalized FROM tenders")
    ).fetchall()
    for row in rows:
        authority = (row.authority_normalized or "")[:AUTHORITY_CHARS]
        title = " ".join((row.title_normalized or "").split()[:TITLE_WORDS])
        connection.execute(
            sa.text("UPDATE tenders SET blocking_key = :key WHERE id = :id"),
            {"key": f"{authority}|{title}", "id": row.id},
        )


def downgrade() -> None:
    op.drop_index("ix_tenders_blocking_key", table_name="tenders")
    with op.batch_alter_table("tenders") as batch:
        batch.drop_column("blocking_key")
