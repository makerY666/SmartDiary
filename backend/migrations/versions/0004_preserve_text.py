"""Protect accepted text when restoring processed phone attachments."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "attachments", sa.Column("preserve_text", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade():
    op.drop_column("attachments", "preserve_text")
