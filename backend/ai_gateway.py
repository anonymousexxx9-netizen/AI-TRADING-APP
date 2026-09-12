"""Provider-neutral OpenAI-compatible transport, shared by all original bot features."""
import os
import requests


def chat_completion(payload, timeout=60):
    key = os.getenv("AI_API_KEY") or os.getenv("GROQ_API_KEY", "")
    if not key:
        raise RuntimeError("AI belum dikonfigurasi. Isi AI_API_KEY di server.")
    base = os.getenv("AI_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    body = dict(payload)
    # Do not assume Groq-specific reasoning settings work with every routed model.
    body.pop("reasoning_effort", None)
    effort = os.getenv("AI_REASONING_EFFORT", "")
    if effort:
        body["reasoning_effort"] = effort
    return requests.post(base + "/chat/completions", json=body,
                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                         timeout=timeout)
