"""A gateway that speaks GLC's contract and answers from a local model.

S14Code talks to exactly one seam: ``POST {GLC_BASE_URL}/v1/chat`` returning
``{text, provider, model}``. That is the whole surface between the framework and
whatever is generating tokens. This serves that contract and forwards to an
Ollama instance, so the framework can be run end to end without a hosted key —
unchanged, unpatched, and unaware.

    TELLTALE_OLLAMA=http://192.168.32.2:11434 uv run telltale-gateway   # :8112

Then point S14Code at it and the composed turns are real:

    GLC_BASE_URL=http://127.0.0.1:8112 uv run uvicorn s13code.main:app --port 8113

This lives in Telltale rather than in S14Code on purpose. It is an operational
convenience for running the application, not a contribution to the framework,
and the pull request should not carry a second gateway implementation for a
model the assignment does not name.

What it does NOT do is loosen anything. It returns text; the framework parses,
validates and repairs it exactly as it does for any other provider. A local
model gets no more trust than a hosted one.
"""

from __future__ import annotations

import os

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

OLLAMA = os.getenv("TELLTALE_OLLAMA", "http://192.168.32.2:11434").rstrip("/")
MODEL = os.getenv("TELLTALE_OLLAMA_MODEL", "gemma4:latest")
PORT = int(os.getenv("TELLTALE_GATEWAY_PORT", "8112"))

app = FastAPI(title="Telltale local gateway (GLC contract)")


class ChatBody(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    system: str = ""
    max_tokens: int = 2000
    temperature: float = 0
    # Accepted and ignored: GLC's routing knobs mean nothing to one local model,
    # and rejecting them would make the framework's payload provider-specific.
    reasoning: str | None = None
    agent: str | None = None
    session: str | None = None
    provider: str | None = None


@app.get("/healthz")
async def healthz():
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            tags = (await client.get(f"{OLLAMA}/api/tags")).json()
        except httpx.HTTPError as error:
            raise HTTPException(503, f"ollama unreachable at {OLLAMA}: {error}") from error
    return {"ok": True, "upstream": OLLAMA,
            "models": [m.get("name") for m in tags.get("models", [])]}


@app.post("/v1/chat")
async def chat(body: ChatBody):
    prompt = "\n\n".join(str(m.get("content", "")) for m in body.messages if m.get("content"))
    messages = ([{"role": "system", "content": body.system}] if body.system else []) + \
               [{"role": "user", "content": prompt}]

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        # The compose step asks for one JSON object. Constraining the decoder to
        # JSON removes a whole class of "almost JSON" replies that would fail to
        # parse — it does not make the content any more trusted.
        "format": "json",
        "options": {"temperature": body.temperature, "num_predict": body.max_tokens,
                    "num_ctx": int(os.getenv("TELLTALE_OLLAMA_CTX", "16384"))},
    }
    async with httpx.AsyncClient(timeout=900) as client:
        try:
            response = await client.post(f"{OLLAMA}/api/chat", json=payload)
        except httpx.HTTPError as error:
            raise HTTPException(503, f"ollama unreachable: {error}") from error
    if response.status_code >= 400:
        raise HTTPException(502, f"ollama {response.status_code}: {response.text[:300]}")

    reply = response.json()
    return {"text": (reply.get("message") or {}).get("content", ""),
            "provider": "ollama", "model": reply.get("model", MODEL)}


def main() -> None:
    print(f"GLC-contract gateway on :{PORT} -> {OLLAMA} ({MODEL})")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
