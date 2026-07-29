# The service/orchestration layer. This package intentionally does NOT re-export
# its subpackages' names — import directly, e.g. ``from services.chat import run_chat_turn``.
# Tool registration is triggered explicitly in ``main.py`` (the subpackage ``__init__``
# modules self-register on import).
