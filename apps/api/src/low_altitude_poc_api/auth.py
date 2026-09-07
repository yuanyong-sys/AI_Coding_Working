from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine, Integer, String, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

SESSION_COOKIE = "low_altitude_session"
SESSION_LIFETIME = timedelta(hours=8)
logger = logging.getLogger(__name__)


class AuthBase(DeclarativeBase):
    pass


class Role(StrEnum):
    PLATFORM_OPERATOR = "platform_operator"


ROLE_LABELS = {
    Role.PLATFORM_OPERATOR: "平台操作员",
}

ROLE_CAPABILITIES = {
    Role.PLATFORM_OPERATOR: [
        "situation:read",
        "query:read",
        "clue:review",
        "spatial:manage",
    ],
}

DEMO_ACCOUNTS = {
    "platform-operator": Role.PLATFORM_OPERATOR,
}


class UserAccount(AuthBase):
    __tablename__ = "user_accounts"

    username: Mapped[str] = mapped_column(String, primary_key=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)


class AuthSession(AuthBase):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    expires_at: Mapped[str] = mapped_column(String)


class AuditEvent(AuthBase):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String)
    outcome: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthenticatedUser(BaseModel):
    username: str
    role: Role
    role_label: str
    capabilities: list[str]


class AuditEventResponse(BaseModel):
    id: int
    actor: str
    action: str
    outcome: str
    created_at: datetime
    subject: str | None


class AuditEventList(BaseModel):
    events: list[AuditEventResponse]


@dataclass(frozen=True)
class AuthService:
    require_user: Callable[..., AuthenticatedUser]
    record_audit: Callable[..., None]


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    algorithm, salt_hex, expected_hex = encoded.split("$", maxsplit=2)
    if algorithm != "scrypt":
        return False
    actual = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1
    )
    return hmac.compare_digest(actual.hex(), expected_hex)


def public_user(account: UserAccount) -> AuthenticatedUser:
    role = Role(account.role)
    return AuthenticatedUser(
        username=account.username,
        role=role,
        role_label=ROLE_LABELS[role],
        capabilities=ROLE_CAPABILITIES[role],
    )


def configure_auth(
    app: FastAPI, engine: Engine, demo_password: str | None
) -> AuthService:
    AuthBase.metadata.create_all(engine)
    with engine.begin() as connection:
        audit_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(audit_events)"))
        }
        if "subject" not in audit_columns:
            connection.execute(text("ALTER TABLE audit_events ADD COLUMN subject TEXT"))
    configured_password = demo_password or os.getenv("LOW_ALTITUDE_DEMO_PASSWORD")
    if configured_password is None:
        configured_password = secrets.token_urlsafe(15)
        logger.warning(
            "Generated local demo password for all local accounts: %s",
            configured_password,
        )

    with Session(engine) as database:
        for username, role in DEMO_ACCOUNTS.items():
            account = database.get(UserAccount, username)
            if account is None:
                database.add(
                    UserAccount(
                        username=username,
                        password_hash=password_hash(configured_password),
                        role=role.value,
                    )
                )
            else:
                account.password_hash = password_hash(configured_password)
                account.role = role.value
        database.commit()

    def record_audit(
        actor: str,
        action: str,
        outcome: str,
        *,
        subject: str | None = None,
        database: Session | None = None,
    ) -> None:
        event = AuditEvent(
            actor=actor,
            action=action,
            outcome=outcome,
            created_at=datetime.now(UTC).isoformat(),
            subject=subject,
        )
        if database is not None:
            database.add(event)
            return
        with Session(engine) as own_database:
            own_database.add(event)
            own_database.commit()

    def require_user(
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> AuthenticatedUser:
        if session_token is None:
            record_audit("anonymous", "access_denied", "denied")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        with Session(engine) as database:
            auth_session = database.get(
                AuthSession, hashlib.sha256(session_token.encode()).hexdigest()
            )
            if auth_session is None or datetime.fromisoformat(
                auth_session.expires_at
            ) <= datetime.now(UTC):
                record_audit("anonymous", "access_denied", "denied")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
            account = database.get(UserAccount, auth_session.username)
            if account is None or account.username not in DEMO_ACCOUNTS:
                record_audit(auth_session.username, "access_denied", "denied")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
            return public_user(account)

    @app.post("/api/auth/login", response_model=AuthenticatedUser)
    def login(credentials: LoginRequest, response: Response) -> AuthenticatedUser:
        with Session(engine) as database:
            account = database.get(UserAccount, credentials.username)
            if (
                account is None
                or account.username not in DEMO_ACCOUNTS
                or not password_matches(credentials.password, account.password_hash)
            ):
                record_audit(credentials.username, "login_failed", "denied")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
            token = secrets.token_urlsafe(32)
            database.add(
                AuthSession(
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    username=account.username,
                    expires_at=(datetime.now(UTC) + SESSION_LIFETIME).isoformat(),
                )
            )
            database.commit()
            record_audit(account.username, "login_succeeded", "allowed")
            response.set_cookie(
                SESSION_COOKIE,
                token,
                httponly=True,
                samesite="strict",
                max_age=int(SESSION_LIFETIME.total_seconds()),
                path="/",
            )
            return public_user(account)

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(
        response: Response,
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> None:
        if session_token:
            with Session(engine) as database:
                auth_session = database.get(
                    AuthSession, hashlib.sha256(session_token.encode()).hexdigest()
                )
                if auth_session:
                    database.delete(auth_session)
                    database.commit()
        response.delete_cookie(SESSION_COOKIE, path="/")

    @app.get("/api/auth/session", response_model=AuthenticatedUser)
    def current_session(
        user: AuthenticatedUser = Depends(require_user),  # noqa: B008
    ) -> AuthenticatedUser:
        return user

    @app.get("/api/audit/events", response_model=AuditEventList)
    def audit_events(
        actor: str | None = None,
        user: AuthenticatedUser = Depends(require_user),  # noqa: B008
    ) -> AuditEventList:
        if actor is not None and actor != user.username:
            record_audit(user.username, "access_denied", "denied")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        with Session(engine) as database:
            events = database.scalars(
                select(AuditEvent)
                .where(AuditEvent.actor == user.username)
                .order_by(AuditEvent.id)
            ).all()
            return AuditEventList(
                events=[
                    AuditEventResponse(
                        id=event.id,
                        actor=event.actor,
                        action=event.action,
                        outcome=event.outcome,
                        created_at=datetime.fromisoformat(event.created_at),
                        subject=event.subject,
                    )
                    for event in events
                ]
            )

    return AuthService(require_user=require_user, record_audit=record_audit)
