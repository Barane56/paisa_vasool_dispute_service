"""
Dependency injection for the dispute service.

The dispute service validates JWT access tokens issued by the auth service.
Both services share the same SECRET_KEY so no HTTP call to auth service is needed.
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import InvalidTokenError, TokenExpiredError
from src.data.clients.postgres import get_db
from src.data.repositories.repositories import UserRepository
from src.schemas.common_schemas import CurrentUser
from src.utils.jwt import decode_access_token

bearer_scheme = HTTPBearer()


async def get_current_user(
    request: Request,
    # credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    # print("Validating token for incoming request...")  # Debug: log when this function is called  # noqa: E501
    # use the below for testing for swagger
    # token = credentials.credentials
    # print(request.headers)
    token = None
    # for api based connection we need to read the cookie from access token
    # when using swagger please uncomment the HTTPAuthorizationCredentials line
    # if (credentials := await bearer_scheme(request)):
    #     token = credentials.credentials
    # else:
    token = request.cookies.get("access_token")
    # if token is None:
    #     # try to get from authorization headers
    #     token = request.headers.get('access_token').split(' ')[1]

    # print(token)
    # print(f"Received token: {token[:10]}...")  # Debug: log the start of the token
    try:
        payload = decode_access_token(token)  # type: ignore
    except (TokenExpiredError, InvalidTokenError) as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=e.message)  # noqa: B904

    user_id = int(payload["sub"])
    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found. Ensure auth service is seeding the same database.",
        )

    # Resolve role — user.user_roles is lazy joined on the User model
    role_name = "finance_associate"
    if user.user_roles and user.user_roles.role:
        role_name = user.user_roles.role.role_name

    return CurrentUser(
        user_id=user.user_id, name=user.name, email=user.email, role=role_name
    )
