from __future__ import annotations

import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Generator

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(str(Path(__file__).resolve().parents[2] / '.env'), '.env'), extra='ignore')
    database_url: str = 'sqlite:///./radar.db'
    admin_username: str = 'admin'
    admin_password: str = ''
    secret_key: str = ''
    secure_cookies: bool = False
    brave_api_key: str = ''
    anthropic_api_key: str = ''
    anthropic_model: str = 'claude-haiku-4-5-20251001'
    max_search_requests: int = 9
    max_search_results: int = 20
    allowed_origin: str = ''


settings = Settings()
if os.getenv('PYTEST_CURRENT_TEST') is None and (not settings.admin_password or len(settings.secret_key) < 32):
    # Fail closed; never start publicly accessible app without credentials.
    raise RuntimeError('Configure ADMIN_PASSWORD e SECRET_KEY (mínimo 32 caracteres) no .env')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger('radar')

connect_args = {'check_same_thread': False} if settings.database_url.startswith('sqlite') else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as db:
        yield db


serializer = URLSafeTimedSerializer(settings.secret_key or 'testing-session-secret-only', salt='radar-v1')


def auth_session(request: Request) -> dict:
    raw = request.cookies.get('radar_session')
    if not raw:
        raise HTTPException(401, 'Sessão não autenticada')
    try:
        data = serializer.loads(raw, max_age=60 * 60 * 24 * 7)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, 'Sessão expirada') from None
    if data.get('user') != settings.admin_username:
        raise HTTPException(401, 'Sessão inválida')
    return data


def require_csrf(request: Request, session: dict = Depends(auth_session)) -> dict:
    if request.headers.get('X-Radar-CSRF') != session.get('csrf'):
        raise HTTPException(403, 'Token CSRF inválido')
    origin = request.headers.get('origin')
    if settings.allowed_origin and origin and origin.rstrip('/') != settings.allowed_origin.rstrip('/'):
        raise HTTPException(403, 'Origem inválida')
    return session
