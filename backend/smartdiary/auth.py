import secrets
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from .config import settings
from .db import get_db, utcnow
from .models import User

hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def issue_token(user):
    return jwt.encode(
        {"sub": user.id, "iat": utcnow(), "exp": utcnow() + timedelta(days=7)},
        settings.jwt_secret,
        algorithm="HS256",
    )


def verify_password(encoded, password):
    try:
        return hasher.verify(encoded, password)
    except VerificationError:
        return False


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)):
    try:
        if not credentials:
            raise ValueError()
        claims = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
        user = db.get(User, claims["sub"])
        if not user:
            raise ValueError()
        return user
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "登录已过期，请重新登录") from None


def register(db, credentials):
    if settings.registration_token and not secrets.compare_digest(
        credentials.invitation, settings.registration_token
    ):
        raise HTTPException(403, "邀请码无效")
    if db.scalar(select(User).where(User.username == credentials.username)):
        raise HTTPException(409, "用户名已存在")
    user = User(username=credentials.username, password_hash=hasher.hash(credentials.password))
    db.add(user)
    db.commit()
    return user
