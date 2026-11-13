import json
from typing import Any


def json_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for the HTTP API (payload format 2.0)."""
    route = event.get("routeKey", "")
    if route == "GET /health":
        return json_response(200, {"status": "ok", "service": "starsort"})
    return json_response(404, {"error": "not found", "route": route})
