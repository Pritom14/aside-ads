---
title: Aside demo
emoji: 💬
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: agpl-3.0
short_description: Self-hosted contextual ads for AI chat, beside the answer
---

# Aside demo

A chat app with a labeled "Sponsored" card beside relevant answers, served by
[Aside](https://github.com/Pritom14/aside-ads), an open-source, self-hosted ad server for AI chat apps.
All advertisers are fictional. Conversation text is used in memory to pick an ad and is never stored.

Try "my postgres queries are slow", "what should I pack for Japan" or "give me a sourdough recipe".
Sensitive messages, such as "my mom passed away last week", get no ad.

This Space builds the `demo` image from the GitHub repository (see `Dockerfile`). The admin key for each
boot is printed in the Space logs; the dashboard is not exposed.
