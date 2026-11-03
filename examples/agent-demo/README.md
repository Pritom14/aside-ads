# Agent connector demo

Try Aside's MCP connector without real traffic. `simulate.py` seeds the cooking demo ("Rasoi AI" and five
advertisers, amounts in INR) and replays a simulated day of chats through the real API: serves,
impressions, clicks and conversions. The cookware advertiser's daily budget is small, so it runs out
early in the afternoon and the reports have something to find. Every number is simulated.

```bash
pip install "aside-ads[embeddings]"
python examples/agent-demo/simulate.py --data-dir .aside-agent-demo

export ASIDE_DATA_DIR=.aside-agent-demo ASIDE_TIMEZONE=Asia/Kolkata ASIDE_CURRENCY=INR ASIDE_FLOOR_CPM=20
aside serve --port 8010
```

Connect an agent with the admin key from `.aside-agent-demo/demo.env`, for example Claude Code:

```bash
source .aside-agent-demo/demo.env
claude mcp add --transport http aside http://127.0.0.1:8010/mcp --header "Authorization: Bearer $ASIDE_ADMIN_KEY"
```

Then ask "How did my ads do yesterday? What should I fix?" and "Raise Lohasutra's daily budget to ₹300."
The second one asks for your approval before it changes anything.
