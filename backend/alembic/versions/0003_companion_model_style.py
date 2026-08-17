"""companion_models.style: seed-style routing metadata (anime | realistic)

Rows default to 'realistic' so models generated before the NPR refactor keep
rendering under PBR; new generations carry the style of the seed they were
built from (see avatar_service._write_fullbody → model_service).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE companion_models ADD COLUMN IF NOT EXISTS style VARCHAR(16) NOT NULL DEFAULT 'realistic'")


def downgrade() -> None:
    op.execute("ALTER TABLE companion_models DROP COLUMN IF EXISTS style")
