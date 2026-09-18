"""SQLite-Storage: Keyword-Cache + Kosten-Ledger.

Der Cache ist der wichtigste Kostenhebel -- ein zweiter Run ueber dieselben
Keywords kostet nichts. Der Ledger protokolliert jeden API-Call mit den
tatsaechlich von der API gemeldeten Kosten, nicht mit Schaetzwerten.
"""
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS kw_cache (
    cache_key   TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    market      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    fetched_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_kw ON kw_cache(keyword, market);

CREATE TABLE IF NOT EXISTS cost_ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    ts          REAL NOT NULL,
    provider    TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    layer       TEXT NOT NULL,
    units       INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0.0,
    saved_usd   REAL NOT NULL DEFAULT 0.0,
    cached      INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_ledger_run ON cost_ledger(run_id);

CREATE TABLE IF NOT EXISTS keyword_state (
    keyword     TEXT NOT NULL,
    market      TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'new',
    note        TEXT NOT NULL DEFAULT '',
    tier        INTEGER,
    difficulty  INTEGER,
    cpc         REAL,
    volume      INTEGER,
    flags       TEXT NOT NULL DEFAULT '',
    serp_intent TEXT NOT NULL DEFAULT '',
    trend       REAL,
    subject     TEXT NOT NULL DEFAULT '',
    top_domains TEXT NOT NULL DEFAULT '',
    sure        INTEGER,
    unsure      INTEGER,
    run_id      TEXT NOT NULL DEFAULT '',
    first_seen  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (keyword, market)
);

CREATE TABLE IF NOT EXISTS pending_tasks (
    task_id     TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    provider    TEXT NOT NULL,
    path_base   TEXT NOT NULL,
    layer       TEXT NOT NULL,
    posted_at   REAL NOT NULL,
    cost_usd    REAL NOT NULL DEFAULT 0.0,
    keywords    INTEGER NOT NULL DEFAULT 0,
    fetched_at  REAL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  REAL NOT NULL,
    finished_at REAL,
    seed        TEXT,
    market      TEXT,
    status      TEXT NOT NULL DEFAULT 'running'
);
"""


# Spalten, die nach dem ersten Release dazukamen. SQLite kann nur einzeln
# ergaenzen, also wird beim Verbinden nachgezogen statt migriert.
LATE_COLUMNS = [
    ("serp_intent", "TEXT NOT NULL DEFAULT ''"),
    ("trend", "REAL"),
    ("subject", "TEXT NOT NULL DEFAULT ''"),
    ("top_domains", "TEXT NOT NULL DEFAULT ''"),
    ("sure", "INTEGER"),
    ("unsure", "INTEGER"),
    ("run_id", "TEXT NOT NULL DEFAULT ''"),
]


def connect():
    conn = sqlite3.connect(str(Config.db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    have = set(r["name"] for r in conn.execute("PRAGMA table_info(keyword_state)"))
    for name, decl in LATE_COLUMNS:
        if name not in have:
            conn.execute("ALTER TABLE keyword_state ADD COLUMN %s %s" % (name, decl))
    conn.commit()
    return conn


def now():
    return time.time()


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


class BudgetExceeded(RuntimeError):
    pass


class Run:
    """Ein Run buendelt alle Calls und erzwingt das Budget-Limit."""

    def __init__(self, seed="", budget_usd=None, conn=None):
        self.conn = conn or connect()
        self.run_id = uuid.uuid4().hex[:12]
        self.seed = seed
        self.budget = Config.run_budget_usd if budget_usd is None else budget_usd
        self.started_at = now()
        self.conn.execute(
            "INSERT INTO runs (run_id, started_at, seed, market) VALUES (?,?,?,?)",
            (self.run_id, self.started_at, seed, Config.market()),
        )
        self.conn.commit()

    # -- Ledger ----------------------------------------------------------
    def charge(self, provider, endpoint, layer, units, cost_usd, cached=False, note=None):
        """Bucht einen Call. Ein Cache-Hit kostet 0 und wird als Ersparnis gefuehrt."""
        cost_usd = float(cost_usd or 0.0)
        billed, saved = (0.0, cost_usd) if cached else (cost_usd, 0.0)
        spent = self.total_cost()
        if billed > 0 and spent + billed > self.budget:
            raise BudgetExceeded(
                "Budget-Limit erreicht: %.4f + %.4f > %.2f USD (Run %s)"
                % (spent, billed, self.budget, self.run_id)
            )
        self.conn.execute(
            "INSERT INTO cost_ledger (run_id, ts, provider, endpoint, layer, units, cost_usd,"
            " saved_usd, cached, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, now(), provider, endpoint, layer, units,
             billed, saved, 1 if cached else 0, note),
        )
        self.conn.commit()

    def would_exceed(self, cost_usd):
        return self.total_cost() + cost_usd > self.budget

    def total_cost(self):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS s FROM cost_ledger WHERE run_id=?", (self.run_id,)
        ).fetchone()
        return float(row["s"])

    def total_saved(self):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(saved_usd),0) AS s FROM cost_ledger WHERE run_id=?", (self.run_id,)
        ).fetchone()
        return float(row["s"])

    def breakdown(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT layer, provider, endpoint, COUNT(*) AS calls, SUM(units) AS units,"
            " SUM(cost_usd) AS cost, SUM(saved_usd) AS saved, SUM(cached) AS cache_hits"
            " FROM cost_ledger WHERE run_id=? GROUP BY layer, provider, endpoint"
            " ORDER BY cost DESC", (self.run_id,))]

    def finish(self, status="done"):
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=? WHERE run_id=?", (now(), status, self.run_id)
        )
        self.conn.commit()

    # -- Cache -----------------------------------------------------------
    def cache_get(self, source, keyword, market=None, ttl_days=None):
        market = market or Config.market()
        ttl = (Config.cache_ttl_days if ttl_days is None else ttl_days) * 86400
        key = "%s|%s|%s" % (source, market, keyword.lower())
        row = self.conn.execute(
            "SELECT payload, fetched_at FROM kw_cache WHERE cache_key=?", (key,)
        ).fetchone()
        if not row:
            return None
        if ttl > 0 and now() - row["fetched_at"] > ttl:
            return None
        return json.loads(row["payload"])

    def cache_put(self, source, keyword, payload, market=None):
        market = market or Config.market()
        key = "%s|%s|%s" % (source, market, keyword.lower())
        self.conn.execute(
            "INSERT OR REPLACE INTO kw_cache (cache_key, source, keyword, market, payload, fetched_at)"
            " VALUES (?,?,?,?,?,?)",
            (key, source, keyword.lower(), market, json.dumps(payload, ensure_ascii=False), now()),
        )

    # -- Async-Tasks -----------------------------------------------------
    def add_pending(self, task_id, provider, path_base, layer, cost_usd, keywords):
        """Merkt einen bezahlten Queue-Task, damit er spaeter kostenlos
        eingesammelt werden kann statt erneut gebucht zu werden."""
        self.conn.execute(
            "INSERT OR IGNORE INTO pending_tasks (task_id, run_id, provider, path_base,"
            " layer, posted_at, cost_usd, keywords) VALUES (?,?,?,?,?,?,?,?)",
            (task_id, self.run_id, provider, path_base, layer, now(),
             float(cost_usd or 0.0), int(keywords)),
        )
        self.conn.commit()

    def mark_fetched(self, task_id):
        self.conn.execute("UPDATE pending_tasks SET fetched_at=? WHERE task_id=?",
                          (now(), task_id))
        self.conn.commit()

    # -- Keyword-Zustand -------------------------------------------------
    def upsert_keyword(self, keyword, market, **fields):
        """Legt an oder aktualisiert die Messwerte. state und note bleiben
        unangetastet -- das ist die Spalte des Nutzers, nicht des Tools."""
        cols = ("tier", "difficulty", "cpc", "volume", "flags", "serp_intent",
                "trend", "subject", "top_domains", "sure", "unsure", "run_id")
        row = self.conn.execute(
            "SELECT state FROM keyword_state WHERE keyword=? AND market=?",
            (keyword, market)).fetchone()
        vals = [fields.get(c) for c in cols]
        vals = [("" if c in ("flags", "serp_intent", "subject", "top_domains", "run_id")
                 and v is None else v) for c, v in zip(cols, vals)]
        if row:
            self.conn.execute(
                "UPDATE keyword_state SET %s, updated_at=? WHERE keyword=? AND market=?"
                % ", ".join("%s=?" % c for c in cols),
                vals + [now(), keyword, market])
            return row["state"]
        self.conn.execute(
            "INSERT INTO keyword_state (keyword, market, %s, first_seen, updated_at)"
            " VALUES (?,?,%s,?,?)" % (", ".join(cols), ",".join("?" * len(cols))),
            [keyword, market] + vals + [now(), now()])
        return "new"

    def set_state(self, keyword, market, state=None, note=None):
        if state is None and note is None:
            return False
        sets, vals = [], []
        if state is not None:
            sets.append("state=?"); vals.append(state)
        if note is not None:
            sets.append("note=?"); vals.append(note)
        vals += [now(), keyword.lower(), market]
        cur = self.conn.execute(
            "UPDATE keyword_state SET %s, updated_at=? WHERE keyword=? AND market=?"
            % ", ".join(sets), vals)
        self.conn.commit()
        return cur.rowcount > 0

    def states(self, market, keywords=None):
        if keywords:
            marks = ",".join("?" * len(keywords))
            rows = self.conn.execute(
                "SELECT * FROM keyword_state WHERE market=? AND keyword IN (%s)" % marks,
                [market] + list(keywords)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM keyword_state WHERE market=?", (market,)).fetchall()
        return dict((r["keyword"], dict(r)) for r in rows)

    def commit(self):
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.finish("error" if exc_type else "done")
        self.conn.commit()
        return False


def all_runs(limit=20):
    conn = connect()
    return [dict(r) for r in conn.execute(
        "SELECT r.*, COALESCE(SUM(c.cost_usd),0) AS cost, COUNT(c.id) AS calls"
        " FROM runs r LEFT JOIN cost_ledger c ON c.run_id=r.run_id"
        " GROUP BY r.run_id ORDER BY r.started_at DESC LIMIT ?", (limit,))]


def open_tasks():
    """Bezahlte, aber noch nicht abgeholte Queue-Tasks."""
    conn = connect()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM pending_tasks WHERE fetched_at IS NULL ORDER BY posted_at")]


def set_keyword_state(keyword, market, state=None, note=None):
    """Zustand setzen ohne einen Run zu eroeffnen -- fuer das Interface."""
    if state is None and note is None:
        return False
    conn = connect()
    sets, vals = [], []
    if state is not None:
        sets.append("state=?"); vals.append(state)
    if note is not None:
        sets.append("note=?"); vals.append(note)
    vals += [now(), keyword.lower(), market]
    cur = conn.execute("UPDATE keyword_state SET %s, updated_at=? WHERE keyword=? AND market=?"
                       % ", ".join(sets), vals)
    conn.commit()
    return cur.rowcount > 0


def keyword_rows(market, state=None, limit=None):
    conn = connect()
    q = "SELECT * FROM keyword_state WHERE market=?"
    args = [market]
    if state:
        q += " AND state=?"; args.append(state)
    q += " ORDER BY (difficulty IS NULL) DESC, difficulty ASC, cpc DESC"
    if limit:
        q += " LIMIT %d" % int(limit)
    return [dict(r) for r in conn.execute(q, args)]


def state_counts(market):
    conn = connect()
    return dict((r["state"], r["n"]) for r in conn.execute(
        "SELECT state, COUNT(*) AS n FROM keyword_state WHERE market=? GROUP BY state",
        (market,)))


def lifetime_cost():
    conn = connect()
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd),0) AS s, COALESCE(SUM(saved_usd),0) AS v,"
        " COUNT(*) AS n FROM cost_ledger").fetchone()
    return float(row["s"]), float(row["v"]), int(row["n"])
