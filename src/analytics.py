"""
analytics.py — Agent 5: Analytics (NL → SQL → Narrative Insight)
Berjalan lokal di Python (tidak lewat n8n) karena butuh akses ke DataFrame.
Groq free tier: llama3-70b-8192 untuk speed, qwen3-30b untuk kualitas.
"""

import os, json, sqlite3
import pandas as pd
from groq import Groq

GROQ_API_KEY = os.getenv("GROQ_API_KEY","")
MODEL        = "llama3-70b-8192"   # fast, cukup untuk analytics

SYSTEM_PROMPT = """You are a senior health insurance business analyst with deep SQL expertise.
Given a natural language question about insurance claims data, generate:
1. A precise SQLite SQL query
2. A one-sentence hypothesis

Available table: claims
Columns: period (int 2017-2019), premium (float EUR), cost_claims_year (float EUR),
         n_medical_services (int), age (int), gender (text M/F),
         type_policy (text I=Individual/C=Corporate),
         type_product (text S=Standard/P=Premium/D=Dental/I=International),
         reimbursement (text Yes/No), distribution_channel (text I/A/D),
         C_H (text H1-H6 health cluster), C_C (text C1-C6 coverage tier),
         lapse (int 1=new/2=active/3=lapsed), loss_ratio (float)

Return ONLY valid JSON (no markdown):
{"sql": "SELECT ... FROM claims ...", "hypothesis": "one sentence"}

Rules:
- Use GROUP BY for trend/comparison questions
- Add ORDER BY for ranking questions
- Use ROUND(x, 2) for float columns
- Limit to 15 rows max
- loss_ratio = cost_claims_year / premium (already computed)"""


def run_analytics(question: str, df: pd.DataFrame, db=None) -> dict:
    """
    Jawab pertanyaan bisnis dengan NL→SQL→narrative.
    df: DataFrame dataset Mendeley (atau sample-nya)
    db: database connection untuk logging (opsional)
    """
    if not GROQ_API_KEY:
        return {"error": "Set GROQ_API_KEY untuk Analytics Agent"}

    client = Groq(api_key=GROQ_API_KEY)

    # Step 1: Generate SQL
    r1 = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role":"system","content":SYSTEM_PROMPT},
            {"role":"user","content":f"Question: {question}"},
        ],
        temperature=0, max_tokens=400,
        response_format={"type":"json_object"},
    )
    try:
        plan = json.loads(r1.choices[0].message.content)
    except Exception:
        return {"error":"LLM gagal generate SQL","question":question}

    sql  = plan.get("sql","SELECT 1")
    hypo = plan.get("hypothesis","")

    # Step 2: Execute SQL via in-memory SQLite
    tmp_conn = sqlite3.connect(":memory:")
    try:
        sample = df.sample(min(8000, len(df)), random_state=42).copy()
        if "loss_ratio" not in sample.columns:
            sample["loss_ratio"] = sample["cost_claims_year"] / sample["premium"].replace(0,1)
        sample.to_sql("claims", tmp_conn, if_exists="replace", index=False)
        result_df  = pd.read_sql_query(sql, tmp_conn)
        result_lst = result_df.head(12).to_dict(orient="records")
        sql_error  = None
    except Exception as e:
        result_lst = []
        sql_error  = str(e)
    finally:
        tmp_conn.close()

    if sql_error:
        return {"question":question,"sql":sql,"hypothesis":hypo,"error":sql_error}

    # Step 3: Narrative insight
    result_str = json.dumps(result_lst, indent=2)[:1500]
    r2 = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role":"system","content":(
                "You are a senior health insurance analyst. "
                "Write 3 sentences of specific business insight from the SQL result. "
                "Include actual numbers. End with one actionable recommendation "
                "for the insurance company. Be direct and concrete."
            )},
            {"role":"user","content":f"Question: {question}\n\nData:\n{result_str}"},
        ],
        temperature=0.3, max_tokens=250,
    )
    narrative = r2.choices[0].message.content.strip()

    # Log ke DB jika tersedia
    if db:
        try:
            from db import log_analytics
            log_analytics(db, question, sql, narrative)
        except Exception:
            pass

    return {
        "question":   question,
        "hypothesis": hypo,
        "sql":        sql,
        "result":     result_lst,
        "narrative":  narrative,
        "row_count":  len(result_lst),
    }


# ── Pre-built insight questions untuk demo ────────────────────────────────────
DEMO_QUESTIONS = [
    "Mengapa biaya klaim meningkat dari 2017 ke 2019? Bandingkan avg cost per tahun.",
    "Produk asuransi apa yang memiliki loss ratio tertinggi? Tampilkan top 5.",
    "Bagaimana distribusi biaya klaim berdasarkan gender dan kelompok usia?",
    "Channel distribusi mana yang menghasilkan insured dengan biaya klaim terendah?",
    "Berapa rata-rata medical services per insured di tiap health cluster (C_H)?",
    "Policy dengan loss ratio > 2.0 — berapa banyak dan apa karakteristiknya?",
    "Bandingkan biaya klaim antara model reimbursement vs direct billing.",
    "Insured dengan seniority > 36 bulan — apakah biaya klaim lebih rendah?",
]
