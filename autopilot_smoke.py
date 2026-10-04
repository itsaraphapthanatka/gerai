"""ทดสอบ autopilot แบบไม่ยิงอะไร — แผนข้าม/รัน ตามความสด, ลำดับขั้น, ขั้นล้มไม่ล้มทั้งรอบ, สรุปข้อความ"""
import datetime
import app.autopilot as ap

now = datetime.datetime(2026, 10, 7, 2, 0)
last = {"sov": "2026-10-06T08:00:00", "rank": "2026-09-29T09:00:00", "gsc": None, "health": "2026-10-06T09:30:00",
        "speed": "2026-09-30T11:00:00", "ai": "2026-10-04T12:00:00", "aiserp": "2026-10-01T10:30:00"}
plan = ap.plan_steps(last, now, content_n=1)
assert plan["sov"][0] is False and "ชม.ก่อน" in plan["sov"][1]           # 18 ชม. < 20 → ข้าม
assert plan["rank"][0] is True and plan["gsc"][0] is True                # 8 วัน / ไม่เคย → รัน
assert plan["health"][0] is False and plan["speed"][0] is True
assert plan["ai"][0] is False and plan["aiserp"][0] is True             # ai 62 ชม. < 72 → ข้าม · aiserp 5.6 วัน → รัน
assert plan["content"] == (True, "") and ap.plan_steps(last, now, 0)["content"][0] is False
print("plan_steps: ของถูกข้ามถ้า <20 ชม. · ของแพงข้ามถ้า <3 วัน · ไม่เคยทำ=รัน · content 0 = ข้าม OK")

calls = []
def mk(name, ret):
    def f(*a): calls.append(name); return ret
    return f
actions = {
    "sov": mk("sov", {"brand_hits": 2, "questions": 8}),
    "rank": mk("rank", {"ranked": 3, "checked": 8, "avg_position": 6.3}),
    "gsc": mk("gsc", {"linked": True, "index": {"indexed": 1, "inspected": 22}, "sitemap": {"just_submitted": True}}),
    "health": mk("health", {"ok": False, "n_fail": 1, "checks": [{"label": "เนื้อหาอยู่ใน HTML", "status": "fail"}]}),
    "speed": lambda bid: (_ for _ in ()).throw(RuntimeError("PSI quota")),
    "ai": mk("ai", {"asked": 16, "rate": 0, "cost_usd": 0.41, "by_engine": {}}),
    "aiserp": mk("aiserp", {"skip": "ยังไม่ได้ตั้งคีย์ SerpApi"}),
    "content": lambda brand, n, mode: calls.append("content") or [{"id": 9, "title": "x", "status": "draft", "question": "q"}, {"status": "quota", "question": "q2"}],
    "report": lambda brand, log: calls.append("report") or "ส่งอีเมลรายงานถึง a@x",
    "notify": lambda brand, log: calls.append("notify") or True,
}
brand = {"id": 1, "name": "JKP", "tenant_id": 1}
log = ap.run(brand, actions, {}, content_n=2, mode="draft", now=now)
by = {s["step"]: s for s in log["steps"]}
assert calls == ["sov", "rank", "gsc", "health", "ai", "aiserp", "content", "report", "notify"], calls   # speed ล้ม แต่ลำดับครบ
assert by["speed"]["status"] == "error" and "PSI quota" in by["speed"]["detail"] and log["errors"] == 1 and log["ok"] is False
assert by["sov"]["detail"] == "SoV 2/8 คำถาม" and by["rank"]["detail"] == "ติดอันดับ 3/8 เฉลี่ย #6.3"
assert by["gsc"]["detail"] == "index 1/22 · ส่ง sitemap ให้แล้ว" and by["health"]["detail"].startswith("ไม่ผ่าน 1 ข้อ: เนื้อหา")
assert by["ai"]["detail"].startswith("ถูกพูดถึง 0% จาก 16") and by["aiserp"]["detail"].startswith("ยังไม่ได้ตั้งคีย์")
assert by["content"]["detail"] == "สร้าง 1 ชิ้น (ร่าง) · โควตาแพ็กเกจเต็ม" and log["content"][0]["id"] == 9
assert by["report"]["detail"] == "ส่งอีเมลรายงานถึง a@x" and by["notify"]["status"] == "ok"
assert "SoV 2/8" in log["summary"] and "แจ้งเตือน" not in log["summary"]
print("run: ลำดับครบ · ขั้นล้มบันทึก error แล้วไปต่อ · สรุปต่อขั้นถูก OK")

# ข้ามตามความสด + content 0
log2 = ap.run(brand, actions, last, content_n=0, mode="publish", now=now)
by2 = {s["step"]: s for s in log2["steps"]}
assert by2["sov"]["status"] == "skip" and by2["health"]["status"] == "skip" and by2["ai"]["status"] == "skip" and by2["content"]["status"] == "skip"
assert by2["rank"]["status"] == "ok" and by2["gsc"]["status"] == "ok" and by2["aiserp"]["status"] == "ok"
txt = ap.notify_text(brand, log2)
assert txt.startswith("🤖 Autopilot JKP: ทำ") and "index 1/22" in txt
print("run: ข้ามของที่เพิ่งทำ · notify_text สั้นกระชับ OK")
print("ALL AUTOPILOT TESTS OK")
