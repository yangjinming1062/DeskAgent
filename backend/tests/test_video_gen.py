import json
from types import SimpleNamespace

import httpx
import pytest

from services.llm import MissingLlmConfigError, ProviderError


def _async_handler(responses):
    """构造一个按队列顺序返回响应的异步 httpx handler。"""
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
        from modules.media import VideoGenJob

        assert VideoGenJob.__tablename__ == "video_gen_jobs"

    def test_required_columns_present(self):
        from modules.media import VideoGenJob

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
                # FastAPI 把 included router 包装为 _IncludedRouter，内部路由位于 .original_router.routes。
                orig = getattr(route, "original_router", None)
                if orig is not None:
                    _walk(orig.routes)

        _walk(test_app.routes)
        assert "/api/media/video_gen" in paths, "POST /video_gen not registered"
        assert "/api/media/video_gen/{task_id}" in paths, (
            "GET /video_gen/{task_id} not registered"
        )


class TestVideoGenJobRoundtrip:
    """使用 mock 供应商的端到端 submit → poll → success 路径。"""

    async def _run_roundtrip(
        self, monkeypatch, *, model, handler, duration, resolution, aspect_ratio
    ):
        """写入用户配置、安装 mock transport、入队任务并等待其到达终态，返回最终 job 行。"""
        import asyncio

        from sqlalchemy import select

        from components import SESSION_LOCAL
        from modules.auth import User, UserModelConfig
        from services.media import enqueue_video_job, get_job

        async with SESSION_LOCAL() as db:
            user = (
                (await db.execute(select(User).where(User.username == "testuser")))
                .scalars()
                .first()
            )
            user_id = user.id
            cfg = (
                (
                    await db.execute(
                        select(UserModelConfig).where(
                            UserModelConfig.user_id == user_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            cfg.video_gen_base_url = "https://api.minimaxi.com"
            cfg.video_gen_api_key = "sk-test"
            cfg.video_gen_model_name = model
            await db.commit()

        # 预先注册一个 mock transport 客户端，使缓存的 get_http 返回我们的 mock，避免创建内部 transport 难以替换的真正 httpx 客户端。
        # 整体替换 services.media.video_jobs.httpx，以同时拦截 _stream_download 内部的裸 httpx.AsyncClient（CDN URL 指向 example.com，否则会访问公网）。
        import services.llm.providers.http as http_mod

        def _mock_async_client(timeout=None, **kwargs):
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                headers=kwargs.get("headers"),
                timeout=timeout,
            )

        monkeypatch.setattr(
            "components.network.safe_outbound_async_client", _mock_async_client
        )
        monkeypatch.setattr(
            "components.network.is_safe_outbound", lambda host: (True, "")
        )

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.minimaxi.com",
            headers={"Authorization": "Bearer sk-test"},
        )
        http_mod._clients[("https://api.minimaxi.com", "sk-test")] = mock_client

        async with SESSION_LOCAL() as db:
            job = await enqueue_video_job(
                db,
                user_id=user_id,
                session_id=None,
                prompt="a cat playing piano",
                duration=duration,
                resolution=resolution,
                first_frame_image=None,
                model=None,
                aspect_ratio=aspect_ratio,
            )
            job_id = job.id
            assert job.status == "queued"
            assert job.provider_task_id == "task-test-1"

        # 等待轮询任务完成（设置中默认每 5 秒轮询一次）
        for _ in range(20):
            await asyncio.sleep(0.2)
            async with SESSION_LOCAL() as db:
                row = await get_job(db, job_id, user_id)
            if row.status in ("succeeded", "failed"):
                break
        return row

    @pytest.mark.asyncio
    async def test_v1_submit_poll_retrieve_download(
        self, monkeypatch, _patch_db, test_token
    ):
        """默认路径：MiniMax-Hailuo v1 三段式（submit → poll → files/retrieve → download）。"""
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            # CDN 下载 URL 是绝对地址而非相对路径，需按完整 URL 匹配。
            if str(request.url) == "https://example.com/video.mp4":
                return httpx.Response(
                    200,
                    content=b"\x00\x00\x00\x18ftypmoov",
                    headers={"content-type": "video/mp4"},
                )
            if path == "/v1/video_generation":
                calls.append("submit")
                body = json.loads(request.content)
                assert body["prompt"] == "a cat playing piano"
                assert "content" not in body
                return httpx.Response(
                    200,
                    json={"base_resp": {"status_code": 0}, "task_id": "task-test-1"},
                )
            if path == "/v1/query/video_generation":
                calls.append("poll")
                return httpx.Response(
                    200,
                    json={
                        "base_resp": {"status_code": 0},
                        "status": "Success",
                        "file_id": "file-1",
                    },
                )
            if path == "/v1/files/retrieve":
                calls.append("retrieve")
                assert request.url.params["file_id"] == "file-1"
                return httpx.Response(
                    200,
                    json={
                        "base_resp": {"status_code": 0},
                        "file": {
                            "download_url": "https://example.com/video.mp4",
                            "content_type": "video/mp4",
                        },
                    },
                )
            return httpx.Response(404, json={"error": "not found", "path": path})

        row = await self._run_roundtrip(
            monkeypatch,
            model="MiniMax-Hailuo-2.3",
            handler=handler,
            duration=6,
            resolution="768P",
            aspect_ratio=None,
        )
        assert row.status == "succeeded", (
            f"job ended in {row.status}: {row.error_message}"
        )
        assert row.video_url.startswith("/api/media/files/"), (
            f"video_url should be our relative media path, got {row.video_url!r}"
        )
        assert row.file_id is not None
        assert calls == ["submit", "poll", "retrieve"], calls

    @pytest.mark.asyncio
    async def test_v2_h3_inline_download_url_skips_retrieve(
        self, monkeypatch, _patch_db, test_token
    ):
        """H3 v2 路径：poll 直接返回 URL 内联，因此不会调用 files/retrieve。"""
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if str(request.url) == "https://example.com/video.mp4":
                return httpx.Response(
                    200,
                    content=b"\x00\x00\x00\x18ftypmoov",
                    headers={"content-type": "video/mp4"},
                )
            if path == "/v2/video_generation":
                calls.append("submit")
                body = json.loads(request.content)
                assert body["content"][0]["text"] == "a cat playing piano"
                return httpx.Response(
                    200,
                    json={"base_resp": {"status_code": 0}, "task_id": "task-test-1"},
                )
            if path.startswith("/v2/query/video_generation/"):
                calls.append("poll")
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

        row = await self._run_roundtrip(
            monkeypatch,
            model="MiniMax-H3",
            handler=handler,
            duration=6,
            resolution="768P",
            aspect_ratio="16:9",
        )
        assert row.status == "succeeded", (
            f"job ended in {row.status}: {row.error_message}"
        )
        assert row.video_url.startswith("/api/media/files/")
        assert calls == ["submit", "poll"], calls

    @pytest.mark.asyncio
    async def test_provider_failure_marks_job_failed(
        self, monkeypatch, _patch_db, test_token
    ):
        # 绕过多 session 可见性问题：全程复用同一个 SESSION_LOCAL，使 commit 与读取落在同一事务。
        from sqlalchemy import select

        import services.llm.providers.http as http_mod
        from components import SESSION_LOCAL
        from modules.auth import User, UserModelConfig
        from services.media import enqueue_video_job

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/video_generation":
                return httpx.Response(
                    200,
                    json={
                        "base_resp": {"status_code": 1004, "status_msg": "auth fail"}
                    },
                )
            return httpx.Response(404)

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.minimaxi.com",
            headers={"Authorization": "Bearer sk-test"},
        )
        http_mod._clients.clear()
        http_mod._clients[("https://api.minimaxi.com", "sk-test")] = mock_client

        async with SESSION_LOCAL() as db:
            user = (
                (await db.execute(select(User).where(User.username == "testuser")))
                .scalars()
                .first()
            )
            user_id = user.id
            cfg = (
                (
                    await db.execute(
                        select(UserModelConfig).where(
                            UserModelConfig.user_id == user_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            cfg.video_gen_base_url = "https://api.minimaxi.com"
            cfg.video_gen_api_key = "sk-test"
            cfg.video_gen_model_name = "MiniMax-Hailuo-2.3"
            await db.commit()

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

            # 通过与测试相同的 session 读取——_update_job 会自开 session，在 SQLite + SAVEPOINT 下可能存在可见性问题，故以测试 session 为准。
            from sqlalchemy import select

            from modules.media import VideoGenJob

            db.expire_all()
            rows = (
                (
                    await db.execute(
                        select(VideoGenJob).where(VideoGenJob.user_id == user_id)
                    )
                )
                .scalars()
                .all()
            )
            assert rows, "expected a failed job row"
            assert rows[0].status == "failed", f"row status: {rows[0].status}"
            assert rows[0].error_reason == "submit_failed"


@pytest.mark.asyncio
async def test_video_gen_status_endpoint_returns_reason(
    test_client, test_app, SessionLocal
):
    from modules.auth import User, get_current_session
    from modules.media import VideoGenJob

    async with SessionLocal() as db:
        user = User(username="vg-user", is_active=True, can_use=True)
        db.add(user)
        await db.flush()
        job = VideoGenJob(
            user_id=user.id,
            prompt="test",
            provider="minimax",
            model="video-01",
            status="failed",
            error_reason="poll_failed",
            error_message="视频生成失败，请稍后重试",
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    test_app.dependency_overrides[get_current_session] = lambda: (user, None)
    try:
        res = await test_client.get(f"/api/media/video_gen/{job_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "failed"
        assert data["reason"] == "poll_failed"
        assert data["error"] == "视频生成失败，请稍后重试"
    finally:
        test_app.dependency_overrides.pop(get_current_session, None)
