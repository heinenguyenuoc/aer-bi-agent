"""Thin LLM client using only the standard library (urllib).

Gemini, Groq, OpenRouter and Ollama expose OpenAI-compatible endpoints, so one function serves them all.
Anthropic uses its own Messages API. The 'mock' provider lets you try the UI offline.

NOTE: the default model names below may be out of date, and free-tier quotas change often.
Check the provider's model/pricing page and override with LLM_MODEL or config.yaml.
"""
import json
import os
import time
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


PROVIDERS = {
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               "key_env": "GEMINI_API_KEY", "model": "gemini-3.8-flash"},
    "groq": {"url": "https://api.groq.com/openai/v1/chat/completions",
             "key_env": "GROQ_API_KEY", "model": "llama-3.3-70b-versatile"},
    "openrouter": {"url": "https://openrouter.ai/api/v1/chat/completions",
                   "key_env": "OPENROUTER_API_KEY", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "ollama": {"url": os.getenv("OLLAMA_URL", "http://localhost:11434") + "/v1/chat/completions",
               "key_env": None, "model": "qwen2.5-coder:7b"},
    "anthropic": {"url": "https://api.anthropic.com/v1/messages",
                  "key_env": "ANTHROPIC_API_KEY", "model": "claude-sonnet-5-5"},
    "mock": {"url": None, "key_env": None, "model": "mock"},
}


def default_model(provider: str) -> str:
    return PROVIDERS[provider]["model"]


def _post(url: str, headers: dict, body: dict, timeout: int, retries: int = 2) -> dict:
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code in (429, 500, 502, 503, 529) and attempt < retries:   # free tiers often return 429
                wait = float(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                time.sleep(min(wait, 10))
                continue
            raise LLMError(f"HTTP {e.code}: {detail}")
        except (TimeoutError, OSError) as e:      # read timeout / connection reset (URLError is an OSError too)
            reason = getattr(e, "reason", e)
            if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
                if attempt < retries:
                    continue
                raise LLMError(f"The model did not answer within {timeout}s (tried {retries + 1} times). "
                               "Try a faster model (LLM_MODEL) or check the provider's status and quota.")
            raise LLMError(f"Cannot reach LLM endpoint: {reason}")
    raise LLMError("LLM request failed after retries.")


def chat(messages: list[dict], provider: str, model: str | None = None, temperature: float = 0.0,
         timeout: int = 60) -> dict:
    """messages: [{'role': 'system'|'user'|'assistant', 'content': str}]. Returns {text, prompt_tokens, completion_tokens}."""
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider '{provider}'. Options: {', '.join(PROVIDERS)}")
    cfg = PROVIDERS[provider]
    model = model or cfg["model"]

    if provider == "mock":
        return {"text": "```sql\nSELECT COUNT(*) AS wells FROM dim_well\n```", "prompt_tokens": 0, "completion_tokens": 0}

    key = os.getenv(cfg["key_env"], "") if cfg["key_env"] else ""
    if cfg["key_env"] and not key:
        raise LLMError(f"Missing API key: set the environment variable {cfg['key_env']}.")

    if provider == "anthropic":
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [m for m in messages if m["role"] != "system"]
        out = _post(cfg["url"], {"x-api-key": key, "anthropic-version": "2023-06-01"},
                    {"model": model, "max_tokens": 1500, "temperature": temperature, "system": system, "messages": msgs},
                    timeout)
        text = "".join(b.get("text", "") for b in out.get("content", []))
        u = out.get("usage", {})
        return {"text": text, "prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0)}

    headers = {"Authorization": f"Bearer {key}"} if key else {}
    # Optional fallbacks: LLM_FALLBACK_MODELS="model-a,model-b". Tried in order when the main model is overloaded
    # (HTTP 503/429), unavailable (404) or too slow.
    fallbacks = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if m.strip()]
    out, last_err = None, None
    tried = [model] + [f for f in fallbacks if f != model]
    for m in tried:
        body = {"model": m, "messages": messages, "temperature": temperature}
        effort = os.getenv("LLM_REASONING_EFFORT")   # optional, e.g. "low": less "thinking" = faster on reasoning models
        if effort:
            body["reasoning_effort"] = effort
        try:
            out = _post(cfg["url"], headers, body, timeout)
            break
        except LLMError as e:
            last_err = e
            if not any(t in str(e) for t in ("HTTP 503", "HTTP 429", "HTTP 404", "did not answer")):
                raise
    if out is None:
        raise LLMError(f"{last_err} [models tried: {', '.join(tried)}]")
    try:
        text = out["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise LLMError(f"Unexpected response: {str(out)[:300]}")
    u = out.get("usage", {}) or {}
    return {"text": text, "prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0)}
