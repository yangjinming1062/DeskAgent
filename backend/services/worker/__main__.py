import asyncio

from . import handlers  # noqa: F401 — import side effect: registers render job kinds on HANDLERS
from .runner import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
