"""
MedClaim Insight Agent — Streamlit App v4
==========================================
Architecture:
  Streamlit UI  ──POST──►  n8n Sumopod (brain/backend)
                              └── Agent 1: Preprocessor (JS Code)
                              └── Agent 2: Groq LLM Extractor (HTTP→Groq API)
                              └── Agent 3: Validator FMR (JS Code)
                              └── Agent 4: Monitor Fraud (JS Code)
                              └── Format Output (JS Code)
                ◄──JSON──   n8n response
  Streamlit  ──►  Turso DB / SQLite (persist results)
  Streamlit  ──►  Analytics Agent (local Python, NL→SQL→Insight)

Deploy: HuggingFace Spaces (gratis)
Run local: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import json, os, uuid, time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MedClaim Insight Agent",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""<style>
.stTabs [data-baseweb="tab"]{font-weight:600;font-size:13px}
.metric-row{display:flex;gap:12px;margin-bottom:16px}
.stat-box{flex:1;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px;text-align:center}
.stat-num{font-size:26px;font-weight:700;color:#1e293b}
.stat-lbl{font-size:11px;color:#64748b;margin-top:3px}
.badge-ok{background:#dcfce7;color:#166534;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:500}
.badge-rv{background:#fef9c3;color:#854d0e;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:500}
.badge-rj{background:#fee2e2;color:#991b1b;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:500}
.badge-hi{background:#fee2e2;color:#991b1b;padding:2px 9px;border-radius:20px;font-size:11px}
.badge-md{background:#fef9c3;color:#854d0e;padding:2px 9px;border-radius:20px;font-size:11px}
.badge-lo{background:#dbeafe;color:#1e40af;padding:2px 9px;border-radius:20px;font-size:11px}
.flow-box{display:inline-block;background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px;padding:6px 12px;font-size:12px;font-weight:500}
.flow-arrow{color:#94a3b8;font-size:16px;padding:0 4px}
.info-card{background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:12px 16px;font-size:13px;color:#1e40af;margin:8px 0}
.warn-card{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px 16px;font-size:13px;color:#92400e;margin:8px 0}
</style>""", unsafe_allow_html=True)

# =============================================================================
# DEBUG MODE: Lazy initialization dengan error handling
# =============================================================================

def get_db_connection():
    """Lazy load DB connection dengan error handling"""
    try:
        from db import init_db as original_init_db
        db = original_init_db()
        return db
    except Exception as e:
        import streamlit as st
        st.warning(f"⚠️ Database connection failed: {e}")
        st.info("Aplikasi akan berjalan dalam mode demo (tanpa persistensi)")
        return None

def get_initial_stats(db):
    """Load stats dengan fallback jika DB gagal"""
    try:
        if db is None:
            return {"total": 0, "approved": 0, "review": 0, "fraud": 0, "avg_fmr": None}
        from db import get_stats
        return get_stats(db)
    except Exception as e:
        import streamlit as st
        st.warning(f"⚠️ Failed to load stats: {e}")
        return {"total": 0, "approved": 0, "review": 0, "fraud": 0, "avg_fmr": None}
# =============================================================================

# ── DB & SESSION STATE ────────────────────────────────────────────────────────
@st.cache_resource
def init_db():
    return get_db()

# db = init_db()

if "n8n_url" not in st.session_state:
    st.session_state["n8n_url"] = os.getenv("N8N_WEBHOOK_URL","")
if "groq_key" not in st.session_state:
    st.session_state["groq_key"] = os.getenv("GROQ_API_KEY","")
if "dataset" not in st.session_state:
    st.session_state["dataset"] = None


# =============================================================================
# MAIN APP
# =============================================================================

def main():

    # Fungsi aman untuk konversi angka (mencegah crash jika data berupa teks)
    def safe_float(val):
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0
            
    # ── Import modules ─────────────────────────────────────────────────────────────
    from db         import get_db, save_result, log_batch_fmr, get_all_results, get_fmr_history, get_stats, USE_TURSO
    from n8n_client import call_n8n_pipeline, call_n8n_batch, test_n8n_connection, call_local_pipeline, N8N_WEBHOOK_URL
    from analytics  import run_analytics, DEMO_QUESTIONS

    # ── SIDEBAR ───────────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("## 🏥 MedClaim Agent")
        st.markdown("*n8n Sumopod + Streamlit + Turso*")
        st.divider()

        # N8N URL
        st.markdown("**🔗 n8n Webhook URL**")
        n8n_url_input = st.text_input(
            "Production URL dari Sumopod",
            value=st.session_state["n8n_url"],
            type="password",
            help="Klik Webhook Trigger di Sumopod → copy Production URL → Publish workflow"
        )
        if n8n_url_input:
            st.session_state["n8n_url"] = n8n_url_input
            os.environ["N8N_WEBHOOK_URL"] = n8n_url_input
            import n8n_client
            n8n_client.N8N_WEBHOOK_URL = n8n_url_input

        # Connection status
        if st.session_state["n8n_url"]:
            col_test, col_status = st.columns([1,1])
            with col_test:
                if st.button("Test", key="test_n8n"):
                    with st.spinner("Testing..."):
                        r = test_n8n_connection()
                    if "error" not in r:
                        st.session_state["n8n_ok"] = True
                    else:
                        st.session_state["n8n_ok"] = False
                        st.session_state["n8n_err"] = r.get("error","")
            with col_status:
                if st.session_state.get("n8n_ok"):
                    st.markdown("🟢 Connected")
                elif st.session_state.get("n8n_ok") == False:
                    st.markdown("🔴 Error")
        else:
            st.markdown('<div class="warn-card">Isi webhook URL dari Sumopod</div>', unsafe_allow_html=True)

        st.divider()
        # Groq key (untuk analytics agent lokal)
        st.markdown("**🤖 Groq API Key**")
        groq_input = st.text_input(
            "Untuk Analytics Agent",
            value=st.session_state["groq_key"],
            type="password",
            help="console.groq.com — gratis, tidak perlu kartu kredit"
        )
        if groq_input:
            st.session_state["groq_key"] = groq_input
            os.environ["GROQ_API_KEY"] = groq_input
            import analytics
            analytics.GROQ_API_KEY = groq_input

        st.divider()
        # DB info
        db_type = "🟣 Turso (cloud)" if USE_TURSO else "🔵 SQLite (local)"
        st.markdown(f"**💾 Database:** {db_type}")
        st.divider()

        # Pipeline flow
        st.markdown("**Pipeline Flow**")
        st.markdown("""
        ```
        Streamlit
            │ POST record
            ▼
            n8n Sumopod
            ├─ Preprocessor
            ├─ Groq Extractor
            ├─ FMR Validator
            ├─ Fraud Monitor
            └─ Format Output
            │ JSON result
            ▼
            Streamlit + DB
         ```
         """)


    # Lazy load database dengan error handling
    db = get_db_connection()
    stats = get_initial_stats(db)
    
    # ── HEADER ────────────────────────────────────────────────────────────────────
    st.title("🏥 MedClaim Insight Agent")
    st.markdown("*Agentic pipeline: n8n Sumopod sebagai backend brain → Groq LLM → FMR validation → fraud detection*")

    # KPI
    # stats = get_stats(db)
    stats = get_initial_stats(db)
    try:
        stats = get_stats(db) if db else {"total": 0, "approved": 0, "review": 0, "fraud": 0, "avg_fmr": None}
    except Exception as e:
        st.warning(f"⚠️ Failed to load stats: {e}")
        stats = {"total": 0, "approved": 0, "review": 0, "fraud": 0, "avg_fmr": None}

    c1,c2,c3,c4,c5,c6 = st.columns(6)
    c1.metric("Total Diproses", stats["total"])
    c2.metric("Avg FMR", f"{stats['avg_fmr']:.1%}" if stats["avg_fmr"] else "—")
    c3.metric("✅ Approved", stats["approve"])
    c4.metric("🟡 Review", stats["review"])
    c5.metric("❌ Rejected", stats["reject"])
    c6.metric("🚨 High Risk", stats["high_risk"])

    # Footer
    st.markdown("---")
    st.caption(
        "🏥 MedClaim Insight Agent v4 | Built by Rachma for Novo AI Portfolio | May 2026"
    )

    st.divider()

    # ── TABS ──────────────────────────────────────────────────────────────────────
    tab_run, tab_analytics, tab_fmr, tab_history, tab_arch = st.tabs([
        "🚀 Run Pipeline",
        "📊 Analytics Agent",
        "📈 FMR Tracker",
        "📋 History",
        "🏗️ Architecture",
    ])

    # ══════════════════════════════════════════════════════════════════════════════
    # TAB 1 — RUN PIPELINE
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_run:
        st.subheader("Upload Dataset & Run via n8n Sumopod")

        col_up, col_info = st.columns([1, 1])

        with col_up:
            uploaded = st.file_uploader(
                "Upload CSV / XLSX — Mendeley Health Insurance Dataset",
                type=["csv","xlsx"],
                help="Dataset: doi.org/10.17632/386vmj2tbk.4 (CC BY 4.0, gratis)"
            )
            if uploaded:
                df = pd.read_excel(uploaded) if uploaded.name.endswith(".xlsx") else pd.read_csv(uploaded)
                st.session_state["dataset"] = df
                st.success(f"✅ {len(df):,} baris × {len(df.columns)} kolom dimuat")

            n_sample = st.slider("Records per batch", 1, 15, 3,
                             help="Mulai dari 1-3 untuk test. Setiap record = 1 call ke Groq via n8n.")

            use_n8n = st.toggle("Gunakan n8n Sumopod", value=bool(st.session_state["n8n_url"]),
                            help="Off = local Python fallback (butuh GROQ_API_KEY)")

            can_run = (
                st.session_state.get("dataset") is not None and
                (st.session_state["n8n_url"] or st.session_state["groq_key"])
            )
            run_btn = st.button("▶ Jalankan Pipeline", type="primary", disabled=not can_run)

        with col_info:
            if not st.session_state["n8n_url"]:
                st.markdown('<div class="warn-card"><strong>Cara connect ke Sumopod:</strong><br>'
                        '1. Buka workflow "MedClaim Insight Pipeline" di Sumopod<br>'
                        '2. Klik node "Webhook Trigger"<br>'
                        '3. Copy <strong>Production URL</strong><br>'
                        '4. Klik <strong>Publish</strong> (kanan atas)<br>'
                        '5. Paste URL di sidebar kiri</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="info-card"><strong>n8n Sumopod terhubung.</strong><br>'
                        'Setiap record dikirim ke: Preprocessor → Groq Extractor → FMR → Monitor → response</div>',
                        unsafe_allow_html=True)

            st.markdown("**Mode operasi:**")
            if use_n8n and st.session_state["n8n_url"]:
                st.info("🔗 **n8n Mode**: Record dikirim ke Sumopod webhook. Groq berjalan di n8n cloud.")
            else:
                st.warning("💻 **Local Mode**: Pipeline berjalan di Python lokal. Butuh GROQ_API_KEY.")

        # ── RUN ───────────────────────────────────────────────────────────────────
        if run_btn:
            df = st.session_state["dataset"]
            sample = df.sample(min(n_sample, len(df)), random_state=int(time.time())%1000)
            records = sample.fillna("").to_dict("records")
            # Sel kosong diganti "" → JSON berhasil ✅
            batch_id = str(uuid.uuid4())[:8]

            st.markdown(f"#### Memproses {len(records)} records via {'n8n Sumopod' if use_n8n else 'Local Python'}...")

            progress_bar = st.progress(0)
            status_text  = st.empty()
            results_container = st.container()
            all_results  = []
            fmr_scores   = []

            for i, rec in enumerate(records):
                status_text.text(f"Record {i+1}/{len(records)} — menunggu n8n response...")

                t_start = time.time()
                if use_n8n and st.session_state["n8n_url"]:
                    result = call_n8n_pipeline(rec)
                else:
                    result = call_local_pipeline(rec)

                # Normalize fields dari n8n response
                if "error" not in result:
                    fmr = result.get("fmr") or result.get("overall_confidence") or 0
                    result["fmr"]           = fmr
                    result["risk_flag"]     = result.get("risk_flag") or (result.get("extracted",{}).get("risk_flag") if isinstance(result.get("extracted"),dict) else "UNKNOWN") or "UNKNOWN"
                    result["recommendation"]= result.get("recommendation","review")
                    result["loss_ratio"]    = result.get("loss_ratio") or 0
                    result["ground_truth"]  = {
                        "policy_id": str(rec.get("ID_policy","")),
                        "premium": safe_float(rec.get("premium",0)),
                        "cost_claims_year": safe_float(rec.get("cost_claims_year",0)),
                        "n_medical_services": int(rec.get("n_medical_services",0)),
                        "age": int(rec.get("age",0)),
                        "gender": str(rec.get("gender","")),
                    }
                    fmr_scores.append(fmr)
                    save_result(db, result)

                all_results.append(result)
                progress_bar.progress((i+1)/len(records))

            status_text.text("✅ Selesai!")
            avg_fmr = round(sum(fmr_scores)/len(fmr_scores), 4) if fmr_scores else 0
            if fmr_scores:
                log_batch_fmr(db, batch_id, len(fmr_scores), avg_fmr)

            # ── SUMMARY ───────────────────────────────────────────────────────────
            st.success(f"✅ {len(records)} records | Avg FMR: {avg_fmr:.1%} | Batch: {batch_id}")

            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("Avg FMR", f"{avg_fmr:.1%}", delta="Target 85%+")
            sc2.metric("Approved", sum(1 for r in all_results if r.get("recommendation")=="approve"))
            sc3.metric("Review", sum(1 for r in all_results if r.get("recommendation")=="review"))
            sc4.metric("Errors", sum(1 for r in all_results if "error" in r))

            # ── DETAIL PER RECORD ──────────────────────────────────────────────────
            st.markdown("#### Hasil per Record")
            for idx, r in enumerate(all_results):
                if "error" in r:
                    st.error(f"❌ Record {idx+1}: {r['error']}")
                    continue

                    fmr = r.get("fmr",0)
                    rec_val = r.get("recommendation","review")
                    icon = "🟢" if fmr>=0.85 else ("🟡" if fmr>=0.60 else "🔴")
                    risk = r.get("risk_flag","?")

                with st.expander(f"{icon} Record {idx+1} — FMR: {fmr:.1%} | {rec_val.upper()} | Risk: {risk} | {r.get('elapsed_ms',0)}ms"):
                    cl, cr = st.columns(2)
                    gt = r.get("ground_truth",{})
                    ex = r.get("extracted",{}) if isinstance(r.get("extracted"),dict) else r

                    with cl:
                        st.markdown("**📋 Ground Truth (CSV)**")
                        st.json({k:v for k,v in gt.items() if k != "messy_text"})

                    with cr:
                        st.markdown("**🤖 Extracted by Groq LLM (via n8n)**")
                        display_ex = {k:v for k,v in ex.items()
                                  if k not in ("_validation_warning",) and v is not None}
                        st.json(display_ex)

                    # FMR detail bars
                    fmr_detail = r.get("fmr_detail",{})
                    if fmr_detail:
                        st.markdown("**FMR Field Breakdown:**")
                        fcols = st.columns(len(fmr_detail))
                        for fi, (field, ok) in enumerate(fmr_detail.items()):
                            fcols[fi].metric(field, "✅" if ok else "❌")

                    # Monitoring alerts
                    mon = r.get("monitoring",{})
                    if isinstance(mon, dict) and mon.get("alerts"):
                        st.markdown("**🔍 Monitoring Alerts:**")
                        for alert in mon["alerts"]:
                            sev = alert.get("severity","LOW")
                            msg = alert.get("message","")
                            if sev=="HIGH":    st.error(f"🔴 {msg}")
                            elif sev=="MEDIUM": st.warning(f"🟡 {msg}")
                            else:               st.info(f"🔵 {msg}")
                    else:
                        st.success("✅ Tidak ada anomali")

                    # Raw n8n response
                    with st.expander("Raw n8n response JSON"):
                        st.json({k:v for k,v in r.items() if k!="ground_truth"})

            # ── BATCH MONITORING SUMMARY ───────────────────────────────────────────
            all_alerts = []
            for r in all_results:
                mon = r.get("monitoring",{})
                if isinstance(mon,dict):
                    all_alerts.extend(mon.get("alerts",[]))

            if all_alerts:
                st.markdown("#### 🔍 Batch Monitoring Summary")
                high = [a for a in all_alerts if a.get("severity")=="HIGH"]
                med  = [a for a in all_alerts if a.get("severity")=="MEDIUM"]
                ma,mb,mc = st.columns(3)
                ma.metric("🔴 High Severity", len(high))
                mb.metric("🟡 Medium", len(med))
                mc.metric("Total Alerts", len(all_alerts))


    # ══════════════════════════════════════════════════════════════════════════════
    # TAB 2 — ANALYTICS AGENT
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_analytics:
        st.subheader("📊 Analytics Agent — Natural Language → SQL → Business Insight")

        if not st.session_state["groq_key"]:
            st.warning("⚠️ Isi GROQ_API_KEY di sidebar untuk Analytics Agent")
            st.stop()

        # Upload dataset untuk analytics
        uploaded_a = st.file_uploader("Upload dataset untuk analytics",
                                   type=["csv","xlsx"], key="ana_upload")
        if uploaded_a:
            df_a = pd.read_excel(uploaded_a) if uploaded_a.name.endswith(".xlsx") else pd.read_csv(uploaded_a)
            st.session_state["ana_dataset"] = df_a
            st.success(f"Dataset: {len(df_a):,} baris dimuat untuk analytics")
        elif st.session_state.get("dataset") is not None:
            df_a = st.session_state["dataset"]
            st.info("Menggunakan dataset yang sama dengan pipeline tab.")
        else:
            df_a = None
            st.info("Upload dataset terlebih dahulu.")

        if df_a is not None:
            st.markdown("**Contoh pertanyaan (klik untuk isi):**")
            q_cols = st.columns(2)
            for qi, q in enumerate(DEMO_QUESTIONS):
                with q_cols[qi % 2]:
                    if st.button(q[:55]+"...", key=f"dq_{qi}"):
                        st.session_state["ana_question"] = q

            question = st.text_area(
                "Tanyakan apa saja tentang data klaim:",
                value=st.session_state.get("ana_question",""),
                height=80, placeholder="Contoh: Mengapa biaya klaim naik dari 2017 ke 2019?"
            )

            if st.button("🔍 Analisis", type="primary", disabled=not question):
                with st.spinner("Analytics Agent berpikir (NL→SQL→Insight)..."):
                    res = run_analytics(question, df_a, db)

                if "error" in res:
                    st.error(f"Error: {res['error']}")
                else:
                    st.markdown("### 💡 Business Insight")
                    st.success(res["narrative"])

                    ia, ib = st.columns(2)
                    with ia:
                        st.markdown("**Hypothesis:**")
                        st.write(res.get("hypothesis",""))
                    with ib:
                        st.markdown("**Generated SQL:**")
                        st.code(res["sql"], language="sql")

                    if res.get("result"):
                        st.markdown("**Query Result:**")
                        rdf = pd.DataFrame(res["result"])
                        st.dataframe(rdf, use_container_width=True)

                        num_cols = rdf.select_dtypes(include="number").columns.tolist()
                        str_cols = rdf.select_dtypes(include="object").columns.tolist()
                        if num_cols and str_cols:
                            fig = px.bar(rdf, x=str_cols[0], y=num_cols[0],
                                     title=question[:70],
                                     color_discrete_sequence=["#3b82f6"])
                            st.plotly_chart(fig, use_container_width=True)
                        elif len(num_cols) >= 2:
                            fig = px.scatter(rdf, x=num_cols[0], y=num_cols[1],
                                         title=question[:70])
                            st.plotly_chart(fig, use_container_width=True)


    # ══════════════════════════════════════════════════════════════════════════════
    # TAB 3 — FMR TRACKER
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_fmr:
        st.subheader("📈 FMR Tracker — Self-Improving Agent Demo")
        st.markdown("""
        Setiap batch yang dijalankan otomatis dicatat FMR-nya ke database.
        Perbaiki prompt di n8n Agent 2, jalankan batch baru → chart menunjukkan improvement.
        **Ini adalah "self-improving agent loop" yang dicari Novo AI.**
        """)

        fmr_hist = get_fmr_history(db)

        if not fmr_hist:
            st.info("Belum ada data. Jalankan pipeline di tab 'Run Pipeline' dulu.")
        else:
            fdf = pd.DataFrame(fmr_hist)
            fdf["created_at"] = pd.to_datetime(fdf["created_at"])

            # Main FMR chart
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=fdf["created_at"], y=fdf["avg_fmr"],
                mode="lines+markers+text",
                text=[f"{v:.0%}" for v in fdf["avg_fmr"]],
                textposition="top center",
                name="Avg FMR per Batch",
                line=dict(color="#3b82f6", width=2.5),
                marker=dict(size=9)
            ))
            fig.add_hline(y=0.85, line_dash="dash", line_color="#22c55e",
                      annotation_text="Target 85%", annotation_position="right")
            fig.add_hline(y=0.60, line_dash="dot", line_color="#f59e0b",
                      annotation_text="Min acceptable", annotation_position="right")
            fig.update_layout(
                title="FMR Improvement — Self-Improving Agent Pipeline",
                xaxis_title="Waktu", yaxis_title="FMR Score",
                yaxis=dict(range=[0,1.05], tickformat=".0%"),
                height=380, showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True)

            # Stats
            fc1,fc2,fc3,fc4 = st.columns(4)
            fc1.metric("Total Batches", len(fdf))
            fc2.metric("Best FMR", f"{fdf['avg_fmr'].max():.1%}")
            fc3.metric("Latest FMR", f"{fdf['avg_fmr'].iloc[-1]:.1%}")
            fc4.metric("Avg FMR All", f"{fdf['avg_fmr'].mean():.1%}")

            st.dataframe(fdf[["batch_id","batch_size","avg_fmr","created_at"]]
                     .rename(columns={"batch_id":"Batch","batch_size":"Records",
                                      "avg_fmr":"FMR","created_at":"Waktu"}),
                     use_container_width=True)

            with st.expander("📖 Cara improve FMR — Self-Improvement Loop"):
                st.markdown("""
                **Di n8n Sumopod:**
                1. Klik node "Agent 2: Extractor (Groq LLM)"
                2. Edit bagian `system` prompt di body request
                3. Tambahkan contoh field yang sering salah (few-shot)
                4. Save dan Publish ulang
                5. Jalankan batch baru di Streamlit
                6. Chart di atas otomatis menunjukkan improvement

                **Di local Python (fallback):**
                1. Edit `EXTRACTION_PROMPT` di `n8n_client.py`
                2. Jalankan batch baru
                3. Bandingkan FMR sebelum dan sesudah

                **Target iterasi:**
                - Batch 1: baseline ~65-75%
                - Batch 2 (setelah perbaiki prompt): ~75-85%
                - Batch 3 (few-shot examples): >85% ✅
                """)


    # ══════════════════════════════════════════════════════════════════════════════
    # TAB 4 — HISTORY
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_history:
        st.subheader("📋 Extraction History")
        all_rows = get_all_results(db)

        if not all_rows:
            st.info("Belum ada data. Jalankan pipeline dulu.")
        else:
            rows_display = []
            for r in all_rows:
                rows_display.append({
                    "ID":          r["id"][:8]+"...",
                    "FMR":         f"{r['fmr']:.1%}" if r.get("fmr") else "—",
                    "Risk":        r.get("risk_flag","?"),
                    "Status":      r.get("recommendation","?"),
                    "Loss Ratio":  f"{r['loss_ratio']:.2f}" if r.get("loss_ratio") else "—",
                    "Source":      r.get("source","?"),
                    "ms":          r.get("elapsed_ms","?"),
                    "Waktu":       r.get("created_at","?")[:16],
                })
            hist_df = pd.DataFrame(rows_display)
            st.dataframe(hist_df, use_container_width=True)

            h1, h2 = st.columns(2)
            with h1:
                rc = hist_df["Status"].value_counts().reset_index()
                rc.columns = ["Status","Count"]
                fig_r = px.pie(rc, names="Status", values="Count",
                           title="Recommendation Distribution",
                           color_discrete_map={"approve":"#22c55e","review":"#f59e0b","reject":"#ef4444"})
                st.plotly_chart(fig_r, use_container_width=True)
            with h2:
                rk = hist_df["Risk"].value_counts().reset_index()
                rk.columns = ["Risk","Count"]
                fig_k = px.bar(rk, x="Risk", y="Count", title="Risk Flag Distribution",
                           color="Risk",
                           color_discrete_map={"HIGH":"#ef4444","MEDIUM":"#f59e0b",
                                               "LOW":"#22c55e","ERROR":"#94a3b8","UNKNOWN":"#94a3b8"})
                st.plotly_chart(fig_k, use_container_width=True)

            if st.button("🗑 Clear History", type="secondary"):
                db.execute("DELETE FROM extractions")
                db.execute("DELETE FROM fmr_log")
                db.commit()
                st.rerun()


    # ══════════════════════════════════════════════════════════════════════════════
    # TAB 5 — ARCHITECTURE
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_arch:
        st.subheader("🏗️ System Architecture")

        st.markdown("""
        ## End-to-End Architecture

        ```
        ┌─────────────────────────────────────────────────────────────────┐
        │  FRONTEND — Streamlit (HuggingFace Spaces, free)                │
        │  • Upload CSV dataset (228k baris Mendeley Health Insurance)    │
        │  • Kirim 1 record per waktu ke n8n via POST                     │
        │  • Tampilkan hasil: FMR, risk flag, monitoring alerts           │
        │  • Analytics: NL question → chart + insight                     │
        └─────────────────────┬───────────────────────────────────────────┘
                          │ POST /webhook/medclaim-extract
                          │ { "record": { ...csv_row... } }
                          ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │  BACKEND BRAIN — n8n Sumopod (cloud, free tier)                 │
        │                                                                  │
        │  1. Webhook Trigger  (receive POST)                              │
        │  2. Agent 1: Preprocessor                                        │
        │     CSV row → cleaned dict → messy_text naratif                  │
        │     (simulasi dokumen invoice / catatan klinik tidak terstruktur)│
        │  3. Agent 2: Extractor (Groq LLM qwen/qwen3-30b)                │
        │     POST https://api.groq.com/openai/v1/chat/completions        │
        │     messy_text → JSON terstruktur (Pydantic schema)             │
        │  4. Agent 3: Validator FMR                                       │
        │     Compare extracted vs ground truth → FMR score               │
        │     approve (>85%) / review (60-85%) / reject (<60%)            │
        │  5. Agent 4: Monitor Fraud                                       │
        │     Loss ratio > 2x, utilisasi > 80 services, duplicates        │
        │  6. Format Output → JSON response ke Streamlit                  │
        └─────────────────────┬───────────────────────────────────────────┘
                          │ JSON result
                          ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │  DATABASE — Turso (cloud edge) / SQLite (local fallback)        │
        │  • extractions: semua hasil extraction + FMR per record         │
        │  • fmr_log: tracking FMR per batch (self-improvement chart)     │
        │  • analytics_log: history NL questions + SQL queries            │
        └─────────────────────────────────────────────────────────────────┘
        ```

        ## Alignment dengan Novo AI

        | Novo AI Requirement | Implementasi |
        |---------------------|-------------|
        | Extract dari dokumen messy | n8n Agent 1+2: CSV→messy text→JSON via Groq |
        | Business analytics otomatis | Agent 5: NL→SQL→narrative insight |
        | Self-improving agent | FMR tracker + n8n prompt versioning |
        | Fraud/anomaly detection | n8n Agent 4: loss ratio + utilization rules |
        | 80% manual work reduction | Demo: 6 jam analisis manual → 3 menit |
        | Healthcare domain knowledge | ICD-aware, medical services, loss ratio |

        ## Database Decision: Turso vs SQLite

        **SQLite** (default, zero config):
        - Cukup untuk HuggingFace Spaces demo
        - Data hilang saat app restart (HF Spaces ephemeral)
        - Ideal untuk portfolio showcase

        **Turso** (upgrade, free 8GB):
        - Data persists antar session dan deploy
        - Edge database — latency rendah global
        - Sudah ada di kode Emergent (lib/turso.js)
        - **Direkomendasikan jika apply ke Novo AI** — menunjukkan production thinking

        ## Linkup.so — Apakah Berguna?

        **Linkup.so** adalah search API untuk AI agents (real-time web search).
        Untuk project ini: **tidak diperlukan** karena data sudah dari dataset lokal.
        Akan berguna jika ditambahkan fitur:
        - Real-time medical price lookup dari web
        - Verifikasi provider/dokter dari internet
        - Enrichment data klaim dengan info publik

        Free tier Linkup: 200 searches/bulan. Cukup untuk demo satu fitur.
        """)

        st.markdown("---")
        st.caption("MedClaim Insight Agent v4 | Built for Novo AI Portfolio Application")

# Run the app
if __name__ == "__main__":
    main()
