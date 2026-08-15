import asyncio

from .handlers import register
from .runner import main

if __name__ == "__main__":
    register()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
