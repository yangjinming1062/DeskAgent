from services.llm import aclose_all

from .video_jobs import enqueue_video_job, get_job, resume_pending_video_jobs

__all__ = ["aclose_all", "enqueue_video_job", "get_job", "resume_pending_video_jobs"]


async def aclose_media() -> None:
    """Close cached httpx / AsyncOpenAI clients owned by the LLM provider pool."""
    await aclose_all()
