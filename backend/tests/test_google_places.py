"""Google Places proxy contracts for registration address autocomplete."""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import google_places  # noqa: E402
import server  # noqa: E402


LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def test_autocomplete_is_hospital_only_and_uses_minimal_fields(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "server-only-secret")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse({"suggestions": [{"placePrediction": {
            "placeId": "ChIJhospital123",
            "text": {"text": "Apollo Hospitals, Chennai, Tamil Nadu"},
            "structuredFormat": {"mainText": {"text": "Apollo Hospitals"}},
        }}]})

    monkeypatch.setattr(google_places.requests, "post", fake_post)
    result = google_places.autocomplete_hospitals("  TX ", "session_token-1")

    assert result == [{
        "place_id": "ChIJhospital123",
        "name": "Apollo Hospitals",
        "description": "Apollo Hospitals, Chennai, Tamil Nadu",
    }]
    assert captured["json"]["input"] == "TX"
    assert captured["json"]["includedPrimaryTypes"] == ["hospital"]
    assert captured["json"]["regionCode"] == "in"
    assert captured["json"]["includedRegionCodes"] == ["in"]
    assert captured["json"]["sessionToken"] == "session_token-1"
    assert "displayName" not in captured["headers"]["X-Goog-FieldMask"]
    assert captured["headers"]["X-Goog-Api-Key"] == "server-only-secret"


def test_organization_autocomplete_allows_company_place_types(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "server-only-secret")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse({"suggestions": [{"placePrediction": {
            "placeId": "ChIJsponsorcompany123",
            "text": {"text": "Pfizer Limited, Mumbai, Maharashtra"},
            "structuredFormat": {"mainText": {"text": "Pfizer Limited"}},
        }}]})

    monkeypatch.setattr(google_places.requests, "post", fake_post)
    result = google_places.autocomplete_organizations(
        "Pfizer", "organization-session-1")

    assert result[0]["name"] == "Pfizer Limited"
    assert captured["json"]["input"] == "Pfizer"
    assert "includedPrimaryTypes" not in captured["json"]
    assert captured["json"]["includeQueryPredictions"] is False


def test_details_requests_essentials_address_only(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "server-only-secret")
    captured = {}

    def fake_get(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse({
            "id": "ChIJhospital123",
            "formattedAddress": "21 Greams Lane, Chennai, Tamil Nadu, India",
        })

    monkeypatch.setattr(google_places.requests, "get", fake_get)
    result = google_places.place_address("ChIJhospital123", "session_token-1")

    assert result["address"].startswith("21 Greams Lane")
    assert captured["params"] == {"sessionToken": "session_token-1"}
    assert captured["headers"]["X-Goog-FieldMask"] == "id,formattedAddress"
    assert "server-only-secret" not in str(result)


def test_public_proxy_returns_safe_shape_without_key(monkeypatch):
    async def no_limit(_request):
        return None

    monkeypatch.setattr(server, "_enforce_places_rate_limit", no_limit)
    monkeypatch.setattr(
        server.google_places,
        "autocomplete_hospitals",
        lambda query, token: [{
            "place_id": "ChIJhospital123",
            "name": "Apollo Hospitals",
            "description": "Apollo Hospitals, Chennai",
        }],
    )

    async def flow():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://testserver",
        ) as client:
            return await client.get(
                "/api/public/places/hospitals/autocomplete",
                params={"input": "Apollo", "session_token": "session_token-1"},
            )

    response = run(flow())
    assert response.status_code == 200, response.text
    assert response.json()["predictions"][0]["name"] == "Apollo Hospitals"
    assert "key" not in response.text.lower()


def test_public_organization_proxy_returns_company_predictions(monkeypatch):
    async def no_limit(_request):
        return None

    monkeypatch.setattr(server, "_enforce_places_rate_limit", no_limit)
    monkeypatch.setattr(
        server.google_places,
        "autocomplete_organizations",
        lambda query, token: [{
            "place_id": "ChIJsponsorcompany123",
            "name": "Pfizer Limited",
            "description": "Pfizer Limited, Mumbai",
        }],
    )

    async def flow():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://testserver",
        ) as client:
            return await client.get(
                "/api/public/places/organizations/autocomplete",
                params={"input": "Pfizer", "session_token": "organization-session-1"},
            )

    response = run(flow())
    assert response.status_code == 200, response.text
    assert response.json()["predictions"][0]["name"] == "Pfizer Limited"
    assert "key" not in response.text.lower()


def test_invalid_session_token_is_rejected(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "server-only-secret")
    with pytest.raises(ValueError, match="session token"):
        google_places.autocomplete_hospitals("Apollo", "token with spaces")


def test_public_proxy_distinguishes_no_matches_from_provider_failure(monkeypatch):
    async def no_limit(_request):
        return None

    monkeypatch.setattr(server, "_enforce_places_rate_limit", no_limit)

    async def flow():
        monkeypatch.setattr(
            server.google_places, "autocomplete_hospitals", lambda *_args: [])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://testserver",
        ) as client:
            empty = await client.get(
                "/api/public/places/hospitals/autocomplete",
                params={"input": "ZZ", "session_token": "empty-session-1"},
            )

            def unavailable(*_args):
                raise google_places.PlacesUpstreamError("provider unavailable")

            monkeypatch.setattr(
                server.google_places, "autocomplete_hospitals", unavailable)
            failed = await client.get(
                "/api/public/places/hospitals/autocomplete",
                params={"input": "TX", "session_token": "failed-session-1"},
            )
        return empty, failed

    empty, failed = run(flow())
    assert empty.status_code == 200
    assert empty.json() == {"predictions": []}
    assert failed.status_code == 502
    assert failed.json() == {
        "detail": "Hospital address search is temporarily unavailable"
    }
