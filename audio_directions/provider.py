"""Small shared HTTP boundary for ElevenLabs only."""
import httpx

from .config import AudioError, Config

BASE_URL = "https://api.elevenlabs.io/v1"


def post(path: str, config: Config, **kwargs) -> httpx.Response:
    """No automatic retries: avoid duplicate synthesis or transcription charges."""
    try:
        response = httpx.post(
            BASE_URL + path, headers={"xi-api-key": config.api_key},
            timeout=config.timeout_seconds, **kwargs,
        )
    except httpx.TimeoutException as exc:
        raise AudioError("ElevenLabs timed out. Check connectivity and try again.") from exc
    except httpx.RequestError as exc:
        raise AudioError("Cannot reach ElevenLabs. Check your internet connection.") from exc
    if response.is_success:
        return response
    # Inspect status without printing response bodies, transcripts, or credentials.
    try:
        detail = response.json().get("detail", {})
        status = str(detail.get("status", "")) if isinstance(detail, dict) else ""
    except (ValueError, AttributeError):
        status = ""
    if "quota" in status.lower() or "credit" in status.lower() or response.status_code == 402:
        raise AudioError("ElevenLabs quota/credits exhausted. Check account usage and billing.")
    if response.status_code in (401, 403):
        raise AudioError("ElevenLabs authentication/access denied. Check the API key, permissions, and voice access.")
    if response.status_code == 429:
        raise AudioError("ElevenLabs rate/concurrency limit or quota reached. Check usage; retry later.")
    raise AudioError(f"ElevenLabs returned HTTP {response.status_code}. Check model ID, voice ID, and request settings.")
