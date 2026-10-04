"""Keep recording segments in capture order across retries and restoration."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("attachments", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("attachments", "position")
