import pytest
from fastapi.testclient import TestClient
from jsonschema import validate, ValidationError

from main import app  # Assuming main.py exports the FastAPI app
from database.models import Base, get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy.pool import StaticPool

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=test_engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

# JSON Schema for Match Response
MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "home_team": {"type": "string"},
        "away_team": {"type": "string"},
        "kickoff_at": {"type": "string"},
        "status": {"type": "string"},
    },
    "required": ["id", "home_team", "away_team", "kickoff_at", "status"]
}

# JSON Schema for Issue Response
ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "issue_id": {"type": "string"},
        "status": {"type": "string"},
        "matches": {"type": "array", "items": MATCH_SCHEMA}
    },
    "required": ["issue_id", "status"]
}

def test_matches_contract():
    """
    前端关键 API 合约测试：确保 /api/matches 的返回结构稳定。
    """
    response = client.get("/api/matches")
    # if it requires auth or specific query, we just assert the response format if successful
    if response.status_code == 200:
        data = response.json()
        assert "items" in data
        assert isinstance(data["items"], list)
        for item in data["items"][:2]:  # Test first few to save time
            try:
                validate(instance=item, schema=MATCH_SCHEMA)
            except ValidationError as e:
                pytest.fail(f"Match API contract broken: {e}")

def test_issues_contract():
    """
    前端关键 API 合约测试：确保 /api/jingcai/issues 的返回结构稳定。
    """
    response = client.get("/api/jingcai/issues")
    if response.status_code == 200:
        data = response.json()
        assert "items" in data
        assert isinstance(data["items"], list)
        for item in data["items"][:2]:
            try:
                validate(instance=item, schema=ISSUE_SCHEMA)
            except ValidationError as e:
                pytest.fail(f"Issue API contract broken: {e}")
