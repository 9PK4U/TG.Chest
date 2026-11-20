"""Explicit model boundaries and durable encrypted original inputs."""
from alembic import op
from sqlalchemy import Column, String, inspect, text
from app.models import Capture

revision = "0002"
down_revision = "0001"


def upgrade():
    bind = op.get_bind()
    # 0001 historically used live metadata.create_all; also support fresh installs.
    columns = {c["name"] for c in inspect(bind).get_columns("entries")}
    if "ai_policy" not in columns:
        # Existing content was not explicitly classified. Never send it again by default.
        op.add_column("entries", Column("ai_policy", String(12), nullable=False, server_default="trusted"))
        bind.execute(text("UPDATE chunks SET embedding=NULL, model=''"))
    if "capture_id" not in columns:
        op.add_column("entries", Column("capture_id", String(32), nullable=True))
        op.create_index("ix_entries_capture_id", "entries", ["capture_id"])
    if "bot_ai_policy" not in {c["name"] for c in inspect(bind).get_columns("users")}:
        op.add_column("users", Column("bot_ai_policy", String(12), nullable=False, server_default="external"))
    Capture.__table__.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("Privacy migration requires an explicit data migration to downgrade")
