"""Aside chat demo.

A minimal chat app: the assistant answer comes from an OpenAI-compatible LLM
(if LLM_BASE_URL is set) or a canned responder, and a labeled sponsored card
from your local Aside server appears BESIDE relevant answers, never inside
them, and never beside sensitive conversations.

Run (from the repo root, after `aside seed-demo` and `aside serve`):

    python examples/chat-demo/app.py          # http://127.0.0.1:8001

Config (environment, or .aside/demo.env written by `aside seed-demo`):
    ASIDE_URL, ASIDE_API_KEY, ASIDE_PLACEMENT
    LLM_BASE_URL (e.g. https://api.openai.com/v1 or http://localhost:11434/v1), LLM_API_KEY, LLM_MODEL
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import sys
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

try:
    from aside_ads_client import AsyncAsideClient
except ImportError:  # running from a checkout without installing the SDK
    sys.path.insert(0, str(REPO / "sdks" / "python" / "src"))
    from aside_ads_client import AsyncAsideClient


def _load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (Path(os.environ.get("ASIDE_DEMO_ENV", ".aside/demo.env")), REPO / ".aside" / "demo.env"):
        if path.exists():
            for line in path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    values.setdefault(k.strip(), v.strip())
            break
    values.update({k: v for k, v in os.environ.items() if k.startswith(("ASIDE_", "LLM_"))})
    return values


CONFIG = _load_env()
ASIDE_URL = CONFIG.get("ASIDE_URL", "http://127.0.0.1:8000")
ASIDE_API_KEY = CONFIG.get("ASIDE_API_KEY", "")
PLACEMENT = CONFIG.get("ASIDE_PLACEMENT", "chat-sidebar")

CANNED = [
    (
        ("postgres", "sql", "query", "queries", "index"),
        "Start with `EXPLAIN (ANALYZE, BUFFERS)` on the slow query. Look for sequential scans on large "
        "tables and add an index on the filtered or joined columns. Check `pg_stat_statements` for the "
        "queries with the highest total time, and run `ANALYZE` so the planner has fresh statistics.",
    ),
    (
        ("github actions", "ci", "build", "pipeline"),
        "Cache your dependency directory keyed on the lockfile hash, split the test suite across a matrix "
        "of runners, and skip jobs whose paths did not change. Most 20 minute builds spend half their "
        "time reinstalling dependencies.",
    ),
    (
        ("fine-tune", "fine tune", "llama", "lora", "gpu"),
        "For a first fine-tune, use LoRA or QLoRA on a 7B or 8B model: it fits on a single 24 GB GPU. "
        "Prepare a few thousand high-quality instruction pairs in the model's chat format, hold out 5% "
        "for evaluation, and train for 1 to 3 epochs.",
    ),
    (
        ("gst", "invoice", "freelancer", "tax"),
        "Freelancers above the threshold register for GST, issue tax invoices with your GSTIN, and file "
        "GSTR-1 (outward supplies) and GSTR-3B (summary and payment). Reconcile your purchases against "
        "GSTR-2B before claiming input tax credit.",
    ),
    (
        ("spanish", "hindi", "japanese", "learn", "language"),
        "Fifteen minutes a day beats two hours on Sunday. Learn the 300 most frequent words first, shadow "
        "native audio out loud, and start speaking in week one, even badly.",
    ),
    (
        ("trip", "travel", "pack", "japan", "flight"),
        "Pack light layers, a universal adapter (Japan uses type A plugs at 100 V), a small towel and "
        "comfortable walking shoes. Carry some cash: smaller shops still prefer it. Get an IC card like "
        "Suica for trains on day one.",
    ),
    (
        ("sourdough", "bread", "bake", "baking", "recipe"),
        "Mix 500 g bread flour, 350 g water and 100 g active starter. Rest 30 minutes, add 10 g salt, then "
        "do four sets of stretch-and-folds over two hours. Bulk ferment until about 50% bigger, shape, "
        "cold proof overnight, and bake at 250 C in a covered pot for 20 minutes, then 20 uncovered.",
    ),
    (
        ("export", "markdown", "save this chat"),
        "You can copy any answer with the copy button. Exporting whole conversations is a Pro feature in "
        "this demo app.",
    ),
]

CRISIS = (
    "I'm really sorry you're going through this. You don't have to handle it alone. If you might act on "
    "these thoughts, please contact local emergency services now. In India you can call Tele-MANAS at "
    "14416, in the US call or text 988. Would you like to talk about what's been happening?"
)


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def canned_answer(text: str) -> str:
    t = text.lower().replace("\u2019", "'")
    if _has(t, ("kill myself", "suicide", "want to die", "hurt myself", "don't want to live")):
        return CRISIS
    if _has(t, ("chest pain", "can't breathe", "not breathing", "overdose")):
        return (
            "This sounds like an emergency. Call your local emergency number now "
            "(112 in India and the EU, 911 in the US)."
        )
    for keys, answer in CANNED:
        if _has(t, keys):
            return answer
    return (
        f"(Demo responder) You said: “{text[:200]}”. Set LLM_BASE_URL and LLM_API_KEY to use a real "
        "model. Try one of the suggested prompts to see contextual sponsored cards."
    )


def _llm_call(messages: list[dict[str, str]]) -> str:
    base = CONFIG["LLM_BASE_URL"].rstrip("/")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(
            {"model": CONFIG.get("LLM_MODEL", "gpt-4o-mini"), "messages": messages, "temperature": 0.3}
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {CONFIG.get('LLM_API_KEY', '')}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (operator-provided URL)
        return json.loads(resp.read())["choices"][0]["message"]["content"]


async def answer(messages: list[dict[str, str]]) -> str:
    if CONFIG.get("LLM_BASE_URL"):
        try:
            return await asyncio.to_thread(_llm_call, messages)
        except Exception as exc:  # demo: fall back rather than fail
            return (
                f"(LLM call failed: {exc.__class__.__name__}; using the canned responder)\n\n"
                + canned_answer(messages[-1]["content"])
            )
    return canned_answer(messages[-1]["content"])


class Message(BaseModel):
    role: str
    content: str = Field(max_length=8000)


class ChatIn(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=50)
    session_id: str = Field(default="anonymous", max_length=128)


app = FastAPI(title="Aside chat demo")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


# After a sensitive turn, keep ads off for the next few turns of that session (demo-sized, in memory).
QUIET_TURNS = 3
_quiet: dict[str, int] = {}


@app.post("/api/chat")
async def chat(body: ChatIn) -> dict[str, Any]:
    messages = [m.model_dump() for m in body.messages]
    sid = body.session_id
    quiet = _quiet.get(sid, 0) > 0
    aside = AsyncAsideClient(ASIDE_URL, ASIDE_API_KEY, timeout=2.0)
    # Match the ad to the latest user message. The answer and the ad are fetched in
    # parallel; the ad never waits on, or edits, the answer.
    reply, ad = await asyncio.gather(
        answer(messages),
        aside.get_sponsored(messages[-1:], user_id=sid, placement=PLACEMENT, sensitive=quiet),
    )
    if aside.last_reason == "sensitive_context" and not quiet:
        _quiet[sid] = QUIET_TURNS
    elif quiet:
        _quiet[sid] -= 1
    return {
        "answer": reply,
        "sponsored": asdict(ad) if ad else None,
        "ad_reason": None if ad else aside.last_reason,
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "aside_url": ASIDE_URL,
        "placement": PLACEMENT,
        "has_key": bool(ASIDE_API_KEY),
        "llm": bool(CONFIG.get("LLM_BASE_URL")),
    }


@app.get("/landing/{slug}", response_class=HTMLResponse)
async def landing(slug: str, aside_click_id: str = "") -> str:
    """Stand-in landing page for the fictional demo advertisers."""
    s, cid = html.escape(slug), html.escape(aside_click_id)
    return f"""<!doctype html><meta charset="utf-8"><title>{s} (demo advertiser)</title>
<body style="font:16px/1.5 system-ui;max-width:640px;margin:48px auto;padding:0 16px">
<p style="color:#6b7078">Fictional advertiser landing page</p>
<h1>{s}</h1>
<p>You arrived through a signed Aside click. The advertiser receives a click id:</p>
<pre style="background:#f4f5f7;padding:12px">aside_click_id={cid}</pre>
<p>On signup or purchase the advertiser reports a conversion back to Aside:</p>
<pre style="background:#f4f5f7;padding:12px;white-space:pre-wrap">\
curl -X POST {html.escape(ASIDE_URL)}/v1/conversions \\
  -H "Authorization: Bearer $ADVERTISER_OR_ADMIN_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"click_id": "{cid}", "idempotency_key": "order-123", "value": 25}}'</pre>
<p><a href="/">Back to the chat</a></p></body>"""


if __name__ == "__main__":
    if not ASIDE_API_KEY:
        print(
            "No ASIDE_API_KEY found. Run `aside seed-demo` first (writes .aside/demo.env).", file=sys.stderr
        )
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8001")))
