from . import auth, companion, conversation, memory, scheduler, settings, system, update, ws

# Importing each package registers its ORM models on ``ModelBase.metadata`` so
# ``create_all`` sees every table. ``modules.media`` is intentionally omitted
# because its mapper triggers an import-time crash on stale DB connections —
# callers that need ``VideoGenJob`` on ``create_all`` must import it explicitly.

__all__ = [
    "auth",
    "companion",
    "conversation",
    "memory",
    "scheduler",
    "settings",
    "system",
    "update",
    "ws",
]
