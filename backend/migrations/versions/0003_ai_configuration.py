"""Encrypted live model configuration, managed through Mini App."""
from alembic import op
from sqlalchemy import select, update
from app.models import AIConfiguration, Chunk, Entry, Job, uid

revision = "0003"
down_revision = "0002"


def upgrade():
    bind = op.get_bind()
    AIConfiguration.__table__.create(bind, checkfirst=True)
    # Model identities now include provider URL and dimensions; old vectors are
    # regenerated so changing providers never compares unrelated vector spaces.
    bind.execute(update(Chunk).values(embedding=None, model=""))
    for identifier in bind.scalars(select(Entry.id).where(Entry.kind != "secret")):
        bind.execute(Job.__table__.insert().values(id=uid(), kind="index", target_id=identifier,
            status="pending", attempts=0, run_at=0, locked_until=0, error=""))
    bind.execute(update(Entry).where(Entry.kind != "secret").values(index_status="pending"))


def downgrade():
    raise RuntimeError("Export or reset AI configuration before an explicit downgrade")
