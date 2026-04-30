#!/usr/bin/env python3
"""Find a current btc-updown-5m token id on series 10684."""

import json
import urllib.request

URL = "https://gamma-api.polymarket.com/markets?closed=false&limit=50&series_id=10684"
HEADERS = {
    "user-agent": "polymarket-latency-probe/1.0",
    "accept": "application/json",
}

req = urllib.request.Request(URL, headers=HEADERS)
markets = json.loads(urllib.request.urlopen(req, timeout=10).read())

for m in markets:
    slug = m.get("slug") or ""
    if "btc-updown-5m" not in slug:
        continue
    tids = m.get("clobTokenIds")
    if isinstance(tids, str):
        tids = json.loads(tids)
    if not tids:
        continue
    print(f"slug={slug}")
    print(f"token={tids[0]}")
    break
else:
    print("no btc-updown-5m market found; first 5 active slugs on series 10684:")
    for m in markets[:5]:
        print(f"  {m.get('slug')}")
