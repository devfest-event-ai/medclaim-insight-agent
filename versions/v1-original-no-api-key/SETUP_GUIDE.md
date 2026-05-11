# Setup Guide — MedClaim Insight Agent
## Langkah Demi Langkah: API → n8n → Python → GitHub

### LANGKAH 1: Groq API Key (5 menit, gratis)
1. Buka https://console.groq.com
2. Sign up / Login (Google account bisa)
3. Klik "API Keys" → "Create API Key"
4. Copy key: gsk_xxxxxxxxxxxxxxxxxxxx
5. Simpan — hanya muncul sekali

Model yang dipakai: qwen/qwen3-30b (via Groq endpoint)
Kenapa Groq bukan OpenAI: Groq FREE, OpenAI BERBAYAR

### LANGKAH 2: Turso Database (5 menit, gratis — opsional)
Jika skip, otomatis pakai SQLite lokal.

1. Buka https://turso.tech → Sign up
2. Install CLI: npm install -g @turso/cli
3. Login: turso auth login
4. Buat DB: turso db create medclaim
5. Ambil URL: turso db show medclaim  (copy kolom URL)
6. Buat token: turso db tokens create medclaim  (copy tokennya)

### LANGKAH 3: Setup project lokal

git clone https://github.com/USERNAME/medclaim-insight-agent
cd medclaim-insight-agent
python3 -m venv venv
source venv/bin/activate          # Mac/Linux
# venv\Scripts\activate           # Windows
pip install -r requirements.txt

### LANGKAH 4: Isi .env

cp .env.example .env
nano .env  (atau buka dengan editor)

Isi:
  N8N_WEBHOOK_URL=https://sumopod.app/webhook/XXX/medclaim-extract
  GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
  TURSO_URL=libsql://medclaim-USERNAME.turso.io      # opsional
  TURSO_AUTH_TOKEN=eyJhbGciOiJFZERTQSJ9.xxxxx       # opsional

### LANGKAH 5: Import n8n workflow ke Sumopod

1. Login ke https://sumopod.app
2. Klik "+ New Workflow"
3. Klik menu (3 titik) → "Import from JSON"
4. Upload file: n8n_workflow_qwen3.json
5. Workflow terbuka dengan 7 node

### LANGKAH 6: Setup credential Groq di Sumopod

1. Klik node "Agent 2: Qwen3-30B Extractor"
2. Klik field "Credential" → "Create New"
3. Pilih type: "Header Auth"
4. Name: "Groq API (Qwen3)"
5. Name (header): Authorization
6. Value: Bearer gsk_xxxxxxxxxxxxxxxxxxxx
   ↑ PENTING: "Bearer " + spasi + api_key kamu
7. Save

### LANGKAH 7: Publish workflow di Sumopod

1. Klik node "Webhook Trigger"
2. Tab "Parameters" → copy "Production URL"
   Contoh: https://sumopod.app/webhook/abc123/medclaim-extract
3. Paste ke .env sebagai N8N_WEBHOOK_URL
4. Klik tombol "Publish" (kanan atas, warna hijau)
5. Status berubah dari "0/1" menjadi "0/∞"

### LANGKAH 8: Test n8n dari terminal

curl -X POST "URL_WEBHOOK_KAMU" \
  -H "Content-Type: application/json" \
  -d '{
    "record": {
      "ID_policy": "TEST-001",
      "ID_insured": "INS-001",
      "period": 2019,
      "premium": 1200.00,
      "cost_claims_year": 850.00,
      "n_medical_services": 12,
      "age": 42,
      "gender": "F",
      "type_policy": "I",
      "type_product": "S",
      "reimbursement": "No",
      "distribution_channel": "A",
      "C_H": "H2",
      "C_C": "C1",
      "lapse": 2,
      "seniority_insured": 36,
      "exposure_time": 1.0
    }
  }'

Response yang diharapkan:
{
  "fmr": 0.857,
  "recommendation": "approve",
  "risk_flag": "LOW",
  "extracted": { ... }
}

### LANGKAH 9: Jalankan Streamlit lokal

streamlit run app.py

Buka: http://localhost:8501
- Isi webhook URL di sidebar
- Isi Groq API key di sidebar
- Upload dataset health_insurance.xlsx
- Klik "Jalankan Pipeline"

### LANGKAH 10: Push ke GitHub

git init
git add app.py n8n_client.py db.py analytics.py requirements.txt README.md
git add n8n_workflow_qwen3.json .env.example
# JANGAN add: .env, *.db, data/health_insurance.xlsx
git commit -m "feat: MedClaim Insight Agent v5 - 5-agent pipeline with Qwen3-30B"
git branch -M main
git remote add origin https://github.com/USERNAME/medclaim-insight-agent.git
git push -u origin main

### LANGKAH 11: Deploy ke HuggingFace Spaces

1. Buka https://huggingface.co/new-space
2. Space name: medclaim-insight-agent
3. SDK: Streamlit
4. Visibility: Public
5. Connect to GitHub: pilih repo medclaim-insight-agent
6. Klik "Create Space"
7. Tambah Secrets (Settings → Variables and secrets):
   - N8N_WEBHOOK_URL = URL webhook Sumopod kamu
   - GROQ_API_KEY    = gsk_xxxxxxxxxxxxxxxxxxxx
   - TURSO_URL       = (jika pakai Turso)
   - TURSO_AUTH_TOKEN= (jika pakai Turso)
8. App otomatis deploy dari GitHub

### VERIFIKASI AKHIR

Checklist sebelum apply ke Novo AI:
[ ] curl test ke webhook berhasil (dapat JSON response)
[ ] Streamlit lokal: pipeline jalan untuk 3 records
[ ] FMR > 0.70 minimal
[ ] Analytics agent: 1 pertanyaan berhasil dijawab
[ ] HuggingFace Spaces: public URL bisa dibuka tanpa login
[ ] GitHub repo: README dengan screenshot
[ ] n8n workflow di Sumopod: status "Published"
