# DATA_CONTRACTS — รูปข้อมูลที่เสนอ (ยังไม่แก้ schema)

**สถานะ: ข้อเสนอเท่านั้น** · ไม่มีการสร้าง/แก้ตารางใน stage 00 · ทุกตารางใหม่ในอนาคตเป็นแบบเพิ่มอย่างเดียว ผ่าน `_ensure_column`/`CREATE TABLE IF NOT EXISTS` ใน `app/db.py` ให้ใช้ได้ทั้ง SQLite และ Postgres
ข้อเสนอเพิ่มเติม: เพิ่มตาราง `schema_migrations(version, applied_at)` เพื่อให้ rollback/ตรวจเวอร์ชันได้ (ตอนนี้ไม่มี)

## 1. BusinessContext (stage 04B) — ผูกกับ `leads.id` ก่อน แล้วย้ายไป account ใน stage 09
```json
{
  "lead_id": 123, "version": 2, "updated_by": "customer|staff|system",
  "fields": {
    "primary_service": {"value": "ให้เช่าคลังสินค้า", "source": "confirmed|inferred", "evidence_ref": "ev_01"},
    "region":          {"value": "บางนา", "source": "inferred", "evidence_ref": "ev_02"},
    "audience":        {"value": null, "source": "unknown"},
    "questions":       [{"text": "...", "origin": "suggested|customer", "is_search_volume": false}],
    "competitor":      {"value": null, "source": "unknown"}
  },
  "created_at": "ISO-8601"
}
```
เก็บทุกเวอร์ชัน ไม่แก้ทับ

## 2. Evidence
```json
{"id": "ev_01", "kind": "page_text|schema|robots|search_result|ai_answer|gsc",
 "url": "https://...", "captured_at": "ISO-8601", "excerpt": "ข้อความดิบ (escape ตอนแสดง)",
 "protocol_id": "snapshot-v1", "provider": "httpx", "visibility": "private|internal_only|approved_for_publication"}
```

## 3. Measurement (ใช้นิยามจาก METRIC_DICTIONARY.md)
```json
{"family": "web_search_presence", "state": "found|absent|not_tested|unavailable|partial",
 "value": 0.25, "attempted": 8, "succeeded": 6, "failed": 2, "denominator": 6,
 "protocol_id": "sov-search-v1", "protocol_version": 1, "provider": "ddgs",
 "measured_at": "ISO-8601", "comparability": "comparable|qualified|non_comparable|legacy_search_only",
 "evidence_refs": ["ev_.."]}
```
`value` เป็น `null` เมื่อ state = not_tested/unavailable

## 4. Report view-model (stage 05) — adapter อ่านจาก `leads.audit_json`/`deep_json`/`deep_ai_json` เดิม ไม่แก้ข้อมูลเก่า
```json
{"identity": {"domain": "...", "checked_at": "...", "pages_checked": 5, "pages_failed": 1},
 "status": "completed|partial|provider_unavailable|failed",
 "summary": {"text": "...", "uncertainty": "..."},
 "context": "BusinessContext@version",
 "lanes": {"website_readiness": "Measurement", "web_search_presence": "Measurement",
           "ai_brand_mention": "Measurement", "ai_citation": "Measurement"},
 "coverage": {"lanes_tested": 1, "lanes_total": 4},
 "actions": [{"title": "...", "url": "...", "confidence": "high|medium|low", "rationale": "...",
              "effort": "~1 ชม.", "owner": "customer_web_team|jorai|either", "evidence_refs": []}],
 "draft_improvement": {"before": "...", "after": "...", "requires_fact_confirmation": true},
 "next_step": "confirm_context|request_plan|request_meeting|view_quote",
 "methodology_ref": "/methodology"}
```

## 5. PlanRequest (stage 06) — ใช้ `leads` + `lead_events` เดิม เพิ่มเฉพาะที่ขาด
```json
{"lead_id": 123, "report_token_hash": "sha256", "idempotency_key": "uuid",
 "name": "...", "role": "...", "contact_channel": "line|phone|email", "contact_value": "...",
 "desired_window": "ภายใน 1 เดือน", "web_readiness": "own_team|agency|none|unknown",
 "budget": "ยังไม่แน่ใจ|<range>",
 "consents": [{"purpose": "fulfil_request|discuss_service|marketing", "granted": true,
               "notice_version": "2026-11-v1", "source": "plan_form", "at": "ISO-8601"}],
 "status": "stored", "created_at": "ISO-8601"}
```
SalesBrief: สร้างจาก PlanRequest + BusinessContext + report — แยก `confirmed` / `inferred` / `unknown`

## 6. Appointment (stage 06A) — แยกจากสถานะดีล
```json
{"id": "ap_1", "lead_id": 123, "opportunity_id": null, "line_user_id": "U…|null",
 "status": "requested|pending_confirmation|confirmed|completed|cancelled|no_show|needs_review",
 "slot_start": "ISO-8601+07:00", "duration_min": 30, "timezone": "Asia/Bangkok",
 "format": "video|phone|onsite", "owner_staff_id": null, "version": 1,
 "history": [{"version": 1, "from": null, "to": "...", "by": "customer|staff", "reason": "", "at": "..."}],
 "provider_ref": null, "idempotency_key": "uuid"}
```
`requested` = แทน event `wants_call` เดิมได้ทันที

## 7. LineIdentityLink (stage 06A-1) — แทนการผูกจากโดเมน (`pipeline.py:723-731`)
```json
{"line_user_id": "U…", "lead_id": 123, "link_token_hash": "sha256", "scope": "report_view|appointment",
 "expires_at": "ISO-8601", "used_at": null, "revoked_at": null, "verified_by": "signed_link|staff"}
```

## 8. Attribution (stage 08B/17) — allowlist เท่านั้น
`{"source": "partner|organic|line|direct|referral|unknown", "partner_code": "abcd1234|null", "utm_source": "allowlisted|null", "first_seen": "...", "last_meaningful": "..."}` · ห้ามเก็บ PII/token/URL เต็มของรายงานใน analytics สาธารณะ

## 9. Feature flags (stage 01)
ไม่มี module กลาง → เสนอ `app/flags.py` อ่านจาก `settings` (key `flag_<name>`, ค่าเริ่มต้น OFF) + override ด้วย env สำหรับ local/test · flag แรก: `marketing_v2`, `snapshot_v2`, `report_v2`, `plan_request_v2`, `line_appointments`
