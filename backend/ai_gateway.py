"""Provider-neutral OpenAI-compatible transport, shared by all original bot features."""
import os
import requests


def chat_completion(payload, timeout=60, vision=False):
    key_name = "AI_VISION_API_KEY" if vision else "AI_API_KEY"
    key = os.getenv(key_name) or (os.getenv("GROQ_API_KEY", "") if not vision else "")
    if not key:
        raise RuntimeError(f"AI belum dikonfigurasi. Isi {key_name} di server.")
    default_base = "https://openrouter.ai/api/v1" if vision else "https://api.groq.com/openai/v1"
    base = os.getenv("AI_VISION_BASE_URL" if vision else "AI_BASE_URL", default_base).rstrip("/")
    body = dict(payload)
    # Do not assume Groq-specific reasoning settings work with every routed model.
    body.pop("reasoning_effort", None)
    effort = os.getenv("AI_REASONING_EFFORT", "")
    if effort:
        body["reasoning_effort"] = effort
    return requests.post(base + "/chat/completions", json=body,
                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                         timeout=timeout)
