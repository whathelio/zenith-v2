import asyncio, json, sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from backend.app import app

client = TestClient(app)

# 1. flash 列表
r = client.get("/api/news/flash")
print("FLASH status:", r.status_code)
if r.status_code == 200:
    data = r.json()
    print("FLASH items:", len(data.get("items", [])), "has_more:", data.get("has_more"))
    if data.get("items"):
        first = data["items"][0]
        print("FLASH[0] keys:", sorted(first.keys()))
        print("FLASH[0] title:", (first.get("title") or "")[:40])
        print("FLASH[0] source:", first.get("source"))
    else:
        print("FLASH empty items, full:", json.dumps(data, ensure_ascii=False)[:300])
else:
    print("FLASH error body:", r.text[:300])

# 2. search
r2 = client.get("/api/news/search", params={"keyword": "CPI"})
print("\nSEARCH status:", r2.status_code)
if r2.status_code == 200:
    d2 = r2.json()
    print("SEARCH items:", len(d2.get("items", [])), "keyword:", d2.get("keyword"))
    if d2.get("items"):
        print("SEARCH[0] title:", (d2["items"][0].get("title") or "")[:40])
        print("SEARCH[0] keyword:", d2["items"][0].get("keyword"))
else:
    print("SEARCH error body:", r2.text[:300])

# 3. 缺参数
r3 = client.get("/api/news/search")
print("\nSEARCH no keyword status:", r3.status_code, r3.text[:100])
