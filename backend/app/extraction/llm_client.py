"""Transport for the canonical extraction pipeline's model calls.

Deliberately thin. It knows how to send a prompt and get JSON back; it knows
nothing about schedules. That separation is what lets the whole extraction
pipeline be tested without a network call, by handing it a scripted client.

The pipeline asks the model one focused question per semantic category rather
than one enormous question about the whole protocol. That costs more calls but
each answer is checkable against a small contract, and a model that fails to
answer one category degrades that category only - it does not corrupt the rest.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Protocol

log = logging.getLogger(__name__)

#: Matches the Claude model the rest of the product already uses.
DEFAULT_MODEL = "claude-opus-5"

#: Generous, because a schedule-of-assessments answer for one category on a large
#: protocol can be long. Truncated JSON is unrecoverable, so this is not a place
#: to economise.
DEFAULT_MAX_TOKENS = 16000


class ExtractionModelError(RuntimeError):
    """The model could not be reached or did not answer usefully."""


class ExtractionNotConfigured(ExtractionModelError):
    """No API key: the caller should surface this rather than silently degrade."""


class LLMClient(Protocol):
    """Anything that can answer a prompt with text.

    Implementations must not retry silently forever, and must raise
    ExtractionModelError rather than returning a plausible-looking empty answer:
    an empty answer is indistinguishable from "the protocol does not say this",
    and those two must never be confused.
    """

    def complete(self, *, system: str, prompt: str, max_tokens: int = ...) -> str: ...


class AnthropicClient:
    """Claude, via the Anthropic Messages API.

    The document text is sent as a cached prefix. A full extraction asks about
    eighteen semantic categories against the same protocol, so caching the
    document makes every call after the first far cheaper.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        document_text: str = "",
    ):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._model = model or os.getenv("UCTSM_EXTRACTION_MODEL") or DEFAULT_MODEL
        self._document_text = document_text

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _client(self):
        if not self._api_key:
            raise ExtractionNotConfigured(
                "ANTHROPIC_API_KEY is not set; extraction cannot run"
            )
        import anthropic  # lazy so the app boots without the dependency or key

        return anthropic.Anthropic(api_key=self._api_key)

    def complete(
        self, *, system: str, prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        client = self._client()
        content: list[dict[str, Any]] = []
        if self._document_text:
            content.append({
                "type": "text",
                "text": f"<protocol_document>\n{self._document_text}\n</protocol_document>",
                "cache_control": {"type": "ephemeral"},
            })
        content.append({"type": "text", "text": prompt})
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as error:  # noqa: BLE001 - re-raised as our own type
            raise ExtractionModelError(f"model request failed: {error}") from error
        return "".join(
            getattr(block, "text", "")
            for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", "") == "text"
        )


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse_json_object(text: str) -> dict[str, Any]:
    """Read one JSON object out of a model reply.

    Tolerates a code fence and a stray preface, because those are formatting
    noise rather than a difference of meaning. Anything else raises: a reply we
    cannot parse must become a visible extraction issue, never an empty result
    that reads as "the protocol does not mention this".
    """
    cleaned = _FENCE.sub("", (text or "").strip())
    if not cleaned:
        raise ExtractionModelError("model returned an empty response")
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        first, last = cleaned.find("{"), cleaned.rfind("}")
        if first < 0 or last <= first:
            raise ExtractionModelError(
                "model response was not JSON"
            ) from None
        try:
            value = json.loads(cleaned[first:last + 1])
        except json.JSONDecodeError as error:
            raise ExtractionModelError(f"model response was not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ExtractionModelError("model response was not a JSON object")
    return value


def parse_json_list(text: str, key: str) -> list[dict[str, Any]]:
    """Read ``{key: [...]}`` from a model reply, tolerating a bare list."""
    cleaned = _FENCE.sub("", (text or "").strip())
    if cleaned.startswith("["):
        try:
            items = json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise ExtractionModelError(f"model response was not valid JSON: {error}") from error
    else:
        items = parse_json_object(text).get(key, [])
    if not isinstance(items, list):
        raise ExtractionModelError(f"expected a list under {key!r}")
    return [item for item in items if isinstance(item, dict)]
