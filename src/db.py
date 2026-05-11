"""
db.py — Database layer untuk MedClaim Insight
Strategi: Turso (cloud, edge) jika env tersedia, fallback ke SQLite lokal
Turso free tier: 8GB storage, gratis selamanya di turso.tech

SETUP TURSO (5 menit, gratis):
  1. Daftar di turso.tech
  2. npx turso auth login
  3. npx turso db create medclaim
  4. npx turso db show medclaim  → copy URL
  5. npx turso db tokens create medclaim → copy token
  6. Isi di .env: TURSO_URL + TURSO_AUTH_TOKEN

Tanpa Turso: semua data ke SQLite lokal (medclaim.db) otomatis.
"""

import os, json, sqlite3
from datetime import datetime

TURSO_URL   = os.getenv("TURSO_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")
USE_TURSO   = bool(TURSO_URL and TURSO_TOKEN)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS extractions (
    id              TEXT PRIMARY KEY,
    source          TEXT DEFAULT 'n8n_pipeline',
    pipeline_ver    TEXT DEFAULT '4.0',
    fmr             REAL,
    risk_flag       TEXT,
    loss_ratio      REAL,
    recommendation  TEXT,
    monitoring_json TEXT,
    output_json     TEXT,
    ground_truth_json TEXT,
    elapsed_ms      INTEGER,
    created_at      TEXT
);
CREATE TABLE IF NOT EXISTS fmr_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    TEXT,
    batch_size  INTEGER,
    avg_fmr     REAL,
    prompt_ver  INTEGER DEFAULT 1,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS analytics_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    question   TEXT,
    sql_query  TEXT,
    answer     TEXT,
    created_at TEXT
);
"""


# ── TURSO (remote) ────────────────────────────────────────────────────────────
class TursoDB:
    def __init__(self):
        import libsql_experimental as libsql
        self.conn = libsql.connect(
            database=TURSO_URL,
            auth_token=TURSO_TOKEN,
        )
        self._init_schema()

    def _init_schema(self):
        for stmt in SCHEMA_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                self.conn.execute(s)
        self.conn.commit()

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    def fetchall(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── SQLITE (local fallback) ───────────────────────────────────────────────────
class SQLiteDB:
    def __init__(self, path="medclaim.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    def fetchall(self, sql, params=()):
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


# ── Factory ───────────────────────────────────────────────────────────────────
_db_instance = None

def get_db():
    global _db_instance
    if _db_instance is None:
        if USE_TURSO:
            try:
                _db_instance = TursoDB()
                print("✅ Turso DB connected")
            except Exception as e:
                print(f"⚠️ Turso failed ({e}), fallback to SQLite")
                _db_instance = SQLiteDB()
        else:
            _db_instance = SQLiteDB()
    return _db_instance


# ── CRUD helpers ──────────────────────────────────────────────────────────────
def save_result(db, result: dict):
    """Simpan satu hasil pipeline ke DB."""
    db.execute("""
        INSERT OR REPLACE INTO extractions
        (id, source, pipeline_ver, fmr, risk_flag, loss_ratio,
         recommendation, monitoring_json, output_json, ground_truth_json,
         elapsed_ms, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        result["id"],
        result.get("source", "n8n_pipeline"),
        result.get("pipeline_version", "4.0"),
        result.get("fmr", 0),
        result.get("risk_flag", "UNKNOWN"),
        result.get("loss_ratio", 0),
        result.get("recommendation", "review"),
        json.dumps(result.get("monitoring", {})),
        json.dumps(result.get("extracted", {})),
        json.dumps(result.get("ground_truth", {})),
        result.get("elapsed_ms", 0),
        datetime.now().isoformat(),
    ))
    db.commit()


def log_batch_fmr(db, batch_id: str, batch_size: int, avg_fmr: float):
    db.execute(
        "INSERT INTO fmr_log (batch_id,batch_size,avg_fmr,created_at) VALUES (?,?,?,?)",
        (batch_id, batch_size, avg_fmr, datetime.now().isoformat())
    )
    db.commit()


def log_analytics(db, question: str, sql: str, answer: str):
    db.execute(
        "INSERT INTO analytics_log (question,sql_query,answer,created_at) VALUES (?,?,?,?)",
        (question, sql, answer, datetime.now().isoformat())
    )
    db.commit()


def get_all_results(db) -> list:
    rows = db.fetchall("SELECT * FROM extractions ORDER BY created_at DESC")
    for r in rows:
        for key in ("output_json", "ground_truth_json", "monitoring_json"):
            if r.get(key):
                try:    r[key] = json.loads(r[key])
                except: pass
    return rows


def get_fmr_history(db) -> list:
    return db.fetchall("SELECT * FROM fmr_log ORDER BY created_at ASC")


def get_stats(db) -> dict:
    rows = db.fetchall("SELECT * FROM extractions")
    if not rows:
        return {"total":0,"avg_fmr":0,"approve":0,"review":0,"reject":0,"high_risk":0}
    return {
        "total":    len(rows),
        "avg_fmr":  round(sum(r.get("fmr",0) or 0 for r in rows)/len(rows), 4),
        "approve":  sum(1 for r in rows if r.get("recommendation")=="approve"),
        "review":   sum(1 for r in rows if r.get("recommendation")=="review"),
        "reject":   sum(1 for r in rows if r.get("recommendation")=="reject"),
        "high_risk":sum(1 for r in rows if r.get("risk_flag")=="HIGH"),
    }
