import inspect


def test_background_review_uses_resolver():
    from services.scheduler.background_review import run_background_memory_review

    src = inspect.getsource(run_background_memory_review)
    assert "context_length=128000" not in src
    assert "resolve_context_tokens" in src


def test_memory_consolidator_uses_resolver():
    from services.scheduler.memory_consolidator import maybe_consolidate_one_user

    src = inspect.getsource(maybe_consolidate_one_user)
    assert "context_length=128000" not in src
    assert "resolve_context_tokens" in src
