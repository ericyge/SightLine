"""Change only extract_steps when the upstream JSON schema is agreed."""
import json
from pathlib import Path


def extract_steps(payload: dict | str | Path) -> list[str]:
    """Accept {steps: [{text: str}, ...]} or a UTF-8 JSON file path.

    Extra fields are ignored. Validate all steps before any paid API request.
    Strings are paths, not serialized JSON.
    """
    if isinstance(payload, (str, Path)):
        payload = json.loads(Path(payload).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
        raise ValueError('Expected JSON object with "steps": [{"text": "..."}].')
    if not payload["steps"]:
        raise ValueError("steps must contain at least one direction.")
    texts = []
    for i, step in enumerate(payload["steps"], 1):
        text = step.get("text") if isinstance(step, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Step {i} must have a nonempty text string.")
        texts.append(text.strip())
    return texts
