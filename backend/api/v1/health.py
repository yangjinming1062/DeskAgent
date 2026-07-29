from common import get_router

router = get_router()


@router.get("")
def health() -> dict[str, str]:
    return {"status": "ok"}
