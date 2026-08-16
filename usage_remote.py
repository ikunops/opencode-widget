#!/usr/bin/env python3
"""独立本地库：存储从 opencode.ai 官网抓取的全部 usage 数据。
与 server_usage.db 分离，因为官网数据结构与本地 opencode.db 不同。
支持增量同步（基于本地最新 time_created 补齐）。
"""
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import quote

APP_DIR = os.path.dirname(os.path.abspath(__file__))
REMOTE_DB = os.path.join(APP_DIR, "usage_remote.db")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

USAGE_LIST_HASH = "bfd684bfc2e4eed05cd0b518f5e4eafd3f3376e3938abb9e536e7c03df831e5c"
GET_COSTS_HASH = "15702f3a12ff8bff357f8c2aa154a17e65b746d5f6b96adc9002c86ee0c15205"
USAGE_PAGE_SIZE = 50


def _to_ms(iso):
    if not iso:
        return None
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        try:
            dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _now_ms():
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Seroval parser (subset, same as server_data.py)
# ---------------------------------------------------------------------------
class SerovalParser:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.refs = {}

    def ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1

    def parse_stream(self):
        idx = self.s.rfind("$R[0]=")
        if idx < 0:
            idx = self.s.find("$R[0]=")
        if idx < 0:
            raise ValueError("no $R[0]=")
        self.i = idx + len("$R[0]=")
        self.ws()
        return self.parse_any()

    def parse_any(self):
        self.ws()
        c = self.s[self.i] if self.i < len(self.s) else ""
        if c == "n" and self.s.startswith("new Date(", self.i):
            self.i += 9
            self.ws()
            ds = self.parse_string()
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == ")":
                self.i += 1
            return ds
        if c == "[":
            return self.parse_array()
        if c == "{":
            return self.parse_object()
        if c == '"':
            return self.parse_string()
        if c == "n":
            if self.s.startswith("null", self.i):
                self.i += 4
                return None
            raise ValueError("bad null")
        if c == "t":
            if self.s.startswith("true", self.i):
                self.i += 4
                return True
            raise ValueError("bad true")
        if c == "f":
            if self.s.startswith("false", self.i):
                self.i += 5
                return False
            raise ValueError("bad false")
        if c == "!":
            if self.s.startswith("!0", self.i):
                self.i += 2
                return False
            if self.s.startswith("!1", self.i):
                self.i += 2
                return True
            raise ValueError("bad !")
        if c == "$":
            m = re.match(r"\$R\[(\d+)\]", self.s[self.i:])
            if not m:
                raise ValueError("bad ref: " + self.s[self.i:self.i + 30])
            self.i += m.end()
            if self.i < len(self.s) and self.s[self.i] == "=":
                self.i += 1
                val = self.parse_any()
                self.refs[m.group(1)] = val
                return val
            if m.group(1) in self.refs:
                return self.refs[m.group(1)]
            raise ValueError("unresolved ref " + m.group(1))
        m = re.match(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", self.s[self.i:])
        if m:
            self.i += m.end()
            s = m.group(0)
            return int(s) if ("." not in s and "e" not in s.lower()) else float(s)
        raise ValueError("unknown char %r at %d: %s" % (c, self.i, self.s[self.i:self.i + 40]))

    def parse_array(self):
        self.i += 1
        out = []
        while True:
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == "]":
                self.i += 1
                return out
            if self.i < len(self.s) and self.s[self.i] == ",":
                self.i += 1
                continue
            out.append(self.parse_any())

    def parse_object(self):
        self.i += 1
        out = {}
        while True:
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == "}":
                self.i += 1
                return out
            if self.i < len(self.s) and self.s[self.i] == ",":
                self.i += 1
                continue
            if self.i < len(self.s) and self.s[self.i] == '"':
                key = self.parse_string()
            else:
                j = self.s.find(":", self.i)
                if j < 0:
                    raise ValueError("no colon for bare key at %d" % self.i)
                key = self.s[self.i:j]
                self.i = j
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == ":":
                self.i += 1
            val = self.parse_any()
            out[key] = val

    def parse_string(self):
        self.i += 1
        buf = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                nxt = self.s[self.i + 1] if self.i + 1 < len(self.s) else ""
                if nxt == "u":
                    h = self.s[self.i + 2:self.i + 6]
                    buf.append(chr(int(h, 16)))
                    self.i += 6
                    continue
                if nxt == "n":
                    buf.append("\n")
                elif nxt == "t":
                    buf.append("\t")
                elif nxt == "r":
                    buf.append("\r")
                elif nxt == '"':
                    buf.append('"')
                elif nxt == "\\":
                    buf.append("\\")
                else:
                    buf.append(nxt)
                self.i += 2
                continue
            if c == '"':
                self.i += 1
                return "".join(buf)
            buf.append(c)
            self.i += 1
        raise ValueError("unterminated string")


# ---------------------------------------------------------------------------
# RPC
# ---------------------------------------------------------------------------
def call_rpc(fn_hash, args, auth_cookie, timeout=25):
    url = "https://opencode.ai/_server?id=%s&args=%s" % (fn_hash, quote(json.dumps(args)))
    req = Request(url, headers={
        "Cookie": "auth=%s" % auth_cookie,
        "User-Agent": USER_AGENT,
        "Accept": "text/x-component, application/json, text/html, */*",
        "X-Server-Id": fn_hash,
        "X-Server-Instance": "server-fn:0",
    })
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# DB init
# ---------------------------------------------------------------------------
def init_remote_db():
    conn = sqlite3.connect(REMOTE_DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usage_records (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            time_created INTEGER,
            time_updated INTEGER,
            model TEXT,
            provider TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            reasoning_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_5m_tokens INTEGER,
            cache_write_1h_tokens INTEGER,
            cost INTEGER,
            key_id TEXT,
            session_id TEXT,
            fetched_at INTEGER,
            sync_ts INTEGER
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cost_summary (
            workspace_id TEXT,
            year INTEGER,
            month INTEGER,
            model TEXT,
            key_id TEXT,
            plan TEXT,
            total_cost INTEGER,
            fetched_at INTEGER,
            PRIMARY KEY (workspace_id, year, month, model, key_id)
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quota_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at INTEGER,
            kind TEXT,
            label TEXT,
            pct REAL,
            reset_text TEXT,
            workspace_id TEXT
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sync_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Sync meta
# ---------------------------------------------------------------------------
def get_sync_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM sync_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_sync_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()


# ---------------------------------------------------------------------------
# usage.list 明细同步
# ---------------------------------------------------------------------------
def sync_usage_list(conn, auth_cookie, workspace_id, full=False):
    """增量同步 usage.list 明细。
    full=True: 忽略已知 id，强制翻到服务器尽头（补齐历史缺口）。
    返回新增条数。"""
    max_pages = 300 if full else 130
    known = set()
    if not full:
        rows = conn.execute("SELECT id FROM usage_records ORDER BY time_created DESC LIMIT 5000").fetchall()
        known = {r[0] for r in rows}
    records = []
    page = 0
    last_ts = None
    while page < max_pages:
        txt = call_rpc(USAGE_LIST_HASH, [workspace_id, page], auth_cookie)
        data = SerovalParser(txt).parse_stream()
        if not isinstance(data, list) or not data:
            break
        new = [r for r in data if r.get("id") not in known]
        if not new:
            break
        records.extend(new)
        known.update(r.get("id") for r in new)
        last_ts = new[-1].get("timeCreated") or last_ts
        if len(new) < len(data) or len(data) < USAGE_PAGE_SIZE:
            break
        page += 1
    fetched_at = _now_ms()
    n = 0
    for r in records:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO usage_records (id, workspace_id, time_created, time_updated, "
                "model, provider, input_tokens, output_tokens, reasoning_tokens, cache_read_tokens, "
                "cache_write_5m_tokens, cache_write_1h_tokens, cost, key_id, session_id, fetched_at, sync_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.get("id"), workspace_id,
                 _to_ms(r.get("timeCreated")), _to_ms(r.get("timeUpdated")),
                 r.get("model"), r.get("provider"),
                 r.get("inputTokens"), r.get("outputTokens"), r.get("reasoningTokens"),
                 r.get("cacheReadTokens"), r.get("cacheWrite5mTokens"), r.get("cacheWrite1hTokens"),
                 r.get("cost"), r.get("keyID"), r.get("sessionID"), fetched_at, fetched_at))
            n += 1
        except Exception:
            pass
    conn.commit()
    if last_ts:
        set_sync_meta(conn, "last_usage_ts", last_ts)
    set_sync_meta(conn, "last_sync_ts", str(fetched_at))
    return n


# ---------------------------------------------------------------------------
# getCosts 月度汇总同步
# ---------------------------------------------------------------------------
def _resolve_get_costs_month(api_month):
    """getCosts 的 month 参数存在 +1 偏移（api_month=7 → 真实月份=8）。
    返回真实月份。"""
    return api_month + 1


def sync_costs(conn, auth_cookie, workspace_id, year=None, month=None):
    """同步 getCosts 月度汇总。month 若为 None，默认取 api_month=当前月-1（真实月份=当前月）。
    返回新增条数。"""
    now = datetime.now(timezone.utc)
    year = year if year is not None else now.year
    if month is None:
        real_month = now.month
        api_month = real_month - 1
        if api_month < 1:
            api_month = 12
    else:
        real_month = month
        api_month = month - 1
        if api_month < 1:
            api_month = 12

    data = None
    for tz in (-480, 480):
        try:
            txt = call_rpc(GET_COSTS_HASH, [workspace_id, year, api_month, tz], auth_cookie)
            parsed = SerovalParser(txt).parse_stream()
            if isinstance(parsed, dict) and parsed.get("usage"):
                data = parsed
                break
        except Exception:
            continue
    if not data:
        return 0
    fetched_at = _now_ms()
    n = 0
    for u in (data.get("usage") or []):
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cost_summary (workspace_id, year, month, model, key_id, plan, total_cost, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (workspace_id, year, real_month,
                 u.get("model"), u.get("keyId"), u.get("plan"),
                 u.get("totalCost"), fetched_at))
            n += 1
        except Exception:
            pass
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# quota_snapshot 同步
# ---------------------------------------------------------------------------
def sync_quota_snapshot(conn, windows, workspace_id):
    if not windows:
        return 0
    fetched_at = _now_ms()
    n = 0
    monthly_pct = None
    for w in windows:
        if w.get("kind") == "monthly":
            monthly_pct = w.get("pct")
        try:
            conn.execute(
                "INSERT INTO quota_snapshot (fetched_at, kind, label, pct, reset_text, workspace_id) "
                "VALUES (?,?,?,?,?,?)",
                (fetched_at, w.get("kind"), w.get("label"), w.get("pct"), w.get("reset_text"), workspace_id))
            n += 1
        except Exception:
            pass
    # 订阅锚点（月口径按订阅事件推）：观测到"月度重置"（pct 从接近满回到低位）时记录。
    # monthly_anchor_ts 锁定首次重置时刻，供以后修正比例用；不影响界面展示。
    if monthly_pct is not None:
        prev = conn.execute(
            "SELECT pct FROM quota_snapshot WHERE kind='monthly' AND fetched_at < ? "
            "ORDER BY fetched_at DESC LIMIT 1", (fetched_at,)).fetchone()
        if prev and prev[0] >= 90 and monthly_pct <= 40:
            set_sync_meta(conn, "monthly_last_reset", fetched_at)
            if get_sync_meta(conn, "monthly_anchor_ts") is None:
                set_sync_meta(conn, "monthly_anchor_ts", fetched_at)
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# 读取：转成与本地 rows 相同的结构
# ---------------------------------------------------------------------------
def read_remote_rows():
    """读取 usage_records 全部明细，转成与本地 rows 相同的结构。
    返回 [{ts, cost(USD), model, tokens, src, key_id}]，cost 已从 1e8 原始单位换算为美元。"""
    if not os.path.exists(REMOTE_DB):
        return []
    rows = []
    try:
        conn = sqlite3.connect(f"file:{REMOTE_DB}?mode=ro", uri=True)
        cur = conn.execute(
            "SELECT time_created, model, provider, input_tokens, output_tokens, reasoning_tokens, "
            "cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, cost, key_id "
            "FROM usage_records")
        for (ts, model, provider, tin, tout, treas, tcr, tcw5, tcw1, cost, key_id) in cur:
            if not ts or not model:
                continue
            tokens = {
                "input": tin or 0,
                "output": tout or 0,
                "cache": {"read": tcr or 0, "write": (tcw5 or 0) + (tcw1 or 0)},
                "reasoning": treas or 0,
            }
            src = "go" if (provider or "").startswith("inf-go") else "zen"
            rows.append({
                "ts": ts,
                "cost": (cost or 0) / 1e8,
                "model": model,
                "tokens": tokens,
                "src": src,
                "key_id": key_id or "",
            })
        conn.close()
    except Exception:
        pass
    return rows


def read_remote_cost_map():
    """从 cost_summary 读取整月权威成本（跨 key 合并）: {model: usd}。"""
    if not os.path.exists(REMOTE_DB):
        return {}
    cm = {}
    try:
        conn = sqlite3.connect(f"file:{REMOTE_DB}?mode=ro", uri=True)
        for model, total in conn.execute(
                "SELECT model, SUM(total_cost) FROM cost_summary GROUP BY model").fetchall():
            cm[model] = (total or 0) / 1e8
        conn.close()
    except Exception:
        pass
    return cm


def read_remote_quota():
    """读取最近一次抓取的滚动窗口百分比。"""
    if not os.path.exists(REMOTE_DB):
        return []
    try:
        conn = sqlite3.connect(REMOTE_DB)
        rows = conn.execute("""
            SELECT kind, label, pct, reset_text
            FROM quota_snapshot
            WHERE fetched_at = (SELECT MAX(fetched_at) FROM quota_snapshot)
            ORDER BY CASE kind WHEN 'session' THEN 0 WHEN 'weekly' THEN 1 ELSE 2 END
        """).fetchall()
        conn.close()
        return [{"kind": r[0], "label": r[1], "pct": r[2], "reset_text": r[3]} for r in rows]
    except Exception:
        return []


def read_remote_credits():
    """读取最近一次同步时解析到的已应用 referral credit 数。"""
    if not os.path.exists(REMOTE_DB):
        return 0
    try:
        conn = sqlite3.connect(f"file:{REMOTE_DB}?mode=ro", uri=True)
        row = conn.execute("SELECT value FROM sync_meta WHERE key='applied_credits'").fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def read_remote_latest_fetched_at():
    if not os.path.exists(REMOTE_DB):
        return 0
    try:
        conn = sqlite3.connect(f"file:{REMOTE_DB}?mode=ro", uri=True)
        row = conn.execute("SELECT MAX(fetched_at) FROM usage_records").fetchone()
        conn.close()
        return row[0] or 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Full sync entry
# ---------------------------------------------------------------------------
def full_sync(auth_cookie, workspace_id, windows=None, applied_credits=None):
    """一次性全量同步：usage.list + getCosts + quota。返回摘要 dict。"""
    result = {"usage_list": 0, "costs": 0, "quota": 0, "errors": []}
    if not auth_cookie or not workspace_id:
        result["errors"].append("缺少 auth cookie 或 workspace ID")
        return result
    conn = init_remote_db()
    try:
        n = sync_usage_list(conn, auth_cookie, workspace_id, full=True)
        result["usage_list"] = n
        n = sync_costs(conn, auth_cookie, workspace_id)
        result["costs"] = n
        if windows:
            result["quota"] = sync_quota_snapshot(conn, windows, workspace_id)
        if applied_credits is not None:
            set_sync_meta(conn, "applied_credits", applied_credits)
    except Exception as e:
        result["errors"].append(str(e))
    finally:
        conn.close()
    return result
