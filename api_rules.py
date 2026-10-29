from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI


def _schema(openapi: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    reference = value.get("$ref")
    if reference is not None:
        name = reference.rsplit("/", maxsplit=1)[-1]
        return openapi["components"]["schemas"][name]
    return value


def _assert_required_fields(openapi: dict[str, Any], value: dict[str, Any]) -> None:
    schema = _schema(openapi, value)
    required = set(schema.get("required", []))
    properties = schema.get("properties", {})
    assert required >= set(properties), f"Optional request field in {schema}"
    for key in ("allOf", "anyOf", "oneOf"):
        for child in schema.get(key, []):
            _assert_required_fields(openapi, child)
    for child in properties.values():
        _assert_required_fields(openapi, child)
    items = schema.get("items")
    if isinstance(items, dict):
        _assert_required_fields(openapi, items)


def assert_api_rules(app: FastAPI) -> None:
    openapi = app.openapi()
    for path_item in openapi["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, Mapping):
                continue
            assert operation.get("operationId")
            assert operation.get("summary")
            for parameter in operation.get("parameters", []):
                assert parameter["required"]
            body = operation.get("requestBody")
            if body is None:
                continue
            for media in body["content"].values():
                _assert_required_fields(openapi, media["schema"])
