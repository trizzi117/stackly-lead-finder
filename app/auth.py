"""Аутентификация: хеш паролей (pbkdf2, stdlib — без внешних зависимостей)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from sqlalchemy.orm import Session

from .models import Subscription, User

_ITER = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iters, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def create_user(db: Session, email: str, password: str) -> User:
    user = User(email=email.strip().lower(), password_hash=hash_password(password))
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, plan="none", status="inactive"))
    db.commit()
    db.refresh(user)
    return user


def email_taken(db: Session, email: str) -> bool:
    return db.query(User).filter(User.email == email.strip().lower()).first() is not None


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if user and verify_password(password, user.password_hash):
        return user
    return None
