"""Forme exacte des requêtes envoyées à l'API Messages (sans appel réseau)."""

import json
from typing import Any

import anthropic
import httpx2
import pytest

from aiffel.orchestrator.llm import FALLBACK_BETA, AnthropicProvider, LLMError, SystemPrompt

RESPONSE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "content": [
        {"type": "text", "text": "Je regarde."},
        {
            "type": "tool_use",
            "id": "tu_1",
            "name": "ekylibre_parcelles",
            "input": {"query": "noue"},
        },
    ],
    "usage": {
        "input_tokens": 120,
        "output_tokens": 30,
        "cache_read_input_tokens": 2000,
        "cache_creation_input_tokens": 0,
    },
}


def _provider(handler: Any) -> AnthropicProvider:
    client = anthropic.AsyncAnthropic(
        api_key="test",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicProvider(client, "claude-opus-5-5", effort="medium", max_tokens=16000)


async def test_request_shape_and_parsing() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        return httpx2.Response(200, json=RESPONSE)

    tools = [{"name": "ekylibre_parcelles", "description": "d", "input_schema": {"type": "object"}}]
    result = await _provider(handler).complete(
        SystemPrompt(stable="RÈGLES", volatile="Ferme : Demo"),
        [{"role": "user", "content": "Où est la Noue ?"}],
        tools,
    )

    body = seen["body"]
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000
    assert body["system"][0] == {
        "type": "text",
        "text": "RÈGLES",
        "cache_control": {"type": "ephemeral"},
    }
    assert body["system"][1] == {"type": "text", "text": "Ferme : Demo"}
    assert body["fallbacks"] == "default" and FALLBACK_BETA in seen["beta"]
    assert body["output_config"] == {"effort": "medium"}
    assert "tool_choice" not in body and "thinking" not in body and "temperature" not in body

    assert result.text == "Je regarde."
    assert [(c.name, c.input) for c in result.tool_calls] == [
        ("ekylibre_parcelles", {"query": "noue"})
    ]
    assert result.usage.cache_read_tokens == 2000 and result.stop_reason == "tool_use"
    assert len(result.assistant_content) == 2


async def test_refusal_is_reported() -> None:
    refused = {**RESPONSE, "stop_reason": "refusal", "content": []}
    result = await _provider(lambda _r: httpx2.Response(200, json=refused)).complete(
        SystemPrompt("s", "v"), [{"role": "user", "content": "x"}], []
    )
    assert result.refused and not result.tool_calls


@pytest.mark.parametrize("status", [429, 500, 400])
async def test_api_errors_become_llm_errors(status: int) -> None:
    error = {"type": "error", "error": {"type": "api_error", "message": "contenu sensible"}}
    with pytest.raises(LLMError) as excinfo:
        await _provider(lambda _r: httpx2.Response(status, json=error)).complete(
            SystemPrompt("s", "v"), [{"role": "user", "content": "x"}], []
        )
    assert "sensible" not in str(excinfo.value)
