import pytest

from tools.files.native_ops import NativeFileOperations


@pytest.fixture
def tmp_cwd(tmp_path):
    cwd = str(tmp_path)
    return NativeFileOperations(cwd=cwd), tmp_path


def test_read_file(tmp_cwd):
    ops, cwd = tmp_cwd
    test_file = cwd / "test.txt"
    test_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = ops.read_file("test.txt")
    assert result.error is None
    assert result.total_lines == 3
    assert "1|line1" in result.content
    assert "3|line3" in result.content


def test_read_file_pagination(tmp_cwd):
    ops, cwd = tmp_cwd
    test_file = cwd / "test.txt"
    test_file.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    result = ops.read_file("test.txt", offset=2, limit=2)
    assert result.error is None
    assert result.total_lines == 4
    assert "2|line2" in result.content
    assert "3|line3" in result.content
    assert "1|" not in result.content
    assert "4|" not in result.content
    assert result.truncated is True


def test_write_file(tmp_cwd):
    ops, cwd = tmp_cwd

    result = ops.write_file("new_dir/new_file.txt", "hello world")
    assert result.error is None
    assert result.dirs_created is True

    written_file = cwd / "new_dir" / "new_file.txt"
    assert written_file.exists()
    assert written_file.read_text(encoding="utf-8") == "hello world"


def test_patch_replace(tmp_cwd):
    ops, cwd = tmp_cwd
    test_file = cwd / "test.txt"
    test_file.write_text("hello\nworld\nhello", encoding="utf-8")

    # Error when replace_all is False but multiple occur
    result = ops.patch_replace("test.txt", "hello", "hi")
    assert result.success is False
    assert "Use replace_all=True" in result.error

    # Success when replace_all is True
    result = ops.patch_replace("test.txt", "hello", "hi", replace_all=True)
    assert result.success is True
    assert test_file.read_text(encoding="utf-8") == "hi\nworld\nhi"


def test_search(tmp_cwd):
    ops, cwd = tmp_cwd
    (cwd / "file1.txt").write_text("foo\nbar\nbaz", encoding="utf-8")
    (cwd / "file2.txt").write_text("baz\nqux", encoding="utf-8")
    (cwd / "ignore_dir").mkdir()
    (cwd / "ignore_dir" / "file3.txt").write_text("bar", encoding="utf-8")

    result = ops.search("bar", path=".")
    assert result.error is None
    assert result.total_count == 2
    paths = {m.path.replace("\\", "/") for m in result.matches}
    assert "file1.txt" in paths
    assert "ignore_dir/file3.txt" in paths

    # Test file glob
    result = ops.search("bar", path=".", file_glob="*.txt")
    assert result.total_count == 2


def test_binary_detection(tmp_cwd):
    ops, cwd = tmp_cwd
    test_file = cwd / "test.bin"
    test_file.write_bytes(b"\x00\x01\x02\x03")

    result = ops.read_file("test.bin")
    assert result.is_binary is True
    assert result.error is not None


def test_legacy_encoding_text(tmp_cwd):
    ops, cwd = tmp_cwd
    test_file = cwd / "test_gbk.txt"
    # Write some non-utf8 but valid text in another encoding
    test_file.write_bytes("你好世界".encode("gbk"))

    # Because non-printable ascii count is low, it should NOT be flagged as binary,
    # and reading it with replace will yield replacement chars, avoiding crash.
    result = ops.read_file("test_gbk.txt")
    assert result.error is None
    assert result.is_binary is False


def test_delete_file(tmp_cwd):
    ops, cwd = tmp_cwd
    test_file = cwd / "test.txt"
    test_file.write_text("foo")

    assert test_file.exists()
    res = ops.delete_file("test.txt")
    assert res.error is None
    assert not test_file.exists()


def test_delete_path(tmp_cwd):
    ops, cwd = tmp_cwd
    test_dir = cwd / "test_dir"
    test_dir.mkdir()
    (test_dir / "test.txt").write_text("foo")

    # Non-recursive should fail
    res = ops.delete_path("test_dir", recursive=False)
    assert res.error is not None
    assert test_dir.exists()

    # Recursive should succeed
    res = ops.delete_path("test_dir", recursive=True)
    assert res.error is None
    assert not test_dir.exists()


def test_move_file(tmp_cwd):
    ops, cwd = tmp_cwd
    test_file = cwd / "test.txt"
    test_file.write_text("foo")

    res = ops.move_file("test.txt", "new_dir/test.txt")
    assert res.error is None
    assert not test_file.exists()
    assert (cwd / "new_dir" / "test.txt").exists()


def test_read_file_raw(tmp_cwd):
    ops, cwd = tmp_cwd
    test_file = cwd / "test.txt"
    test_file.write_text("foo\nbar")

    res = ops.read_file_raw("test.txt")
    assert res.error is None
    assert res.content == "foo\nbar"


def test_patch_v4a(tmp_cwd):
    ops, _ = tmp_cwd
    res = ops.patch_v4a("patch")
    assert res.success is False
    assert res.error is not None


def test_bom_preservation(tmp_cwd):
    ops, cwd = tmp_cwd
    test_file = cwd / "test.txt"
    test_file.write_bytes(b"\xef\xbb\xbffoo")

    res = ops.write_file("test.txt", "foo")
    assert res.error is None
    assert test_file.read_bytes() == b"\xef\xbb\xbffoo"


def test_search_inside_dotdir_root(tmp_cwd):
    """A search root that itself sits under a dot-dir is not wholesale skipped."""
    ops, cwd = tmp_cwd
    hidden_root = cwd / ".spiritagent"
    hidden_root.mkdir()
    (hidden_root / "file1.txt").write_text("target", encoding="utf-8")
    (hidden_root / ".hidden").mkdir()
    (hidden_root / ".hidden" / "file2.txt").write_text("target", encoding="utf-8")

    result = ops.search("target", path=".spiritagent")
    assert result.error is None
    assert result.total_count == 1
    assert {m.path.replace("\\", "/") for m in result.matches} == {"file1.txt"}


def test_search_reads_legacy_encoding_files(tmp_cwd):
    ops, cwd = tmp_cwd
    (cwd / "gbk.txt").write_bytes("hello 世界".encode("gbk"))

    result = ops.search("hello", path=".")
    assert result.total_count == 1


def test_search_exact_limit_not_truncated(tmp_cwd):
    ops, cwd = tmp_cwd
    (cwd / "f.txt").write_text("hit\nhit\nhit", encoding="utf-8")

    assert ops.search("hit", path=".", limit=3).truncated is False
    assert ops.search("hit", path=".", limit=2).truncated is True


def test_exec_timeout_returns_partial_stdout(tmp_cwd, monkeypatch):
    """text=True makes TimeoutExpired.stdout a str; decoding it again used to raise."""
    import subprocess

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=cmd, timeout=kwargs.get("timeout"), output="partial output"
        )

    monkeypatch.setattr("tools.files.native_ops.subprocess.run", fake_run)
    ops, _ = tmp_cwd
    result = ops._exec("whatever", timeout=1)
    assert result.exit_code == 124
    assert "partial output" in result.stdout


class TestFuzzyNonOverlappingExact:
    """Regression: _strategy_exact used to produce overlapping matches
    (start = pos + 1), double-replacing replace_all ranges and double-
    counting single occurrences."""

    def test_overlapping_pattern_counts_once(self):
        from tools.files.fuzzy_match import _strategy_exact

        assert _strategy_exact("aaa", "aa") == [(0, 2)]

    def test_replace_all_overlap_replaces_once(self):
        from tools.files.fuzzy_match import fuzzy_find_and_replace

        content, count, strategy, error = fuzzy_find_and_replace("aaa", "aa", "b", True)
        assert error is None
        assert count == 1
        assert content == "ba"
