import base64
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

AIPolicy = Literal["external", "trusted", "local"]


class Cipher(BaseModel):
    v: Literal[1] = 1
    iv: str = Field(max_length=32)
    ciphertext: str = Field(max_length=100000)

    @model_validator(mode="after")
    def valid_cipher(self):
        try:
            if len(base64.b64decode(self.iv, validate=True)) != 12:
                raise ValueError()
            if len(base64.b64decode(self.ciphertext, validate=True)) < 16:
                raise ValueError()
        except Exception:
            raise ValueError("Invalid AES-GCM ciphertext") from None
        return self


class VaultHint(BaseModel):
    hint: str = Field(default="", max_length=300)

    @field_validator("hint")
    @classmethod
    def clean_hint(cls, value):
        return value.strip()


class VaultSetup(VaultHint):
    salt: str = Field(max_length=64)
    verifier: Cipher

    @field_validator("salt")
    @classmethod
    def valid_salt(cls, value):
        if len(base64.b64decode(value, validate=True)) != 16:
            raise ValueError("Salt must be 16 bytes")
        return value


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None
    color: str = Field(default="#bba6f8", pattern=r"^#[0-9a-fA-F]{6}$")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Название не может быть пустым")
        return value.strip()


class EntryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=100000)
    kind: Literal["note", "link", "secret"] = "note"
    folder_id: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    favorite: bool = False
    secret: Cipher | None = None
    ai_policy: AIPolicy = "external"

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError("Название не может быть пустым")
        return value.strip()

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, tags):
        if any(len(t) > 40 for t in tags):
            raise ValueError("Тег слишком длинный")
        return list(dict.fromkeys(t.strip().lstrip("#") for t in tags if t.strip().lstrip("#")))

    @model_validator(mode="after")
    def private_secret(self):
        if self.kind == "secret" and (not self.secret or self.body or self.tags):
            raise ValueError("Секрет содержит только зашифрованные данные; текст и теги запрещены")
        if self.kind != "secret" and self.secret:
            raise ValueError("Ciphertext is only valid for secrets")
        if self.kind == "secret":
            self.ai_policy = "local"
        return self


class EntryUpdate(EntryCreate):
    revision: int = Field(ge=1)
    ai_policy: AIPolicy | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    folder_id: str | None = None
    answer: bool = True
    ai_policy: AIPolicy = "external"

    @field_validator("query")
    @classmethod
    def clean_query(cls, value):
        if not value.strip():
            raise ValueError("Введите запрос")
        return value.strip()


class IntakeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100000)
    ai_policy: AIPolicy = "external"
    folder_id: str | None = None
    defer: bool = False


class AssistRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    revision: int = Field(ge=1)


class BotPolicyRequest(BaseModel):
    ai_policy: AIPolicy


class OrganizedNote(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=100000)
    kind: Literal["note", "link"] = "note"
    tags: list[str] = Field(default_factory=list, max_length=20)
    folder_path: list[str] = Field(default_factory=list, max_length=5)
    attachment_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("tags", "folder_path")
    @classmethod
    def bounded_strings(cls, values):
        if any(not value.strip() or len(value) > 40 for value in values):
            raise ValueError("Invalid label")
        return list(dict.fromkeys(value.strip() for value in values))


class OrganizedNotes(BaseModel):
    notes: list[OrganizedNote] = Field(min_length=1, max_length=12)


class Replacement(BaseModel):
    replacement: str = Field(max_length=100000)


class TrustedVaultRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)
    fields: dict[Literal["login", "password", "url", "notes"], str]

    @field_validator("fields")
    @classmethod
    def bounded_fields(cls, fields):
        if set(fields) != {"login", "password", "url", "notes"} or any(len(v) > 10000 for v in fields.values()):
            raise ValueError("Invalid vault fields")
        return fields


class VaultFields(BaseModel):
    login: str = Field(max_length=10000)
    password: str = Field(max_length=10000)
    url: str = Field(max_length=10000)
    notes: str = Field(max_length=10000)
