"""ทดสอบ pagespeed.summarize/grade แบบไม่ยิงเน็ต — JSON ย่อจากรูปแบบจริงของ PageSpeed Insights v5"""
import app.pagespeed as ps

RAW = {
  "lighthouseResult": {
    "finalDisplayedUrl": "https://petgo.asia/", "fetchTime": "2026-10-04T15:00:00.000Z", "lighthouseVersion": "12.6.0",
    "categories": {"performance": {"score": 0.42}, "seo": {"score": 0.91}, "accessibility": {"score": 0.78}, "best-practices": {"score": None}},
    "audits": {
      "largest-contentful-paint": {"score": 0.3, "displayValue": "4.8 s", "numericValue": 4800},
      "cumulative-layout-shift": {"score": 0.95, "displayValue": "0.02", "numericValue": 0.02},
      "total-blocking-time": {"score": 0.5, "displayValue": "600 ms", "numericValue": 600},
      "first-contentful-paint": {"score": 0.6, "displayValue": "2.1 s", "numericValue": 2100},
      "render-blocking-resources": {"title": "Eliminate render-blocking resources", "displayValue": "Potential savings of 1,200 ms",
                                    "details": {"type": "opportunity", "overallSavingsMs": 1200}},
      "unused-javascript": {"title": "Reduce unused JavaScript", "details": {"type": "opportunity", "overallSavingsMs": 450}},
      "uses-long-cache-ttl": {"title": "cache", "details": {"type": "table"}},
      "zero-savings": {"title": "x", "details": {"type": "opportunity", "overallSavingsMs": 0}},
    }},
  "loadingExperience": {"overall_category": "AVERAGE", "origin_fallback": True,
    "metrics": {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 3100, "category": "AVERAGE"},
                "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 5, "category": "FAST"}}},
}
s = ps.summarize(RAW)
assert s["scores"] == {"performance": 42, "seo": 91, "accessibility": 78, "best-practices": None}, s["scores"]
assert [m["label"] for m in s["metrics"]] == ["LCP", "CLS", "TBT", "FCP"] and s["metrics"][0]["display"] == "4.8 s", s["metrics"]
assert [o["id"] for o in s["opportunities"]] == ["render-blocking-resources", "unused-javascript"], s["opportunities"]
assert s["field"]["overall"] == "AVERAGE" and s["field"]["origin_fallback"] and [m["label"] for m in s["field"]["metrics"]] == ["LCP", "CLS"]
assert s["final_url"] == "https://petgo.asia/" and s["lighthouse"] == "12.6.0"
print("summarize: คะแนน 4 หมวด · เมตริก lab (ตัด nbsp) · opportunities เรียงตามเวลาที่ประหยัด ตัดที่ 0 · CrUX OK")
assert ps.summarize({})["scores"]["performance"] is None and ps.summarize({})["field"] is None and ps.summarize({})["metrics"] == []
print("summarize: JSON ว่างไม่พัง OK")
assert (ps.grade(95), ps.grade(90), ps.grade(89), ps.grade(50), ps.grade(49), ps.grade(None)) == ("good", "good", "ok", "ok", "poor", "")
print("grade: เกณฑ์ Lighthouse 90/50 OK")
print("ALL SPEED TESTS OK")

# ไม่มีคีย์ + Google ตอบ 429 → บอกให้ใส่คีย์ ไม่ใช่โยนข้อความดิบ
class _R:  # จำลอง httpx.HTTPStatusError แบบไม่ต้องมี httpx บนเครื่องที่รันเทสต์
    pass
ps._key = lambda: None
def _boom(url, strategy): raise RuntimeError("Google ตอบ 429: Quota exceeded for quota metric 'Queries'")
ps.fetch = _boom
r = ps.scan("https://x.test")
assert "ใส่ PageSpeed Insights API key" in r["mobile"]["error"] and r["with_key"] is False, r["mobile"]
ps._key = lambda: "k" * 30
r = ps.scan("https://x.test")
assert "429" in r["mobile"]["error"] and "ใส่ PageSpeed" not in r["mobile"]["error"]
print("scan: 429 แบบไม่มีคีย์ → แนะนำใส่คีย์ · มีคีย์ → โชว์ข้อความจริง OK")
print("ALL SPEED TESTS OK (incl. quota)")
