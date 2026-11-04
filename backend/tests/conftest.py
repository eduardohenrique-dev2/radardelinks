import os
import tempfile
from pathlib import Path

os.environ['ADMIN_USERNAME'] = 'admin'
os.environ['ADMIN_PASSWORD'] = 'test-password-strong'
os.environ['SECRET_KEY'] = 'test-session-secret-that-is-long-enough-1234567'
os.environ['RADAR_DISABLE_WORKER'] = '1'
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(tempfile.gettempdir()) / 'radar_api_test.db')

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from app.main import app
from app.core import Base, engine


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def authed(client):
    res = client.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-strong'})
    assert res.status_code == 200
    return client, {'X-Radar-CSRF': res.json()['csrf']}
