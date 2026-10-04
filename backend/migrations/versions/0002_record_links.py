"""Explicit continuity and decision-result links."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "record_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("records.id"), nullable=False),
        sa.Column("child_id", sa.String(36), sa.ForeignKey("records.id"), nullable=False),
        sa.Column("relation", sa.String(20), nullable=False),
        sa.UniqueConstraint("user_id", "child_id"),
    )
    for name in ("user_id", "parent_id", "child_id"):
        op.create_index("ix_record_links_" + name, "record_links", [name])


def downgrade():
    op.drop_table("record_links")
