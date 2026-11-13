import json

from starsort.web.api import handler


def call(route_key: str) -> tuple[int, dict]:
    response = handler({"routeKey": route_key}, context=None)
    return response["statusCode"], json.loads(response["body"])


def test_health_returns_ok():
    status, body = call("GET /health")

    assert status == 200
    assert body == {"status": "ok", "service": "starsort"}


def test_unknown_route_returns_404():
    status, body = call("GET /nope")

    assert status == 404
    assert body["route"] == "GET /nope"


def test_responses_are_json():
    response = handler({"routeKey": "GET /health"}, context=None)

    assert response["headers"]["content-type"] == "application/json"
