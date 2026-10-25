"""Fournisseur OpenRouter (https://openrouter.ai) : un accès à de nombreux modèles (Mistral,
DeepSeek, Qwen, Gemini, GPT, Claude…) par une API compatible « chat completions ».

Le format pivot reste celui de l'API Messages d'Anthropic (ADR-02) : ce module traduit les
messages et les outils à l'aller, et rend la réponse au même format (blocs ``text`` et
``tool_use``), si bien que la boucle d'outils et l'historique ne changent pas.

Données des fermes : ``provider.data_collection = "deny"`` exclut les hébergeurs qui
conservent ou réutilisent les requêtes, et ``require_parameters`` ceux qui ignorent les outils.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from aiffel.orchestrator.llm import Effort, LLMError, LLMResult, SystemPrompt, ToolCall, Usage
from aiffel.vault import SecretValue

API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRY_DELAYS = (2.0, 5.0, 12.0)


def _block(block: Any) -> dict[str, Any]:
    """Bloc de contenu en dictionnaire (objets du SDK Anthropic compris)."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return dict(block.model_dump())
    return {"type": "text", "text": str(block)}


def to_openai_messages(system: SystemPrompt, messages: list[dict[str, Any]]) -> list[Any]:
    out: list[dict[str, Any]] = [
        {"role": "system", "content": f"{system.stable}\n\n{system.volatile}"}
    ]
    for message in messages:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        blocks = [_block(b) for b in content]
        if role == "assistant":
            text = "\n".join(b["text"] for b in blocks if b.get("type") == "text" and b["text"])
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False),
                    },
                }
                for b in blocks
                if b.get("type") == "tool_use"
            ]
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        # Utilisateur : résultats d'outils (un message « tool » chacun) et texte éventuel.
        for b in blocks:
            if b.get("type") == "tool_result":
                result = b.get("content")
                if b.get("is_error"):
                    result = f"ERREUR : {result}"
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": result})
            elif b.get("type") == "text":
                out.append({"role": "user", "content": b["text"]})
    return out


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


_STOP_REASONS = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class OpenRouterProvider:
    def __init__(
        self,
        api_key: SecretValue,
        model: str,
        *,
        effort: Effort | None = None,
        max_tokens: int = 16000,
        http: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
        allow_data_collection: bool = False,
        retry_delays: tuple[float, ...] | None = None,
    ) -> None:
        self._key = api_key
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        # Évaluation sur données fictives seulement (modèles gratuits) : jamais en production.
        self.allow_data_collection = allow_data_collection
        self.retry_delays = RETRY_DELAYS if retry_delays is None else retry_delays
        self._http = http or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(
        self, system: SystemPrompt, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(system, messages),
            "max_tokens": self.max_tokens,
            "usage": {"include": True},
            "provider": {
                "data_collection": "allow" if self.allow_data_collection else "deny",
                "require_parameters": True,
            },
        }
        if tools:
            body["tools"] = to_openai_tools(tools)
        if self.effort is not None:
            # OpenRouter ne connaît que low, medium et high.
            level = {"xhigh": "high", "max": "high"}.get(self.effort, self.effort)
            body["reasoning"] = {"effort": level}
        response = await self._post(body)
        if response.status_code == 429:
            raise LLMError("limite de débit du fournisseur atteinte")
        try:
            data = response.json()
        except ValueError:
            raise LLMError(f"réponse illisible du fournisseur ({response.status_code})") from None
        if response.status_code >= 400 or "error" in data:
            code = (data.get("error") or {}).get("code", response.status_code)
            raise LLMError(f"erreur du fournisseur ({code})")
        return self._parse(data)

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        """POST avec nouvelles tentatives espacées sur 429 (limite de débit en amont, passagère)."""
        for delay in (*self.retry_delays, None):
            try:
                response = await self._http.post(
                    API_URL,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self._key.reveal()}",
                        "HTTP-Referer": "https://aiffel.org",
                        "X-Title": "Aiffel",
                    },
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"fournisseur injoignable ({type(exc).__name__})") from None
            if response.status_code != 429 or delay is None:
                return response
            await asyncio.sleep(delay)
        raise AssertionError("inaccessible")  # pragma: no cover

    def _parse(self, data: dict[str, Any]) -> LLMResult:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = (message.get("content") or "").strip()
        blocks: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        calls = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            call_id = call.get("id") or f"call_{index}"
            calls.append(ToolCall(call_id, function.get("name", ""), arguments))
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": function.get("name", ""),
                    "input": arguments,
                }
            )
        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        finish = str(choice.get("finish_reason") or "stop")
        return LLMResult(
            assistant_content=blocks,
            text=text,
            tool_calls=calls,
            stop_reason="tool_use" if calls else _STOP_REASONS.get(finish, finish),
            model=str(data.get("model") or self.model),
            usage=Usage(
                max(0, int(usage.get("prompt_tokens") or 0) - int(cached)),
                int(usage.get("completion_tokens") or 0),
                int(cached),
                0,
                float(usage.get("cost") or 0.0),
            ),
        )
