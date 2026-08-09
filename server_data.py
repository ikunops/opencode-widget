#!/usr/bin/env python3
"""抓取 opencode.ai 服务端数据 (usage.list 明细 + getCosts 月度汇总 + /go 配额快照) 并落库。"""
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import quote

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "server_usage.db")
GO_ENDPOINT_BASE = "https://opencode.ai/workspace/"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

USAGE_LIST_HASH = "bfd684bfc2e4eed05cd0b518f5e4eafd3f3376e3938abb9e536e7c03df831e5c"
GET_COSTS_HASH = "15702f3a12ff8bff357f8c2aa154a17e65b746d5f6b96adc9002c86ee0c15205"
USAGE_PAGE_SIZE = 50


class SerovalParser:
    """解析 SolidStart server function 返回的 Seroval 流 (subset)。"""

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
            if self.s[self.i] == ")":
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
            raise ValueError("bad null at %d: %s" % (self.i, self.s[self.i:self.i + 20]))
        if c == "t":
            if self.s.startswith("true", self.i):
                self.i += 4
                return True
            raise ValueError("bad")
        if c == "f":
            if self.s.startswith("false", self.i):
                self.i += 5
                return False
            raise ValueError("bad")
        if c == "!":
            if self.s.startswith("!0", self.i):
                self.i += 2
                return False
            if self.s.startswith("!1", self.i):
                self.i += 2
                return True
            raise ValueError("bad")
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
            if self.s[self.i] == "]":
                self.i += 1
                return out
            if self.s[self.i] == ",":
                self.i += 1
                continue
            out.append(self.parse_any())

    def parse_object(self):
        self.i += 1
        out = {}
        while True:
            self.ws()
            if self.s[self.i] == "}":
                self.i += 1
                return out
            if self.s[self.i] == ",":
                self.i += 1
                continue
            if self.s[self.i] == '"':
                key = self.parse_string()
            else:
                j = self.s.find(":", self.i)
                if j < 0:
                    raise ValueError("no colon for bare key at %d" % self.i)
                key = self.s[self.i:j]
                self.i = j
            self.ws()
            if self.s[self.i] == ":":
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


def call_rpc(fn_hash, args, auth_cookie, timeout=25):
    url = ("https://opencode.ai/_server?id=%s&args=%s" % (fn_hash, quote(__import__("json").dumps(args))))
    req = Request(url, headers={
        "Cookie": "auth=%s" % auth_cookie,
        "User-Agent": USER_AGENT,
        "Accept": "text/x-component, application/json, text/html, */*",
        "X-Server-Id": fn_hash,
        "X-Server-Instance": "server-fn:0",
    })
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def init_db():
    conn = sqlite3.connect(DB_PATH)
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
            fetched_at INTEGER
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
    conn.commit()
    return conn


def fetch_usage_list(auth_cookie, workspace_id, max_pages=20, known_ids=None):
    """翻页抓取 usage.list 明细 (增量)。已知 id 集合用于提前停止 (遇到已知 id 即认为已同步过)。
    返回 (records, error)。"""
    records = []
    page = 0
    known = known_ids or set()
    while page < max_pages:
        txt = call_rpc(USAGE_LIST_HASH, [workspace_id, page], auth_cookie)
        data = SerovalParser(txt).parse_stream()
        if not isinstance(data, list) or not data:
            break
        page_new = [r for r in data if r.get("id") not in known]
        records.extend(page_new)
        if len(page_new) < len(data) or len(data) < USAGE_PAGE_SIZE:
            break
        page += 1
    return records, None


def known_ids(conn, limit=5000):
    try:
        rows = conn.execute(
            "SELECT id FROM usage_records ORDER BY time_created DESC LIMIT ?", (limit,)).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def store_usage_records(conn, records, workspace_id, fetched_at):
    n = 0
    for r in records:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO usage_records (id, workspace_id, time_created, time_updated, "
                "model, provider, input_tokens, output_tokens, reasoning_tokens, cache_read_tokens, "
                "cache_write_5m_tokens, cache_write_1h_tokens, cost, key_id, session_id, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.get("id"), workspace_id,
                 _to_ms(r.get("timeCreated")), _to_ms(r.get("timeUpdated")),
                 r.get("model"), r.get("provider"),
                 r.get("inputTokens"), r.get("outputTokens"), r.get("reasoningTokens"),
                 r.get("cacheReadTokens"), r.get("cacheWrite5mTokens"), r.get("cacheWrite1hTokens"),
                 r.get("cost"), r.get("keyID"), r.get("sessionID"), fetched_at))
            n += 1
        except Exception:
            pass
    conn.commit()
    return n


def fetch_costs(auth_cookie, workspace_id, year=None, month=None):
    now = datetime.now(timezone.utc)
    year = year if year is not None else now.year
    month = month if month is not None else now.month - 1
    for tz in (-480, 480):
        try:
            txt = call_rpc(GET_COSTS_HASH, [workspace_id, year, month, tz], auth_cookie)
            data = SerovalParser(txt).parse_stream()
            if isinstance(data, dict) and data.get("usage"):
                return data, None
        except Exception:
            continue
    return None, "getCosts 请求失败"


def store_costs(conn, data, workspace_id, year, month, fetched_at):
    n = 0
    for u in (data.get("usage") or []):
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cost_summary (workspace_id, year, month, model, key_id, plan, total_cost, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (workspace_id, year, month, u.get("model"), u.get("keyId"),
                 u.get("plan"), u.get("totalCost"), fetched_at))
            n += 1
        except Exception:
            pass
    conn.commit()
    return n


def store_quota_snapshot(conn, windows, workspace_id, fetched_at):
    n = 0
    for w in windows:
        try:
            conn.execute(
                "INSERT INTO quota_snapshot (fetched_at, kind, label, pct, reset_text, workspace_id) "
                "VALUES (?,?,?,?,?,?)",
                (fetched_at, w.get("kind"), w.get("label"), w.get("pct"), w.get("reset_text"), workspace_id))
            n += 1
        except Exception:
            pass
    conn.commit()
    return n


def read_server_rows():
    """读取 usage_records 全部明细，转成与本地 rows 相同的结构。
    返回 [{ts, cost(USD), model, tokens, src, key_id}]，cost 已从 1e8 原始单位换算为美元。"""
    if not os.path.exists(DB_PATH):
        return []
    rows = []
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
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


def read_cost_map():
    """从 cost_summary 读取整月权威成本（跨 key 合并）: {model: usd}。"""
    if not os.path.exists(DB_PATH):
        return {}
    cm = {}
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        for model, total in conn.execute(
                "SELECT model, SUM(total_cost) FROM cost_summary GROUP BY model").fetchall():
            cm[model] = (total or 0) / 1e8
        conn.close()
    except Exception:
        pass
    return cm


def sync_all(auth_cookie, workspace_id, windows=None, year=None, month=None, max_pages=130, full=False):
    """一次性同步: usage.list 增量 + getCosts 当月 + 配额快照。返回结果摘要 dict。
    max_pages 默认 130: 首次全量回填 (服务器窗口约 124 页), 之后依赖 known_ids 增量提前停止。
    full=True 时忽略 known_ids 强制翻到服务器尽头, 用于补齐历史缺口。"""
    result = {"usage_list": 0, "costs": 0, "quota": 0, "errors": []}
    if not auth_cookie or not workspace_id:
        result["errors"].append("缺少 auth cookie 或 workspace ID")
        return result
    conn = init_db()
    fetched_at = int(time.time() * 1000)
    try:
        records, err = fetch_usage_list(auth_cookie, workspace_id, max_pages=max_pages,
                                        known_ids=None if full else known_ids(conn))
        if err:
            result["errors"].append(err)
        else:
            result["usage_list"] = store_usage_records(conn, records, workspace_id, fetched_at)

        data, err = fetch_costs(auth_cookie, workspace_id, year, month)
        if err:
            result["errors"].append(err)
        else:
            y = year if year is not None else datetime.now(timezone.utc).year
            m = month if month is not None else datetime.now(timezone.utc).month - 1
            result["costs"] = store_costs(conn, data, workspace_id, y, m, fetched_at)

        if windows:
            result["quota"] = store_quota_snapshot(conn, windows, workspace_id, fetched_at)
    finally:
        conn.close()
    return result


if __name__ == "__main__":
    # 自测: 从主脚本读 cookie (避免循环依赖, 直接调用主脚本函数)
    sys_path = os.path.join(APP_DIR)
    sys_mod = __import__("importlib").import_module("go-usage-widget")
    ck = sys_mod.read_auth_cookie_from_webdata()
    ws = (sys_mod.load_config().get("server") or {}).get("workspace_id") or ""
    print("cookie:", bool(ck), "ws:", ws)
    if ck and ws:
        r = sync_all(ck, ws)
        import json as _j
        print(_j.dumps(r, ensure_ascii=False, indent=2))
        con = sqlite3.connect(DB_PATH)
        for t in ("usage_records", "cost_summary", "quota_snapshot"):
            try:
                print(t, con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
            except Exception as e:
                print(t, "ERR", e)
        con.close()
