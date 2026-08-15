"""drop companion_sprite_images.role NULL default

The column declared ``DEFAULT NULL`` — a no-op on a nullable column that
Postgres stores as ``NULL::character varying`` and alembic's
``compare_server_default`` rightly flags against the model's ``NULL``
literal. Drop it on both sides so strict default comparison stays clean.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-15

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE companion_sprite_images ALTER COLUMN role DROP DEFAULT")


def downgrade() -> None:
    op.execute("ALTER TABLE companion_sprite_images ALTER COLUMN role SET DEFAULT NULL")
