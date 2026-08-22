import os
import threading
import time

import envs.cleanup as env_cleanup
import pytest
import utils.scratch_size as scratch_size
from utils import IS_WINDOWS, get_scratch_size_bytes, reset_scratch_size_cache

_requires_symlink = pytest.mark.skipif(IS_WINDOWS, reason="Windows default config disallows symlink creation")


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_scratch_size_cache()
    yield
    reset_scratch_size_cache()


@pytest.fixture
def fake_scratch_root(tmp_path, monkeypatch):
    fake_root = tmp_path / "scratch"
    fake_root.mkdir()
    monkeypatch.setattr("envs._env_singularity.get_singularity_scratch_dir", lambda: fake_root)
    monkeypatch.setattr("envs.cleanup.get_singularity_scratch_dir", lambda: fake_root)
    return fake_root


def test_walk_recursive_real_size(fake_scratch_root):
    """递归统计真实总大小, 覆盖多层嵌套目录."""
    deep = fake_scratch_root / "spiritagent-x" / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_bytes(b"x" * 1234)
    (fake_scratch_root / "spiritagent-x" / "shallow.txt").write_bytes(b"y" * 100)

    snap = get_scratch_size_bytes()
    assert snap.total_bytes == 1234 + 100
    assert snap.file_count == 2
    assert snap.include_overlays is True


def test_walk_handles_broken_symlink(fake_scratch_root):
    """断链 symlink 不抛异常且跳过计数."""
    root = fake_scratch_root / "spiritagent-x"
    root.mkdir()
    (root / "real.txt").write_bytes(b"data")
    if IS_WINDOWS:
        pytest.skip("Windows default config disallows symlink creation")
    (root / "broken").symlink_to("nonexistent")

    snap = get_scratch_size_bytes()
    assert snap.file_count == 1
    assert snap.total_bytes == 4


@_requires_symlink
def test_walk_handles_symlink_loop_safely(fake_scratch_root):
    """循环目录软链不进入死循环."""
    a = fake_scratch_root / "spiritagent-x" / "a"
    b = fake_scratch_root / "spiritagent-x" / "b"
    a.mkdir(parents=True)
    b.mkdir()
    (a / "link_to_b").symlink_to(b, target_is_directory=True)
    (b / "link_to_a").symlink_to(a, target_is_directory=True)

    snap = get_scratch_size_bytes()
    assert snap.file_count == 0


def test_walk_handles_concurrent_dir_deletion(fake_scratch_root, monkeypatch):
    """遍历过程中目录被并发删除不崩溃."""
    root = fake_scratch_root / "spiritagent-x"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (sub / "f.txt").write_bytes(b"x")

    real_scandir = os.scandir

    def flaky_scandir(path):
        if str(path).endswith("sub"):
            raise FileNotFoundError(2, "No such file or directory", path)
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", flaky_scandir)
    snap = get_scratch_size_bytes()
    assert snap.total_bytes >= 0


def test_cache_keyed_by_include_overlays(fake_scratch_root):
    """不同 include_overlays 参数隔离缓存槽位."""
    overlay_dir = fake_scratch_root / "spiritagent-overlays"
    overlay_dir.mkdir()
    (overlay_dir / "x.bin").write_bytes(b"z" * 500)
    normal = fake_scratch_root / "spiritagent-y"
    normal.mkdir()
    (normal / "f.txt").write_bytes(b"q" * 100)

    snap_false = get_scratch_size_bytes(include_overlays=False)
    assert snap_false.total_bytes == 100
    assert snap_false.include_overlays is False

    snap_true = get_scratch_size_bytes(include_overlays=True)
    assert snap_true.total_bytes == 600
    assert snap_true.include_overlays is True


def test_single_walk_populates_both_variants(fake_scratch_root, monkeypatch):
    """单次扫描同时填充含与不含 overlays 两种缓存."""
    overlay_dir = fake_scratch_root / "spiritagent-overlays"
    overlay_dir.mkdir()
    (overlay_dir / "x.bin").write_bytes(b"z" * 50)
    normal = fake_scratch_root / "spiritagent-y"
    normal.mkdir()
    (normal / "f.txt").write_bytes(b"q" * 50)

    walk_calls = [0]
    real_walk = scratch_size._walk_all

    def counting_walk():
        walk_calls[0] += 1
        return real_walk()

    monkeypatch.setattr(scratch_size, "_walk_all", counting_walk)
    get_scratch_size_bytes(include_overlays=True, ttl_s=10.0)
    get_scratch_size_bytes(include_overlays=False, ttl_s=10.0)
    assert walk_calls[0] == 1
    assert True in scratch_size._cache
    assert False in scratch_size._cache
    assert scratch_size._cache[True].scanned_at_monotonic == scratch_size._cache[False].scanned_at_monotonic
    assert scratch_size._cache[True].total_bytes == 100
    assert scratch_size._cache[False].total_bytes == 50


def test_ttl_hit_within_window(fake_scratch_root):
    """TTL 窗口内重复读取直接命中缓存."""
    (fake_scratch_root / "spiritagent-x").mkdir()

    walk_calls = [0]
    real_walk = scratch_size._walk_all

    def counting_walk():
        walk_calls[0] += 1
        return real_walk()

    scratch_size._walk_all = counting_walk
    try:
        get_scratch_size_bytes(ttl_s=0.5)
        get_scratch_size_bytes(ttl_s=0.5)
    finally:
        scratch_size._walk_all = real_walk

    assert walk_calls[0] == 1


def test_ttl_miss_after_expiry(fake_scratch_root):
    """TTL 过期后触发重新扫描."""
    (fake_scratch_root / "spiritagent-x").mkdir()

    walk_calls = [0]
    real_walk = scratch_size._walk_all

    def counting_walk():
        walk_calls[0] += 1
        return real_walk()

    scratch_size._walk_all = counting_walk
    try:
        get_scratch_size_bytes(ttl_s=0.05)
        time.sleep(0.1)
        get_scratch_size_bytes(ttl_s=0.05)
    finally:
        scratch_size._walk_all = real_walk

    assert walk_calls[0] == 2


def test_reset_clears_both_cache_slots(fake_scratch_root):
    """清空缓存后强制重新扫描."""
    (fake_scratch_root / "spiritagent-x").mkdir()

    walk_calls = [0]
    real_walk = scratch_size._walk_all

    def counting_walk():
        walk_calls[0] += 1
        return real_walk()

    scratch_size._walk_all = counting_walk
    try:
        get_scratch_size_bytes(ttl_s=10.0)
        get_scratch_size_bytes(include_overlays=False, ttl_s=10.0)
        assert walk_calls[0] == 1
        reset_scratch_size_cache()
        get_scratch_size_bytes(ttl_s=10.0)
    finally:
        scratch_size._walk_all = real_walk

    assert walk_calls[0] == 2


def test_reset_accepts_task_id_for_hook_compat(fake_scratch_root):
    """清理 hook 兼容传递 task_id 参数."""
    (fake_scratch_root / "spiritagent-x").mkdir()
    get_scratch_size_bytes()
    assert True in scratch_size._cache
    reset_scratch_size_cache("some-task-id")
    assert True not in scratch_size._cache
    assert False not in scratch_size._cache


def test_concurrent_readers_serialize_via_lock(fake_scratch_root):
    """并发读取由锁同步并保证仅单次扫描."""
    (fake_scratch_root / "spiritagent-x").mkdir()

    walk_calls = [0]
    real_walk = scratch_size._walk_all

    def counting_walk():
        walk_calls[0] += 1
        time.sleep(0.05)
        return real_walk()

    scratch_size._walk_all = counting_walk
    try:
        threads = [threading.Thread(target=get_scratch_size_bytes, kwargs={"ttl_s": 10.0}) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        scratch_size._walk_all = real_walk

    assert walk_calls[0] == 1


def test_hook_invalidates_on_cleanup_vm(fake_scratch_root):
    """注册的环境清理 hook 触发时失效缓存."""
    saved_hooks = list(env_cleanup._cleanup_hooks)
    env_cleanup._cleanup_hooks.clear()
    try:
        env_cleanup.register_env_cleanup_hook(reset_scratch_size_cache)
        (fake_scratch_root / "spiritagent-x").mkdir()
        get_scratch_size_bytes()
        assert True in scratch_size._cache
        for hook in env_cleanup._cleanup_hooks:
            hook("fake-task-id")
        assert True not in scratch_size._cache
    finally:
        env_cleanup._cleanup_hooks.clear()
        env_cleanup._cleanup_hooks.extend(saved_hooks)


def test_orphan_sweep_invalidates_cache(fake_scratch_root):
    """批量环境清理与孤儿扫描完成后失效缓存."""
    overlay_dir = fake_scratch_root / "spiritagent-overlays"
    overlay_dir.mkdir()
    (overlay_dir / "x.bin").write_bytes(b"z" * 50)
    target = fake_scratch_root / "spiritagent-y"
    target.mkdir()
    (target / "f.txt").write_bytes(b"q" * 50)

    get_scratch_size_bytes()
    assert True in scratch_size._cache

    env_cleanup.cleanup_all_environments()
    assert True not in scratch_size._cache
    assert False not in scratch_size._cache
    assert not target.exists()
    assert overlay_dir.exists()
