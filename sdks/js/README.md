# aside-ads (TypeScript / JavaScript)

Client for [Aside](https://github.com/Pritom14/aside-ads), the self-hosted ad server for AI chat apps.
ESM, typed, no runtime dependencies. Node 18+, Deno, Bun and browsers. MIT licensed.

```bash
npm install aside-ads
```

On your server (the API key must never reach the browser):

```ts
import { AsideClient } from "aside-ads";

const aside = new AsideClient({ baseUrl: "http://127.0.0.1:8000", apiKey: process.env.ASIDE_API_KEY });

const ad = await aside.getSponsored({
  context: messages,            // string or [{ role, content }]
  userId: sessionId,            // optional; hashed by the server, used for frequency caps
  placement: "chat-sidebar",
});
// ad: { label: "Sponsored", headline, body, cta, url (signed click), impressionUrl, ... } | null
```

In the browser, render the ad beside the answer and record the impression when it is visible:

```ts
import { trackImpression } from "aside-ads";
trackImpression(ad); // navigator.sendBeacon, falls back to fetch; no key needed
```

`getSponsored` never throws: on timeout (default 800 ms) or any error it resolves to `null`.
Pass `onError` to observe failures. A React example lives in `examples/react/SponsoredCard.tsx`.

Build: `npm install && npm run build` (outputs `dist/`).
