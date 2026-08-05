import json
from types import SimpleNamespace

import httpx
import pytest
from services.llm import MissingLlmConfigError
from services.llm import ProviderError


def _async_handler(responses):
    """Build an async httpx handler that returns the next queued response."""
    queue = list(responses)

    async def handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            return httpx.Response(500, text="queue empty")
        item = queue.pop(0)
        if isinstance(item, tuple):
            status, payload = item
            return httpx.Response(status, json=payload)
        return httpx.Response(200, json=item)

    return handler


class TestVideoGenJobModel:
    def test_video_gen_job_tablename(self):
        from modules.media.models import VideoGenJob

        assert VideoGenJob.__tablename__ == "video_gen_jobs"

    def test_required_columns_present(self):
        from modules.media.models import VideoGenJob

        names = {c.name for c in VideoGenJob.__table__.columns}
        for col in (
            "user_id",
            "session_id",
            "provider",
            "model",
            "prompt",
            "params_json",
            "status",
            "provider_task_id",
            "provider_file_id",
            "file_id",
            "video_url",
            "error_reason",
            "error_message",
            "created_at",
            "updated_at",
            "expires_at",
        ):
            assert col in names, f"missing column {col}"


class TestVideoGenRestEndpoints:
    def test_endpoints_registered(self, test_app):
        paths = set()

        def _walk(routes):
            for route in routes:
                path = getattr(route, "path", None)
                if path is not None:
                    paths.add(path)
                # FastAPI wraps included routers in _IncludedRouter whose
                # inner routes live on ``.original_router.routes``.
                orig = getattr(route, "original_router", None)
                if orig is not None:
                    _walk(orig.routes)

        _walk(test_app.routes)
        assert "/api/media/video_gen" in paths, "POST /video_gen not registered"
        assert "/api/media/video_gen/{task_id}" in paths, "GET /video_gen/{task_id} not registered"


class TestVideoGenJobRoundtrip:
    """End-to-end submit → poll → success path using a mock provider."""

    @pytest.mark.asyncio
    async def test_submit_then_poll_then_download(self, monkeypatch, _patch_db, test_token):
        from components import SESSION_LOCAL
        from modules.auth import User, UserModelConfig
        from services.media import enqueue_video_job, get_job
        import asyncio

        # Seed a user with MiniMax config
        with SESSION_LOCAL() as db:
            user = db.query(User).filter(User.username == "testuser").first()
            user_id = user.id
            cfg = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).first()
            cfg.video_gen_base_url = "https://api.minimaxi.com"
            cfg.video_gen_api_key = "sk-test"
            cfg.video_gen_model_name = "MiniMax-H3"
            db.commit()

        # Mock transport: submit returns task_id, poll returns succeeded with inline download_url
        submit_calls = []
        poll_calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            # The CDN download URL is absolute, not relative — match by full URL.
            if str(request.url) == "https://example.com/video.mp4":
                return httpx.Response(
                    200,
                    content=b"\x00\x00\x00\x18ftypmoov",
                    headers={"content-type": "video/mp4"},
                )
            if path == "/v2/video_generation":
                submit_calls.append(json.loads(request.content))
                return httpx.Response(200, json={"base_resp": {"status_code": 0}, "task_id": "task-test-1"})
            if path.startswith("/v2/query/video_generation/"):
                poll_calls.append(path.split("/")[-1])
                return httpx.Response(
                    200,
                    json={
                        "base_resp": {"status_code": 0},
                        "task": {
                            "status": "succeeded",
                            "content": {"url": "https://example.com/video.mp4"},
                        },
                    },
                )
            return httpx.Response(404, json={"error": "not found", "path": path})

        # Eagerly register a mock-transport-backed client so the cached
        # ``get_http`` lookup returns our mock instead of building a real
        # httpx client whose internal transport we can't easily swap.
        # Replace ``services.media.video_jobs.httpx`` wholesale so the bare
        # ``httpx.AsyncClient(...)`` inside ``_stream_download`` is also
        # intercepted (the CDN URL points to ``example.com``, which would
        # otherwise hit the open internet).
        import services.llm.providers.http as http_mod
        import services.media.video_jobs as video_jobs_mod

        def _mock_async_client(timeout=None, **kwargs):
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                headers=kwargs.get("headers"),
                timeout=timeout,
            )

        fake_httpx = SimpleNamespace(
            Timeout=httpx.Timeout,
            AsyncClient=_mock_async_client,
        )
        monkeypatch.setattr(video_jobs_mod, "httpx", fake_httpx)

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.minimaxi.com",
            headers={"Authorization": "Bearer sk-test"},
        )
        http_mod._clients[("https://api.minimaxi.com", "sk-test")] = mock_client

        with SESSION_LOCAL() as db:
            job = await enqueue_video_job(
                db,
                user_id=user_id,
                session_id=None,
                prompt="a cat playing piano",
                duration=6,
                resolution="768P",
                first_frame_image=None,
                model=None,
                aspect_ratio=None,
            )
            job_id = job.id
            assert job.status == "queued"
            assert job.provider_task_id == "task-test-1"

        # Let the polling task complete (it polls every 5s in settings)
        for _ in range(20):
            await asyncio.sleep(0.2)
            with SESSION_LOCAL() as db:
                row = get_job(db, job_id, user_id)
            if row.status in ("succeeded", "failed"):
                break

        assert row.status == "succeeded", f"job ended in {row.status}: {row.error_message}"
        assert row.video_url.startswith("http"), f"video_url should be our public URL, got {row.video_url!r}"
        assert row.file_id is not None
        assert submit_calls and poll_calls, "submit + poll endpoints should have been hit"

    @pytest.mark.asyncio
    async def test_provider_failure_marks_job_failed(self, monkeypatch, _patch_db, test_token):
        # Bypass the multi-session visibility question: drive everything
        # through the same SESSION_LOCAL session so the commit happens in
        # the same transaction the test reads.
        from components import SESSION_LOCAL
        from modules.auth import User, UserModelConfig
        from services.media import enqueue_video_job
        import services.llm.providers.http as http_mod

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v2/video_generation":
                return httpx.Response(200, json={"base_resp": {"status_code": 1004, "status_msg": "auth fail"}})
            return httpx.Response(404)

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.minimaxi.com",
            headers={"Authorization": "Bearer sk-test"},
        )
        http_mod._clients.clear()
        http_mod._clients[("https://api.minimaxi.com", "sk-test")] = mock_client

        with SESSION_LOCAL() as db:
            user = db.query(User).filter(User.username == "testuser").first()
            user_id = user.id
            cfg = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).first()
            cfg.video_gen_base_url = "https://api.minimaxi.com"
            cfg.video_gen_api_key = "sk-test"
            cfg.video_gen_model_name = "MiniMax-H3"
            db.commit()

            with pytest.raises((MissingLlmConfigError, ProviderError, ValueError)):
                await enqueue_video_job(
                    db,
                    user_id=user_id,
                    session_id=None,
                    prompt="x",
                    duration=6,
                    resolution="768P",
                    first_frame_image=None,
                    model=None,
                    aspect_ratio=None,
                )

            # Read via the SAME session the test session — _update_job
            # opens its own session which on SQLite under SAVEPOINT may
            # have visibility issues, so trust the test session.
            from modules.media.models import VideoGenJob
            from sqlalchemy import select

            db.expire_all()
            rows = db.execute(select(VideoGenJob).where(VideoGenJob.user_id == user_id)).scalars().all()
            assert rows, "expected a failed job row"
            assert rows[0].status == "failed", f"row status: {rows[0].status}"
            assert rows[0].error_reason == "submit_failed"
