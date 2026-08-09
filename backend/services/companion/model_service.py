from components import get_logger
from components import SESSION_LOCAL
from modules.companion import CompanionModel
from modules.ws import WSEvent
from sqlalchemy.orm import Session

from .asset_store import build_signed_model_url

logger = get_logger(__name__)


class ModelGenerationError(RuntimeError):
    """3D model generation failed."""


def get_active_model(db: Session, user_id: int) -> CompanionModel | None:
    return db.query(CompanionModel).filter(CompanionModel.user_id == user_id, CompanionModel.active.is_(True)).one_or_none()


def signed_model_url(model: CompanionModel | None) -> str | None:
    """Never mutates the row — an ORM write would leak the expiring URL into the next autoflush."""
    if model is None or not model.asset_url or not model.asset_url.startswith("companion-models/"):
        return None
    parts = model.asset_url.split("/", 2)
    if len(parts) != 3:
        return None
    return build_signed_model_url(int(parts[1]), parts[2])


async def generate_companion_model(db: Session, *, user_id: int, species_override: str | None = None) -> CompanionModel:
    # The base-GLB pipeline is gone; fail explicitly until the Tripo3D
    # image-to-3D path lands so the API never serves a stale asset.
    raise ModelGenerationError("3D 模型生成服务正在升级（Tripo3D 接入中），请稍后再试")


def emit_wardrobe_updated(user_id: int) -> None:
    try:
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="wardrobe.updated", payload="{}"))
            db.commit()
    except Exception:
        logger.warning("Failed to emit wardrobe.updated event", exc_info=True)
