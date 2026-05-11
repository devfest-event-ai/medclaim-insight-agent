"""
n8n_client.py — Connector: Streamlit → n8n Sumopod webhook
n8n adalah BACKEND / BRAIN. Streamlit hanya mengirim data dan menampilkan hasil.

Flow:
  Streamlit → POST /webhook/medclaim-extract → n8n pipeline →
    Agent1 Preprocessor → Agent2 Groq LLM → Agent3 Validator →
    Agent4 Monitor → Format Output → response JSON → Streamlit

CARA DAPAT WEBHOOK URL dari Sumopod:
  1. Buka workflow "MedClaim Insight Pipeline" di Sumopod
  2. Klik node "Webhook Trigger"
  3. Copy "Production URL" (bukan test URL)
     Contoh: https://sumopod.app/webhook/xxxx-yyyy/medclaim-extract
  4. Paste ke .env: N8N_WEBHOOK_URL=https://sumopod.app/webhook/...
  5. Klik "Publish" di kanan atas workflow Sumopod
"""

import os, json, time, uuid, requests
from typing import Optional

N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")
TIMEOUT_SECONDS = 60   # Groq bisa lambat untuk record kompleks


def call_n8n_pipeline(record: dict) -> dict:
    """
    Kirim satu record ke n8n webhook → terima hasil extraction.
    record: dict satu baris CSV (field sesuai dataset Mendeley)
    Returns: dict hasil lengkap dari n8n (fmr, extracted, monitoring, dll)
    """
    if not N8N_WEBHOOK_URL:
        raise ValueError(
            "N8N_WEBHOOK_URL belum diset. "
            "Copy Production URL dari Webhook Trigger node di Sumopod."
        )

    payload = {"record": record}
    t_start = time.time()

    try:
        resp = requests.post(
            N8N_WEBHOOK_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept":       "application/json",
                "User-Agent":   "MedClaim-Streamlit/4.0",
            },
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        elapsed_ms = int((time.time() - t_start) * 1000)

        try:
            result = resp.json()
        except Exception:
            result = {"raw_response": resp.text}

        # n8n kadang return list
        if isinstance(result, list):
            result = result[0] if result else {}

        result["elapsed_ms"]     = elapsed_ms
        result["source"]         = "n8n_sumopod"
        result["pipeline_version"] = "4.0-groq"
        result["id"]             = result.get("id") or str(uuid.uuid4())

        return result

    except requests.Timeout:
        return {
            "id":    str(uuid.uuid4()),
            "error": f"Timeout setelah {TIMEOUT_SECONDS}s — Groq atau n8n tidak merespons",
            "source": "n8n_timeout",
            "elapsed_ms": int((time.time() - t_start) * 1000),
        }
    except requests.RequestException as e:
        return {
            "id":    str(uuid.uuid4()),
            "error": f"n8n webhook error: {str(e)}",
            "source": "n8n_error",
            "elapsed_ms": int((time.time() - t_start) * 1000),
        }


def call_n8n_batch(records: list, progress_callback=None) -> list:
    """
    Kirim beberapa record ke n8n satu per satu (sequential).
    progress_callback(i, total, result) dipanggil setelah tiap record.
    """
    results = []
    for i, rec in enumerate(records):
        result = call_n8n_pipeline(rec)
        results.append(result)
        if progress_callback:
            progress_callback(i + 1, len(records), result)
    return results


def test_n8n_connection() -> dict:
    """Kirim dummy record untuk test koneksi ke Sumopod."""
    dummy = {
        "ID_policy": "TEST-001",
        "ID_insured": "INS-001",
        "period": 2019,
        "premium": 1000.0,
        "cost_claims_year": 750.0,
        "n_medical_services": 8,
        "age": 35,
        "gender": "M",
        "type_policy": "I",
        "type_product": "S",
        "reimbursement": "No",
        "distribution_channel": "A",
        "C_H": "H2",
        "C_C": "C1",
        "lapse": 2,
        "seniority_insured": 24,
        "exposure_time": 1.0,
    }
    return call_n8n_pipeline(dummy)


# ── LOCAL FALLBACK (jika n8n tidak tersedia) ──────────────────────────────────
# Digunakan saat Sumopod offline atau URL belum diset.
# Menjalankan logika yang sama tapi secara lokal di Python.

def _local_preprocess(raw: dict) -> dict:
    """Mirror dari Agent 1 n8n — untuk fallback."""
    def sf(v, d=0.0):
        try: return float(v)
        except: return d
    def si(v, d=0):
        try: return int(float(v))
        except: return d

    c = {
        "policy_id":          str(raw.get("ID_policy","???")),
        "insured_id":         str(raw.get("ID_insured","???")),
        "period":             si(raw.get("period"), 2019),
        "premium":            sf(raw.get("premium"), 0.0),
        "cost_claims_year":   sf(raw.get("cost_claims_year"), 0.0),
        "n_medical_services": si(raw.get("n_medical_services"), 0),
        "age":                si(raw.get("age"), 35),
        "gender":             str(raw.get("gender","U")).upper()[:1],
        "type_policy":        str(raw.get("type_policy","U")),
        "type_product":       str(raw.get("type_product","U")),
        "C_H":                str(raw.get("C_H","H1")),
        "C_C":                str(raw.get("C_C","C1")),
        "lapse":              si(raw.get("lapse"), 2),
    }
    p = c["premium"]
    c["loss_ratio"] = round(c["cost_claims_year"] / p, 4) if p > 0 else 0.0

    pol  = {"I":"Individual","C":"Corporate"}
    prod = {"S":"Standard","P":"Premium","D":"Dental","I":"International"}
    stat = {1:"new",2:"active",3:"lapsed"}

    c["messy_text"] = f"""HEALTH INSURANCE CLAIM RECORD
Reference  : POL-{c['policy_id']} / INS-{c['insured_id']}
Fiscal Year: {c['period']}
Patient: {('Male' if c['gender']=='M' else 'Female')}, {c['age']} years old
Policy: {pol.get(c['type_policy'],c['type_policy'])} — {prod.get(c['type_product'],c['type_product'])}
FINANCIALS:
  Annual premium        : EUR {c['premium']:,.2f}
  Total claims this year: EUR {c['cost_claims_year']:,.2f}
  Medical encounters    : {c['n_medical_services']} services
Health cluster: {c['C_H']}  Coverage: {c['C_C']}
Status: {c['lapse']} ({stat.get(c['lapse'],'unknown')})""".strip()

    return c


def call_local_pipeline(record: dict) -> dict:
    """
    Fallback: jalankan pipeline lokal tanpa n8n.
    Berguna saat Sumopod offline / testing.
    """
    import os
    from groq import Groq

    groq_key = os.getenv("GROQ_API_KEY","")
    if not groq_key:
        return {"error":"Set GROQ_API_KEY atau N8N_WEBHOOK_URL","id":str(uuid.uuid4())}

    t0 = time.time()
    cleaned = _local_preprocess(record)
    client  = Groq(api_key=groq_key)

    PROMPT = """Extract all fields from the insurance document. Return ONLY JSON.
Schema: {"policy_id":str,"insured_id":str,"period":int,"premium_eur":float,
"cost_claims_eur":float,"n_medical_services":int,"patient_age":int,
"patient_gender":"M"or"F","policy_type":str,"product_type":str,
"reimbursement_model":str,"loss_ratio":float,"health_cluster":str,
"coverage_tier":str,"policy_status":str,
"risk_flag":"HIGH"if lr>1.5 or svc>80 else"MEDIUM"if lr>0.8 else"LOW",
"anomaly_reason":str_or_null}"""

    resp = client.chat.completions.create(
        model= "llama-3.3-70b-versatile",
        messages=[
            {"role":"system","content":PROMPT},
            {"role":"user","content":f"DOCUMENT:\n{cleaned['messy_text']}\n\nExtract:"}
        ],
        temperature=0, max_tokens=600,
        response_format={"type":"json_object"},
    )
    extracted = json.loads(resp.choices[0].message.content)

        # Simple FMR (Safe extraction: handles missing keys AND null/None values)
    checks = {
        "premium":  abs((extracted.get("premium_eur") or 0) - cleaned["premium"]) < cleaned["premium"] * 0.05 + 0.01,
        "cost":     abs((extracted.get("cost_claims_eur") or 0) - cleaned["cost_claims_year"]) < cleaned["cost_claims_year"] * 0.05 + 0.01,
        "age":      str(extracted.get("patient_age") or "") == str(cleaned["age"]),
        "gender":   str(extracted.get("patient_gender") or "").upper()[:1] == cleaned["gender"],
        "services": str(extracted.get("n_medical_services") or "") == str(cleaned["n_medical_services"]),
        "lr":       abs((extracted.get("loss_ratio") or 0) - cleaned["loss_ratio"]) < 0.1,
    }
    fmr = round(sum(checks.values()) / len(checks), 4)
    rec = "approve" if fmr >= 0.85 else ("review" if fmr >= 0.60 else "reject")

    lr = cleaned["loss_ratio"]
    mon = {"batch_status": "CRITICAL" if lr > 5 else ("REVIEW" if lr > 2 else "CLEAN"),
           "alerts": [{"severity": "HIGH", "message": f"Loss ratio {lr:.2f}x"}] if lr > 2 else []}

    return {
        "id":             str(uuid.uuid4()),
        "source":         "local_python_fallback",
        "pipeline_version":"4.0-local",
        "fmr":            fmr,
        "fmr_detail":     checks,
        "recommendation": rec,
        "risk_flag":      extracted.get("risk_flag","UNKNOWN"),
        "loss_ratio":     cleaned["loss_ratio"],
        "extracted":      extracted,
        "ground_truth":   cleaned,
        "monitoring":     mon,
        "elapsed_ms":     int((time.time()-t0)*1000),
    }
