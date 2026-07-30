from services.llm import aclose_all as _close_llm_http

from .video_jobs import enqueue_video_job
from .video_jobs import get_job
from .video_jobs import resume_pending_video_jobs

__all__ = ["aclose_all", "enqueue_video_job", "get_job", "resume_pending_video_jobs"]


async def aclose_all() -> None:
    """Close cached httpx / AsyncOpenAI clients owned by the LLM provider pool.

    FastAPI lifespan ``finally`` block awaits this on shutdown so
    rolling deploys release connection pools + file descriptors instead
    of leaking them until the kernel reaps the process.
    """
    await _close_llm_http()
