from fastapi import APIRouter

ROUTER = APIRouter(tags=["health"])


@ROUTER.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
