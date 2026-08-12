import importlib
import inspect


def test_background_review_uses_resolver():
    import services.scheduler.background_review as br

    importlib.reload(br)
    src = inspect.getsource(br.run_background_memory_review)
    assert "context_length=128000" not in src
    assert "resolve_context_tokens" in src


def test_memory_consolidator_uses_resolver():
    import services.llm.prompt_engineer as pe

    importlib.reload(pe)
    src = inspect.getsource(pe.call_llm_once)
    assert "context_length=128000" not in src
    assert "resolve_context_tokens" in src
