import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User


def validate_init_data(raw: str, token: str, ttl: int) -> dict:
    try:
        pairs = parse_qsl(raw, strict_parsing=True)
        data = dict(pairs)
        if len(pairs) != len(data):
            raise ValueError("duplicate keys")
        received_hash = data.pop("hash")
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_hash, expected):
            raise ValueError("signature")
        age = time.time() - int(data["auth_date"])
        if age < -30 or age > ttl:
            raise ValueError("expired")
        user = json.loads(data["user"])
        if not isinstance(user.get("id"), int) or user["id"] <= 0:
            raise ValueError("user")
        return user
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, "Откройте приложение заново из Telegram") from None


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    s = get_settings()
    if s.dev_auth and s.environment == "development" and authorization == "Dev local":
        identity = {"id": s.dev_user_id, "first_name": "Локальный пользователь"}
    elif authorization.startswith("tma ") and s.bot_token:
        identity = validate_init_data(authorization[4:], s.bot_token, s.init_data_ttl)
    else:
        raise HTTPException(401, "Откройте приложение из Telegram")
    if not (s.dev_auth and s.environment == "development") and identity["id"] not in s.allowed_ids:
        raise HTTPException(403, "Нет доступа")
    user = db.get(User, identity["id"])
    if not user:
        user = User(id=identity["id"], name=identity.get("first_name", ""))
        db.add(user)
        db.commit()
    return user
