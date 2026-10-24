import json

import httpx
import pytest

from aiffel.channels.base import Button, InboundKind, OutboundMessage
from aiffel.channels.identity import hash_external_id
from aiffel.channels.telegram.api import TelegramClient, TelegramError, split_text
from aiffel.channels.telegram.parse import parse_update
from aiffel.vault import SecretValue

USER = {"id": 4242, "first_name": "Marie", "language_code": "fr"}
PRIVATE = {"id": 4242, "type": "private"}


def _message(**fields: object) -> dict[str, object]:
    return {"update_id": 1, "message": {"message_id": 7, "from": USER, "chat": PRIVATE, **fields}}


def test_parse_text() -> None:
    msg = parse_update(_message(text="J'ai traité les Grands Champs"))
    assert msg is not None and msg.kind is InboundKind.TEXT
    assert msg.external_user_id == "4242" and msg.external_chat_id == "4242"
    assert msg.first_name == "Marie"


def test_parse_command_with_bot_suffix_and_args() -> None:
    msg = parse_update(_message(text="/Start@AiffelBot abc"))
    assert msg is not None and msg.kind is InboundKind.COMMAND
    assert msg.command == "start" and msg.command_args == "abc"


def test_parse_voice() -> None:
    msg = parse_update(_message(voice={"file_id": "F1", "duration": 12}))
    assert msg is not None and msg.kind is InboundKind.VOICE
    assert msg.file_id == "F1" and msg.duration_s == 12


def test_parse_photo_takes_largest() -> None:
    photos = [{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 999}]
    msg = parse_update(_message(photo=photos, caption="mildiou ?"))
    assert msg is not None and msg.kind is InboundKind.PHOTO
    assert msg.file_id == "big" and msg.text == "mildiou ?"


def test_parse_location() -> None:
    msg = parse_update(_message(location={"latitude": 46.6, "longitude": -1.4}))
    assert msg is not None and msg.kind is InboundKind.LOCATION and msg.latitude == 46.6


def test_parse_callback() -> None:
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "from": USER,
            "data": "pa:validate:123",
            "message": {"message_id": 9, "chat": PRIVATE},
        },
    }
    msg = parse_update(update)
    assert msg is not None and msg.kind is InboundKind.CALLBACK
    assert msg.callback_id == "cb1" and msg.callback_data == "pa:validate:123"


@pytest.mark.parametrize(
    "update",
    [
        {"update_id": 3, "edited_message": {"text": "x"}},
        {
            "update_id": 4,
            "message": {"from": USER, "chat": {"id": -1, "type": "group"}, "text": "x"},
        },
        {"update_id": 5, "channel_post": {"text": "x"}},
    ],
)
def test_ignored_updates(update: dict[str, object]) -> None:
    assert parse_update(update) is None


def test_unsupported_content() -> None:
    msg = parse_update(_message(sticker={"file_id": "S"}))
    assert msg is not None and msg.kind is InboundKind.UNSUPPORTED


def test_split_text() -> None:
    assert split_text("court") == ["court"]
    long_text = ("mot " * 3000).strip()
    chunks = split_text(long_text, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert " ".join(chunks) == long_text


def test_hash_is_salted_and_channel_bound() -> None:
    a = hash_external_id(SecretValue("sel-1"), "telegram", "4242")
    assert a != hash_external_id(SecretValue("sel-2"), "telegram", "4242")
    assert a != hash_external_id(SecretValue("sel-1"), "whatsapp", "4242")
    assert "4242" not in a and len(a) == 64


async def test_client_sends_chunks_with_buttons_on_last() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"path": request.url.path, **json.loads(request.content)})
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = TelegramClient(SecretValue("123:SECRET"), transport=httpx.MockTransport(handler))
    message = OutboundMessage(
        text="a " * 3000, buttons=[[Button(label="Valider", data="v"), Button(label="Annuler")]]
    )
    await client.send("4242", message)
    await client.aclose()
    assert len(calls) == 2
    assert all(str(c["path"]).endswith("/sendMessage") for c in calls)
    assert "reply_markup" not in calls[0]
    assert calls[1]["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "Valider", "callback_data": "v"}, {"text": "Annuler", "callback_data": None}]
        ]
    }


async def test_client_errors_never_leak_the_token() -> None:
    def failing(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom https://api.telegram.org/bot123:SECRET/sendMessage")

    client = TelegramClient(SecretValue("123:SECRET"), transport=httpx.MockTransport(failing))
    with pytest.raises(TelegramError) as excinfo:
        await client.call("sendMessage", chat_id="1", text="x")
    await client.aclose()
    assert "SECRET" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__


async def test_client_api_error() -> None:
    def refused(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked"})

    client = TelegramClient(SecretValue("123:SECRET"), transport=httpx.MockTransport(refused))
    with pytest.raises(TelegramError, match="403 Forbidden"):
        await client.call("sendMessage", chat_id="1", text="x")
    await client.aclose()
