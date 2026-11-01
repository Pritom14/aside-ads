# aside-ads-client (Python)

Client for [Aside](https://github.com/Pritom14/aside-ads), the self-hosted ad server for AI chat apps.
Standard library only, Python 3.9+. MIT licensed.

```bash
pip install aside-ads-client        # or: pip install ./sdks/python
```

```python
from aside_ads_client import AsideClient

aside = AsideClient("http://127.0.0.1:8000", api_key="aside_pub_...")

ad = aside.get_sponsored(
    context=[{"role": "user", "content": "my postgres queries are slow"}],
    user_id=session_id,  # optional, hashed by the server, used for frequency caps
    placement="chat-sidebar",
)
if ad:
    # Render BESIDE the model's answer, never inside it:
    # ad.label ("Sponsored"), ad.headline, ad.body, ad.cta, link to ad.url (signed click URL)
    aside.track_impression(ad)  # server-side; or let the browser fetch ad.impression_url
```

Behaviour:

- Returns `None` when there is no relevant ad, the context is sensitive, or anything fails
  (timeout default 0.8 s). Pass `raise_errors=True` to get exceptions instead.
- `aside.last_reason` holds the server's reason for the last empty result (debugging only).
- `AsyncAsideClient` has the same methods as coroutines (runs the stdlib client in a thread).
