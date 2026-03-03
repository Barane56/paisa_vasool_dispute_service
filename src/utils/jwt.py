"""
JWT utilities for the dispute service.

The dispute service validates tokens issued by the auth service.
Both services share the same SECRET_KEY so no inter-service call is needed.
"""

import jwt
from src.config.settings import settings
from src.core.exceptions import TokenExpiredError, InvalidTokenError


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "access":
            raise InvalidTokenError()
        return payload
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError()
    except jwt.InvalidTokenError:
        raise InvalidTokenError()
