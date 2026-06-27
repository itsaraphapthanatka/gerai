"""ทดสอบ search backend: เลือก backend + parse brave/serper + fallback (mock httpx)."""
import os
import app.geo_worker as gw
import httpx

for k in ("GEO_SEARCH_BACKEND", "BRAVE_API_KEY", "SERPER_API_KEY"):
    os.environ.pop(k, None)
assert gw.active_backend() == "ddgs"
os.environ["GEO_SEARCH_BACKEND"] = "brave"
assert gw.active_backend() == "ddgs", "brave ไม่มีคีย์ ต้อง fallback ddgs"
os.environ["BRAVE_API_KEY"] = "x"
assert gw.active_backend() == "brave"
os.environ["GEO_SEARCH_BACKEND"] = "serper"; os.environ["SERPER_API_KEY"] = "y"
assert gw.active_backend() == "serper"
print("active_backend logic OK")


class FakeResp:
    def __init__(self, j): self._j = j
    def raise_for_status(self): pass
    def json(self): return self._j


# brave
os.environ["GEO_SEARCH_BACKEND"] = "brave"; os.environ["BRAVE_API_KEY"] = "x"; os.environ.pop("SERPER_API_KEY", None)
httpx.get = lambda *a, **k: FakeResp({"web": {"results": [
    {"title": "A", "url": "https://www.foo.com/x"}, {"title": "B", "url": "https://bar.co.th/y"}]}})
res = gw.search("q", 8)
assert [r["domain"] for r in res] == ["foo.com", "bar.co.th"] and res[0]["position"] == 1, res
print("brave parse OK:", [r["domain"] for r in res])

# serper
os.environ["GEO_SEARCH_BACKEND"] = "serper"; os.environ["SERPER_API_KEY"] = "y"
httpx.post = lambda *a, **k: FakeResp({"organic": [{"title": "S", "link": "https://baz.com/p"}]})
res2 = gw.search("q", 8)
assert [r["domain"] for r in res2] == ["baz.com"], res2
print("serper parse OK:", [r["domain"] for r in res2])

# fallback: backend จ่ายเงินล่ม -> กลับไป ddgs (mock ddgs กัน network)
gw._ddgs_search = lambda q, l: [{"title": "DD", "url": "https://ddgs.fallback/", "domain": "ddgs.fallback", "position": 1}]
os.environ["GEO_SEARCH_BACKEND"] = "brave"; os.environ["BRAVE_API_KEY"] = "x"
def boom(*a, **k):
    raise RuntimeError("brave down")
httpx.get = boom
res3 = gw.search("q", 8)
assert res3 and res3[0]["domain"] == "ddgs.fallback", res3
print("fallback to ddgs on backend error OK")
print("ALL SEARCH BACKEND TESTS OK")
