"""Messages normalisés, indépendants du canal (Telegram aujourd'hui, WhatsApp et le web ensuite)."""

from enum import StrEnum

from pydantic import BaseModel, Field


class InboundKind(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    PHOTO = "photo"
    LOCATION = "location"
    COMMAND = "command"
    CALLBACK = "callback"
    UNSUPPORTED = "unsupported"


class InboundMessage(BaseModel):
    """Message entrant normalisé. ``external_user_id`` n'est ni journalisé, ni stocké en clair."""

    channel: str
    external_user_id: str
    external_chat_id: str
    kind: InboundKind
    text: str | None = None
    command: str | None = None
    command_args: str | None = None
    file_id: str | None = None
    duration_s: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    callback_id: str | None = None
    callback_data: str | None = None
    language_code: str | None = None
    first_name: str | None = None
    # Texte issu de la transcription d'un vocal (des mots peuvent être mal reconnus).
    transcribed: bool = False


class Button(BaseModel):
    label: str = Field(max_length=64)
    # Donnée renvoyée par le bouton (Telegram : 64 octets au maximum).
    data: str | None = Field(default=None, max_length=64)
    url: str | None = None


class Document(BaseModel):
    """Fichier joint (une facture PDF…), envoyé après le texte du message."""

    filename: str = Field(max_length=100)
    content: bytes
    mime: str = "application/pdf"


class OutboundMessage(BaseModel):
    text: str
    buttons: list[list[Button]] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
