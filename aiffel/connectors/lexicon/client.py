"""Client MCP de Lexicon (https://lexicon.osfarm.org/mcp), référentiel en lecture seule (F-CON-5).

Une session MCP par appel (le serveur est sans état côté données), un cache mémoire des réponses,
et une limite d'appels pour respecter le débit du service (30 requêtes par fenêtre).
"""

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CACHE_TTL_S = 3600.0
CACHE_SIZE = 512


class LexiconError(Exception):
    pass


class LexiconClient:
    def __init__(self, url: str, *, max_concurrency: int = 4) -> None:
        self.url = url
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def _call_remote(self, name: str, arguments: dict[str, Any]) -> Any:
        async with self._semaphore:
            try:
                async with (
                    streamable_http_client(self.url) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
            except Exception as exc:
                raise LexiconError(f"Lexicon injoignable ({type(exc).__name__})") from None
        if getattr(result, "is_error", False):
            raise LexiconError("Lexicon a refusé la requête.")
        texts = [c.text for c in getattr(result, "content", []) if getattr(c, "type", "") == "text"]
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            return structured
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except ValueError:
            return joined

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        key = f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
        now = time.monotonic()
        if (hit := self._cache.get(key)) and now - hit[0] < CACHE_TTL_S:
            self._cache.move_to_end(key)
            return hit[1]
        value = await self._call_remote(name, arguments)
        self._cache[key] = (now, value)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return value
