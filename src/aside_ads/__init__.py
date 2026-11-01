"""Aside: a self-hosted ad server for AI chat apps.

Conversations never leave your server. The conversation context sent to
``POST /v1/serve`` is used in memory to pick an ad and is then discarded: it is
never written to the database, logs, or caches. See ``aside_ads.privacy``.
"""

__version__ = "0.1.0"
