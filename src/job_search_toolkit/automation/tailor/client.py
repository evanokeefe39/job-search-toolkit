"""LLM structured-output client: pydantic-ai primary, json_mode fallback.

Primary path: pydantic-ai Agent with output_type=TailorResponse — schema
generation, Pydantic validation, and automatic retry on validation failure
(validation errors are fed back to the model). Uses OpenAIChatModel against
the configured OpenAI-compatible endpoint (DeepSeek).

Fallback path: the original json_mode client (response_format=json_object +
manual Pydantic validation + one retry). Kept because AGENTS.md documents
deepseek's function-call JSON as a known failure class, and pydantic-ai's
structured output uses tool-calling — the smoke test passed (2026-08-10),
but the json_mode path is the proven one if pydantic-ai regresses.

Switch via config.yaml `llm_client:` or env LLM_CLIENT=pydantic_ai|json_mode.
"""

import json
import re
import sys

from pydantic import ValidationError

from job_search_toolkit.automation.tailor.models import TailorResponse


# ---------------------------------------------------------------------------
# pydantic-ai path
# ---------------------------------------------------------------------------

def _make_pai_agent(
    system: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    max_tokens: int,
    max_highlights: int,
):
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    model = OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
    )
    agent = Agent(
        model,
        output_type=TailorResponse,
        system_prompt=system,
        model_settings={
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
    )
    # TailorResponse.max_highlights is enforced by the caller's trim, not the
    # schema — keep the schema permissive and cap in merge_content.
    return agent


async def _call_pydantic_ai(
    system: str,
    user: str,
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float = 0.2,
    max_tokens: int = 8000,
    max_highlights: int = 5,
) -> dict:
    """One pydantic-ai run; structured output validated to TailorResponse."""
    agent = _make_pai_agent(
        system, model_name, base_url, api_key, temperature, max_tokens, max_highlights
    )
    result = await agent.run(user)
    data = result.output.model_dump()
    print(f"[INFO] pydantic-ai response: {len(json.dumps(data))} chars",
          file=sys.stderr)
    print(f"[INFO] usage: {result.usage}", file=sys.stderr)
    return data


# ---------------------------------------------------------------------------
# json_mode fallback path (original client)
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> str:
    """Extract JSON object from LLM response (handles markdown fences)."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return text[s:e + 1].strip()
    return text.strip()


async def _call_json_mode(
    system: str,
    user: str,
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float = 0.2,
    max_tokens: int = 8000,
) -> dict:
    """Original json_mode call with retry-on-ValidationError."""
    from pipeline.llm_client import LLMClient

    client = LLMClient(model=model_name, base_url=base_url, api_key=api_key)
    _last_error = ""
    try:
        for attempt in (1, 2):
            temp = temperature if attempt == 1 else 0.1
            prompt = user if attempt == 1 else (
                f"{user}\n\n-- CORRECTION --\nYour previous response failed "
                f"validation: {_last_error}\nFix the JSON structure and retry."
            )
            print(f"[INFO] Calling {model_name} (json_mode attempt {attempt}, T={temp})...",
                  file=sys.stderr)
            raw = await client.complete(
                prompt=prompt, system=system,
                temperature=temp, max_tokens=max_tokens, json_mode=True,
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


# ---------------------------------------------------------------------------
# Public dispatch


async def call_llm(
    system: str,
    user: str,
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    client_kind: str = "pydantic_ai",
    temperature: float = 0.2,
    max_tokens: int = 8000,
    max_highlights: int = 5,
) -> dict:
    """Tailored structured output: pydantic-ai, or json_mode fallback.

    All connection params, including ``client_kind``, are passed explicitly
    by the caller (resolved from CLI > env > config.yaml > defaults) so the
    client stays transport-agnostic and config-symmetric.
    """
    print(f"[INFO] LLM client: {client_kind}", file=sys.stderr)
    if client_kind == "json_mode":
        return await _call_json_mode(
            system, user,
            model_name=model_name, base_url=base_url, api_key=api_key,
            temperature=temperature, max_tokens=max_tokens,
        )
    return await _call_pydantic_ai(
        system, user,
        model_name=model_name, base_url=base_url, api_key=api_key,
        temperature=temperature, max_tokens=max_tokens,
        max_highlights=max_highlights,
    )
