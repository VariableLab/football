import sys, os
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_root, d))

"""Pytest fixtures for WC Analytics backend tests."""
import os
import pytest
from fastapi.testclient import TestClient

# Ensure test environment
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-at-least-32-chars!!")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key-for-pytest")


@pytest.fixture(scope="session")
def client():
    """FastAPI test client — created once per session."""
    from main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_token(client):
    """Register + login, return Bearer token. Falls back to login if rate-limited."""
    import uuid
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    password = "testpass123"
    # Try register first
    resp = client.post("/api/auth/register", json={"email": email, "password": password})
    if resp.status_code == 200:
        token = resp.json().get("access_token")
        if token:
            return token
    # Fallback: login with a known test user
    resp = client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpass123"},
    )
    if resp.status_code == 200:
        return resp.json().get("access_token")
    # Last resort: create via direct DB
    from models import SessionLocal, User
    from auth import get_password_hash, create_access_token
    session = SessionLocal()
    try:
        u = session.query(User).filter(User.email == "test_fixed@example.com").first()
        if not u:
            u = User(email="test_fixed@example.com", password_hash=get_password_hash("testpass123"))
            session.add(u)
            session.commit()
            session.refresh(u)
        token = create_access_token(data={"sub": u.email})
        return token
    finally:
        session.close()


@pytest.fixture
def auth_headers(auth_token):
    """Authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {auth_token}"}
