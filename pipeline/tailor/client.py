"""LLM client wrapper with retry-on-ValidationError."""

import json
import re
import sys

from pydantic import ValidationError

from pipeline.tailor.models import TailorResponse
from pipeline.llm_client import LLMClient
from pipeline.config import LLM_MODEL


def _extract_json(text: str) -> str:
    """Extract JSON object from LLM response (handles markdown fences)."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return text[s:e + 1].strip()
    return text.strip()


async def call_llm(system: str, user: str) -> dict:
    """One-shot LLM call -> Pydantic validation. Retries once on failure
    with the validation error fed back into the prompt for correction."""
    client = LLMClient()
    _last_error = ""
    try:
        for attempt in (1, 2):
            temp = 0.2 if attempt == 1 else 0.1
            prompt = user if attempt == 1 else (
                f"{user}\n\n-- CORRECTION --\nYour previous response failed "
                f"validation: {_last_error}\nFix the JSON structure and retry."
            )
            print(f"[INFO] Calling {LLM_MODEL} (attempt {attempt}, T={temp})...",
                  file=sys.stderr)
            raw = await client.complete(
                prompt=prompt, system=system,
                temperature=temp, max_tokens=8000, json_mode=True,
            )
            print(f"[INFO] Response: {len(raw)} chars", file=sys.stderr)
            try:
                data = json.loads(_extract_json(raw))
                return TailorResponse.model_validate(data).model_dump()
            except (json.JSONDecodeError, ValidationError) as exc:
                _last_error = str(exc)
                if attempt == 2:
                    print(f"[ERROR] Validation failed after retry: {exc}",
                          file=sys.stderr)
                    print(f"[ERROR] Raw response (first 2000 chars):\n{raw[:2000]}",
                          file=sys.stderr)
                    sys.exit(1)
                print(f"[WARN] Validation failed ({exc}), retrying...",
                      file=sys.stderr)
    finally:
        await client.close()
