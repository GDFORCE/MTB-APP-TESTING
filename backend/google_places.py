"""Small, billing-conscious client for Google Places API (New).

The browser/mobile bundle never receives the API key. Registration needs
hospital predictions for site fields and broader place predictions for sponsor
organizations. Response field masks deliberately exclude Pro fields such as
``displayName``.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List
from urllib.parse import quote

import requests


AUTOCOMPLETE_URL = "https://places.googleapis.com/v1/places:autocomplete"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
SESSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")
# Place IDs are opaque. Reject URL delimiters and whitespace while allowing the
# provider to evolve the identifier alphabet without breaking valid selections.
PLACE_ID_RE = re.compile(r"^[^/?#\s]{5,255}$")


class PlacesNotConfigured(RuntimeError):
    pass


class PlacesUpstreamError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        raise PlacesNotConfigured("Google Places is not configured")
    return key


def _headers(field_mask: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": _api_key(),
        "X-Goog-FieldMask": field_mask,
    }


def _json_response(response: requests.Response) -> Dict[str, Any]:
    if response.status_code >= 400:
        raise PlacesUpstreamError(f"Google Places returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise PlacesUpstreamError("Google Places returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise PlacesUpstreamError("Google Places returned an invalid response")
    return payload


def _autocomplete_places(
    input_text: str,
    session_token: str,
    included_primary_types: List[str] | None = None,
) -> List[dict]:
    query = " ".join(input_text.strip().split())
    # Two letters support short hospital brands such as "TX". A single letter
    # is too broad and would create noisy, avoidable paid requests.
    if len(query) < 2:
        return []
    if not SESSION_TOKEN_RE.fullmatch(session_token):
        raise ValueError("Invalid Places session token")

    body: Dict[str, Any] = {
        "input": query,
        "sessionToken": session_token,
        "includeQueryPredictions": False,
        "languageCode": os.environ.get("GOOGLE_PLACES_LANGUAGE", "en").strip() or "en",
    }
    if included_primary_types:
        body["includedPrimaryTypes"] = included_primary_types
    region = os.environ.get("GOOGLE_PLACES_REGION", "in").strip().lower()
    if region:
        body["regionCode"] = region
        body["includedRegionCodes"] = [region]

    try:
        response = requests.post(
            AUTOCOMPLETE_URL,
            json=body,
            headers=_headers(
                "suggestions.placePrediction.placeId,"
                "suggestions.placePrediction.text.text,"
                "suggestions.placePrediction.structuredFormat.mainText.text"
            ),
            timeout=5,
        )
    except requests.RequestException as exc:
        raise PlacesUpstreamError("Google Places is temporarily unreachable") from exc

    results: List[dict] = []
    # Autocomplete (New) returns at most five predictions. Preserve every
    # prediction it supplies instead of imposing another application-side cap.
    for suggestion in _json_response(response).get("suggestions", []):
        prediction = suggestion.get("placePrediction") or {}
        place_id = str(prediction.get("placeId") or "")
        description = str(((prediction.get("text") or {}).get("text")) or "").strip()
        name = str(
            (((prediction.get("structuredFormat") or {}).get("mainText") or {}).get("text"))
            or description
        ).strip()
        if place_id and name:
            results.append({
                "place_id": place_id,
                "name": name,
                "description": description or name,
            })
    return results


def autocomplete_hospitals(input_text: str, session_token: str) -> List[dict]:
    return _autocomplete_places(input_text, session_token, ["hospital"])


def autocomplete_organizations(input_text: str, session_token: str) -> List[dict]:
    # Google returns all place types when includedPrimaryTypes is omitted. This
    # is intentional for sponsors/CROs because company offices have several
    # different primary types in Places and no single reliable company type.
    return _autocomplete_places(input_text, session_token)


def place_address(place_id: str, session_token: str) -> dict:
    if not PLACE_ID_RE.fullmatch(place_id):
        raise ValueError("Invalid Google Place ID")
    if not SESSION_TOKEN_RE.fullmatch(session_token):
        raise ValueError("Invalid Places session token")
    try:
        response = requests.get(
            DETAILS_URL.format(place_id=quote(place_id, safe="")),
            params={"sessionToken": session_token},
            headers=_headers("id,formattedAddress"),
            timeout=5,
        )
    except requests.RequestException as exc:
        raise PlacesUpstreamError("Google Places is temporarily unreachable") from exc
    payload = _json_response(response)
    address = str(payload.get("formattedAddress") or "").strip()
    if not address:
        raise PlacesUpstreamError("Google Places did not return an address")
    return {
        "place_id": str(payload.get("id") or place_id),
        "address": address,
    }
