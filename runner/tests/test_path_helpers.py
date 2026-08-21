from unittest import mock

from utils.path_helpers import SANE_PATH, append_sane_path_entries


def test_append_sane_path_entries_posix_merges_deduped():
    """POSIX branch: existing entries keep their order, SANE_PATH fills gaps, no dupes."""
    with mock.patch("utils.path_helpers.IS_WINDOWS", False):
        merged = append_sane_path_entries("/usr/local/bin:/usr/bin:/extra")
    assert merged.startswith("/usr/local/bin:/usr/bin:/extra:")
    assert "/opt/homebrew/bin" in merged
    assert merged.count("/usr/bin") == 1
    assert merged == ":".join(dict.fromkeys(merged.split(":")))


def test_append_sane_path_entries_posix_empty_path():
    with mock.patch("utils.path_helpers.IS_WINDOWS", False):
        assert append_sane_path_entries("") == SANE_PATH
