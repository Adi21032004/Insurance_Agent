from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from agent.service import app as agent_app
from api_rules import assert_api_rules
from backend.main import app


def test_backend_and_agent_service_api_rules():
    assert_api_rules(app)
    assert_api_rules(agent_app)


def test_api_rules_reject_an_optional_nested_request_field():
    class NestedInput(BaseModel):
        required_value: str
        optional_value: str | None = None

    class RequestInput(BaseModel):
        lines: list[NestedInput]

    test_app = FastAPI()

    @test_app.post("/request")
    def request(value: RequestInput) -> dict[str, Any]:
        """Accept one test request."""
        return value.model_dump()

    with pytest.raises(AssertionError, match="Optional request field"):
        assert_api_rules(test_app)
