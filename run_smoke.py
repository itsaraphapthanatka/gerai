"""ทดสอบ batch job: select_targets (due/all/brand) + run_targets เรียก run_for_brand (mock)."""
import os
os.environ["LITELLM_BASE_URL"] = ""
import datetime
import run_monitors as rm
import app.geo_worker as gw

now = datetime.datetime.now()
brands = [
    {"id": 1, "name": "NoRun", "last_run_at": None},
    {"id": 2, "name": "Old", "last_run_at": (now - datetime.timedelta(days=10)).isoformat(timespec="seconds")},
    {"id": 3, "name": "Recent", "last_run_at": (now - datetime.timedelta(days=1)).isoformat(timespec="seconds")},
]

due = rm.select_targets(brands, due=True, days=7)
assert [b["id"] for b in due] == [1, 2], due
print("due (>=7d) selection OK:", [b["id"] for b in due])

assert len(rm.select_targets(brands, all_=True)) == 3
assert [b["id"] for b in rm.select_targets(brands, brand_id=3)] == [3]
print("all / single-brand selection OK")

calls = []
gw.run_for_brand = lambda bid: (calls.append(bid) or {"brand_hits": 1, "questions": 2})
rm.run_targets(due)
assert calls == [1, 2], calls
print("run_targets invoked run_for_brand for:", calls)
print("ALL SCHEDULER TESTS OK")
