"""OpenAI file transcription with bounded retry behavior."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable


DEFAULT_MODEL = "gpt-4o-transcribe"
SUPPORTED_MODELS = (DEFAULT_MODEL, "gpt-4o-mini-transcribe", "whisper-1")


def transcribe_audio(
    path: Path,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    prompt: str | None = None,
    attempts: int = 4,
    progress: Callable[[str], None] = lambda message: None,
) -> str:
    if model not in SUPPORTED_MODELS:
        raise ValueError(f"Unsupported transcription model: {model}")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI transcription requires the 'openai' package. Run the HistoricalContent launcher."
        ) from exc

    client = OpenAI(api_key=api_key)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with path.open("rb") as audio_file:
                request = {
                    "model": model,
                    "file": audio_file,
                    "response_format": "json",
                    "language": "en",
                }
                if prompt:
                    request["prompt"] = prompt
                response = client.audio.transcriptions.create(**request)
            text = response.get("text", "") if isinstance(response, dict) else response.text
            return str(text or "").strip()
        except Exception as exc:  # SDK exposes several provider-specific subclasses.
            last_error = exc
            status_code = getattr(exc, "status_code", None)
            if status_code in {400, 401, 403, 404, 413, 422}:
                break
            if attempt >= attempts:
                break
            delay = min(2 ** (attempt - 1), 8)
            progress(f"Transcription attempt {attempt} failed for {path.name}; retrying in {delay}s.")
            time.sleep(delay)
    raise RuntimeError(f"Transcription failed for {path.name}: {last_error}")
