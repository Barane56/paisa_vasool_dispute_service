# user_models.py — Role, User, UserRole, RefreshToken
# Auth-owned tables; read-only from dispute service perspective.
from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    TIMESTAMP, ForeignKey, Index, UniqueConstraint, func, text,
)
from sqlalchemy.orm import relationship
from .base import Base


class Role(Base):
    __tablename__ = "roles"

    role_id   = Column(Integer, primary_key=True)
    role_name = Column(String(50), unique=True, nullable=False)

    user_roles = relationship("UserRole", back_populates="role", lazy="select")

    def __repr__(self) -> str:
        return f"<Role id={self.role_id} name={self.role_name}>"


class User(Base):
    __tablename__ = "users"

    user_id       = Column(Integer, primary_key=True, nullable=False)
    name          = Column(String(100), nullable=False)
    email         = Column(String(150), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    user_roles     = relationship("UserRole",             back_populates="user",      uselist=False, lazy="joined", cascade="all, delete-orphan")
    assignments    = relationship("DisputeAssignment",    back_populates="assignee",  lazy="select")
    activity_logs  = relationship("DisputeActivityLog",   back_populates="performer", lazy="select")
    status_history = relationship("DisputeStatusHistory", back_populates="performer", lazy="select")
    refresh_tokens = relationship("RefreshToken",         back_populates="user",      lazy="select", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_users_email", "email"),)

    def __repr__(self) -> str:
        return f"<User id={self.user_id} email={self.email}>"


class UserRole(Base):
    __tablename__ = "user_roles"

    user_role_id = Column(Integer, primary_key=True)
    user_id      = Column(Integer, ForeignKey("users.user_id",  ondelete="CASCADE"),  nullable=False)
    role_id      = Column(Integer, ForeignKey("roles.role_id",  ondelete="RESTRICT"), nullable=False)
    assigned_at  = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="user_roles")
    role = relationship("Role", back_populates="user_roles", lazy="joined")

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_roles_user_id"),
        Index("ix_user_roles_user_id", "user_id"),
        Index("ix_user_roles_role_id", "role_id"),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_token"

    token_id      = Column(Integer, primary_key=True)
    user_id       = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    jti           = Column(Text, unique=True, nullable=False)
    refresh_token = Column(Text, unique=True, nullable=False)
    is_revoked    = Column(Boolean, nullable=False, default=False, server_default=text("FALSE"))
    expires_at    = Column(TIMESTAMP(timezone=True), nullable=False)
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="refresh_tokens")

    __table_args__ = (
        Index("ix_refresh_token_token",      "refresh_token"),
        Index("ix_refresh_token_jti",        "jti"),
        Index("ix_refresh_token_user_id",    "user_id"),
        Index("ix_refresh_token_expires_at", "expires_at"),
    )
