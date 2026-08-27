"""Entrypoint detected by the Apify CLI: python actors are looked up as
``src/__main__.py``. Works both as a plain file and as ``python -m src``."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import main  # noqa: E402

asyncio.run(main())
