"""Initial schema with pgvector and full text indexes."""
from alembic import op
from app.db import Base
from app import models

revision = "0001"
down_revision = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind)
    if bind.dialect.name == "postgresql":
        op.execute("CREATE INDEX chunks_vector_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
        op.execute("CREATE INDEX chunks_text_gin ON chunks USING gin(to_tsvector('russian', text))")


def downgrade():
    Base.metadata.drop_all(op.get_bind())
