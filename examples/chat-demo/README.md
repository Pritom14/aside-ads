# Chat demo

A minimal chat page with a labeled sponsored card beside relevant answers, served by your local Aside.

```bash
# from the repo root
uv venv -p 3.12 && source .venv/bin/activate
uv pip install -e .            # add ".[embeddings]" for the local embedding model
aside seed-demo                # demo publisher, keys (.aside/demo.env) and 8 fictional campaigns
aside serve                    # http://127.0.0.1:8000  (dashboard at /dashboard)
python examples/chat-demo/app.py   # in a second terminal: http://127.0.0.1:8001
```

- Answers come from a canned responder by default. Set `LLM_BASE_URL`, `LLM_API_KEY` and
  `LLM_MODEL` to use any OpenAI-compatible endpoint (OpenAI, Ollama, vLLM, LM Studio).
- The ad is requested in parallel with the answer, using only the latest user message, and is
  rendered in its own column. It never edits or appears inside the answer.
- After a sensitive message the demo passes `sensitive: true` for the next three turns of that session.
- Ad clicks go to `/landing/<slug>` on this demo server, which shows the click id an advertiser
  would use for a conversion postback.
- `aside seed-demo --scenario cooking` (in a fresh `ASIDE_DATA_DIR`) turns the page into "Rasoi AI",
  a free cooking assistant with cooking prompts, written answers for them, and kitchen and grocery
  advertisers bidding in INR. The scenario is recorded in `demo.env` (`ASIDE_DEMO_SCENARIO`); start the
  server with `ASIDE_CURRENCY=INR` so the dashboard labels amounts in rupees. Try "Paneer butter masala
  for 4 people?" (a grocery card), "My kadhai keeps sticking, how do I fix it?" (cookware) and "Who
  invented the pressure cooker?" (no card: nothing is relevant enough).

## Hosted mode

[`hosted.py`](hosted.py) runs this app and the ad server together on one port, with demo data seeded on
boot, the dashboard hidden, chat rate limits and daily resets. It is what the public demo image runs; see
[`deploy/README.md`](../../deploy/README.md).

```bash
PORT=8080 python examples/chat-demo/hosted.py    # from the repo root, after installing as above
```
