"""normalize legacy drift: tighten nullability widened by ADD COLUMN IF NOT EXISTS

Pre-Alembic databases were built by create_all + ``ADD COLUMN IF NOT EXISTS``,
which never tightens nullability or widens types — legacy rows drifted from the
models (nullable importance/kind/assembly_json, float4 importance, orphan
embedding_provider column). All operations are idempotent and no-op on fresh
baseline databases.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-15

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE memories SET importance = 1.0 WHERE importance IS NULL")
    op.execute("ALTER TABLE memories ALTER COLUMN importance TYPE DOUBLE PRECISION USING importance::double precision")
    op.execute("ALTER TABLE memories ALTER COLUMN importance SET NOT NULL")
    op.execute("UPDATE wardrobe_items SET kind = 'texture' WHERE kind IS NULL")
    op.execute("UPDATE wardrobe_items SET assembly_json = '{}' WHERE assembly_json IS NULL")
    op.execute("ALTER TABLE wardrobe_items ALTER COLUMN kind SET NOT NULL")
    op.execute("ALTER TABLE wardrobe_items ALTER COLUMN assembly_json SET NOT NULL")
    # Dead column from the pre-Alembic 6-cap provider ALTER loop; zero code references.
    op.execute("ALTER TABLE user_model_configs DROP COLUMN IF EXISTS embedding_provider")


def downgrade() -> None:
    op.execute("ALTER TABLE user_model_configs ADD COLUMN IF NOT EXISTS embedding_provider VARCHAR(64) DEFAULT ''")
    op.execute("ALTER TABLE wardrobe_items ALTER COLUMN assembly_json DROP NOT NULL")
    op.execute("ALTER TABLE wardrobe_items ALTER COLUMN kind DROP NOT NULL")
    op.execute("ALTER TABLE memories ALTER COLUMN importance DROP NOT NULL")
    op.execute("ALTER TABLE memories ALTER COLUMN importance TYPE REAL USING importance::real")
