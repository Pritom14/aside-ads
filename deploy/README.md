# Hosted demo

The `demo` image runs the Aside ad server and the [chat demo](../examples/chat-demo) in one process on one
public port. It is a public playground, not a production setup. What it does
([`examples/chat-demo/hosted.py`](../examples/chat-demo/hosted.py)):

- Serves the chat page, `/api/chat`, the demo landing pages, and the ad server's tracking (`/t/*`) and
  API (`/v1/*`) routes. `/dashboard` and `/v1/admin` return 404 to the public.
- Seeds the eight fictional demo campaigns on first boot and issues a fresh random admin key on every
  boot, revoking older ones. The key is printed to the logs only.
- Rate-limits `/api/chat` per client IP (20 per minute) and in total (600 per minute).
- Caps database growth: impressions, clicks, conversions and spend reset every 24 hours, or sooner once
  there are 50,000 events. Campaigns and keys stay.
- Never stores conversation text. The ad server keeps it in memory for one request; the chat app keeps
  only a per-session counter of quiet turns after a sensitive message.

Data lives in `/tmp/aside-demo` inside the container and disappears on restart, which re-seeds it.

## Fly.io

[`fly.toml`](../fly.toml) builds the `demo` target in region `bom` on the smallest shared CPU with 512 MB
(the local embedding model needs about 250 MB).

```bash
fly apps create aside-demo          # pick your own name and update `app` in fly.toml
fly deploy --ha=false               # one machine: the demo's data and signing key live on it
fly logs | grep "Admin key"         # this boot's admin key
```

The public URL used in ad links is derived from `FLY_APP_NAME` (`https://<app>.fly.dev`). Set
`ASIDE_PUBLIC_URL` in `[env]` if you use a custom domain. Machines stop when idle and start on the next
request; each cold start re-seeds and prints a new admin key.

## Hugging Face Spaces

1. Create a new Space with the Docker SDK.
2. Copy [`huggingface/README.md`](huggingface/README.md) (it carries the Space config: `sdk: docker`,
   `app_port: 7860`) and [`huggingface/Dockerfile`](huggingface/Dockerfile) into the Space repository.
3. The Space builds the demo from this repository's source at `ASIDE_REF` (default `main`; the repository
   must be public). The public URL comes from `SPACE_HOST`, and the admin key is in the Space logs.

## Any Docker host

```bash
docker build --target demo -t aside-demo .
docker run --rm -p 8080:8080 -e ASIDE_PUBLIC_URL=https://demo.example.com aside-demo
```

Without `ASIDE_PUBLIC_URL` (or `FLY_APP_NAME` / `SPACE_HOST`) ad links point at `http://localhost:PORT`.

## Without Docker

```bash
pip install -e ".[embeddings]" ./sdks/python
PORT=8080 python examples/chat-demo/hosted.py    # http://127.0.0.1:8080
```

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT`, `HOST` | `8080`, `0.0.0.0` in the image | Listen address |
| `ASIDE_PUBLIC_URL` | from `FLY_APP_NAME` or `SPACE_HOST`, else `http://localhost:PORT` | Base URL in ad links |
| `ASIDE_DEMO_CHAT_PER_MINUTE` | `20` | Chat requests per client IP per minute (0 = off) |
| `ASIDE_DEMO_CHAT_GLOBAL_PER_MINUTE` | `600` | Chat requests per minute in total (0 = off) |
| `ASIDE_DEMO_MAX_EVENTS` | `50000` | Reset activity early above this many events |
| `ASIDE_DEMO_RESET_HOURS` | `24` | Reset activity this often |
| `ASIDE_DEMO_DASHBOARD` | off | `1` exposes `/dashboard` and `/v1/admin` (still behind the admin key) |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | unset | Use a real OpenAI-compatible model instead of canned answers |

The client IP is read from `Fly-Client-IP`, then the first `X-Forwarded-For` entry. A host that appends
to a client-supplied `X-Forwarded-For` lets clients pick their own per-IP bucket, which is why the global
limit exists. If you connect a paid LLM, keep the global limit low.
