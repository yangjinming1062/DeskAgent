"""companion_models: paid-result download recovery columns

Adds provider_task_id + download_urls_json so a completed (billed) image-to-3D
generation survives a failed download: the pipeline persists both the moment
generation completes, before the download starts. See model_service
(pending_download → downloading → succeeded | download_failed state machine).

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companion_models", sa.Column("provider_task_id", sa.String(length=128), nullable=True))
    op.add_column("companion_models", sa.Column("download_urls_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("companion_models", "download_urls_json")
    op.drop_column("companion_models", "provider_task_id")
