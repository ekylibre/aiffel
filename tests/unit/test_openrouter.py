import json
from typing import Any

import httpx
import pytest

from aiffel.orchestrator.llm import LLMError, SystemPrompt
from aiffel.providers.openrouter import OpenRouterProvider, to_openai_messages
from aiffel.vault import SecretValue

SYSTEM = SystemPrompt(stable="règles", volatile="ferme")
TOOLS = [
    {"name": "ekylibre_parcelles", "description": "Parcelles", "input_schema": {"type": "object"}}
]
HISTORY: list[dict[str, Any]] = [
    {"role": "user", "content": "mes parcelles ?"},
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Je regarde."},
            {"type": "tool_use", "id": "t1", "name": "ekylibre_parcelles", "input": {}},
        ],
    },
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "[P12]", "is_error": False},
        ],
    },
]


def test_pivot_messages_are_translated() -> None:
    out = to_openai_messages(SYSTEM, HISTORY)
    assert out[0] == {"role": "system", "content": "règles\n\nferme"}
    assert out[1] == {"role": "user", "content": "mes parcelles ?"}
    assert out[2]["role"] == "assistant" and out[2]["content"] == "Je regarde."
    [call] = out[2]["tool_calls"]
    assert call["id"] == "t1" and call["function"] == {
        "name": "ekylibre_parcelles",
        "arguments": "{}",
    }
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": "[P12]"}


def _provider(handler: Any) -> OpenRouterProvider:
    return OpenRouterProvider(
        SecretValue("sk-or-SECRET"),
        "mistralai/mistral-small-2603",
        effort="max",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_delays=(0.0, 0.0),
    )


async def test_request_and_tool_call_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "mistralai/mistral-small-2603",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {
                                        "name": "ekylibre_parcelles",
                                        "arguments": '{"campagne": 2026}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 50,
                    "prompt_tokens_details": {"cached_tokens": 400},
                    "cost": 0.0012,
                },
            },
        )

    result = await _provider(handler).complete(SYSTEM, HISTORY, TOOLS)
    body = seen["body"]
    assert seen["auth"] == "Bearer sk-or-SECRET"
    assert body["provider"] == {"data_collection": "deny", "require_parameters": True}
    assert body["reasoning"] == {"effort": "high"}  # « max » ramené au niveau le plus haut
    assert body["tools"][0]["function"]["name"] == "ekylibre_parcelles"
    [call] = result.tool_calls
    assert (call.id, call.name, call.input) == ("c1", "ekylibre_parcelles", {"campagne": 2026})
    assert result.stop_reason == "tool_use"
    assert result.assistant_content == [
        {"type": "tool_use", "id": "c1", "name": "ekylibre_parcelles", "input": {"campagne": 2026}}
    ]
    assert result.usage.input_tokens == 600 and result.usage.cache_read_tokens == 400
    assert result.usage.cost_usd == pytest.approx(0.0012)


async def test_errors_hide_the_key_and_content() -> None:
    def failing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"code": 402, "message": "crédit sk-or-SECRET"}})

    with pytest.raises(LLMError) as excinfo:
        await _provider(failing).complete(SYSTEM, HISTORY, TOOLS)
    assert "SECRET" not in str(excinfo.value)

    def broken_arguments(_request: httpx.Request) -> httpx.Response:
        message = {"tool_calls": [{"id": "c", "function": {"name": "x", "arguments": "{oops"}}]}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    result = await _provider(broken_arguments).complete(SYSTEM, HISTORY, TOOLS)
    assert result.tool_calls[0].input == {}  # la passerelle refusera et le modèle corrigera


async def test_rate_limit_is_retried_then_reported() -> None:
    calls = []

    def flaky(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 2:
            return httpx.Response(429, json={"error": {"code": 429}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert (await _provider(flaky).complete(SYSTEM, HISTORY, TOOLS)).text == "ok"

    def always(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"code": 429}})

    with pytest.raises(LLMError, match="limite de débit"):
        await _provider(always).complete(SYSTEM, HISTORY, TOOLS)
