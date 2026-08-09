#!/usr/bin/env python3
import ctypes
import json
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
INDEX_PATH = os.path.join(APP_DIR, "index.html")
PYLIB = os.path.join(APP_DIR, "pylib")
if os.path.isdir(PYLIB) and PYLIB not in sys.path:
    sys.path.insert(0, PYLIB)

USERPROFILE = os.path.expanduser("~")
OPENCODE_DB = os.path.join(USERPROFILE, ".local", "share", "opencode", "opencode.db")
CODEX_LOGS = os.path.join(USERPROFILE, ".codex", "logs_2.sqlite")
OPENCODE_AUTH = os.path.join(USERPROFILE, ".local", "share", "opencode", "auth.json")
GO_ENDPOINT = "https://opencode.ai/zen/go/v1/models"
DASHBOARD_PREFIX = "https://opencode.ai/workspace/"
DASHBOARD_SUFFIX = "/go"
DASH_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

SESSION_MS = 5 * 3600 * 1000
WEEK_MS = 7 * 24 * 3600 * 1000
LIMITS = {"session": 12.0, "weekly": 30.0, "monthly": 60.0}

PRICES = {
    "grok-4.5":          {"in": 2.00, "out": 6.00, "cr": 0.30, "cw": None},
    "gpt-5.6-luna":      {"in": 0.20, "out": 1.20, "cr": 0.02, "cw": 0.25, "in_hi": 0.40, "out_hi": 1.80, "cr_hi": 0.04, "cw_hi": 0.50, "hi_above": 272000},
    "glm-5.2":           {"in": 1.40, "out": 4.40, "cr": 0.26, "cw": None},
    "glm-5.1":           {"in": 1.40, "out": 4.40, "cr": 0.26, "cw": None},
    "kimi-k3":           {"in": 3.00, "out": 15.00, "cr": 0.30, "cw": None},
    "kimi-k2.7-code":    {"in": 0.95, "out": 4.00, "cr": 0.19, "cw": None},
    "kimi-k2.6":         {"in": 0.95, "out": 4.00, "cr": 0.16, "cw": None},
    "mimo-v2.5":         {"in": 0.14, "out": 0.28, "cr": 0.0028, "cw": None},
    "mimo-v2.5-pro":     {"in": 0.435, "out": 0.87, "cr": 0.003625, "cw": None},
    "minimax-m3":        {"in": 0.30, "out": 1.20, "cr": 0.06, "cw": None},
    "minimax-m2.7":      {"in": 0.30, "out": 1.20, "cr": 0.06, "cw": 0.375},
    "qwen3.8-max":       {"in": 2.00, "out": 6.00, "cr": 0.25, "cw": 2.50},
    "qwen3.7-max":       {"in": 2.50, "out": 7.50, "cr": 0.50, "cw": 3.125},
    "qwen3.7-plus":      {"in": 0.40, "out": 1.60, "cr": 0.04, "cw": 0.50, "in_hi": 1.20, "out_hi": 4.80, "cr_hi": 0.12, "cw_hi": 1.50, "hi_above": 256000},
    "qwen3.6-plus":      {"in": 0.50, "out": 3.00, "cr": 0.05, "cw": 0.625, "in_hi": 2.00, "out_hi": 6.00, "cr_hi": 0.20, "cw_hi": 2.50, "hi_above": 256000},
    "deepseek-v4-pro":   {"in": 0.435, "out": 0.87, "cr": 0.003625, "cw": None},
    "deepseek-v4-flash": {"in": 0.14, "out": 0.28, "cr": 0.0028, "cw": None},
    "hy3":               {"in": 0.14, "out": 0.58, "cr": 0.035, "cw": None},
}

REQ_LIMITS = {
    "grok-4.5":          (120, 300, 600),
    "gpt-5.6-luna":      (2050, 5100, 10250),
    "glm-5.2":           (880, 2150, 4300),
    "glm-5.1":           (880, 2150, 4300),
    "kimi-k3":           (110, 250, 490),
    "kimi-k2.7-code":    (1350, 3380, 6750),
    "kimi-k2.6":         (1150, 2880, 5750),
    "mimo-v2.5":         (30100, 75200, 150400),
    "mimo-v2.5-pro":     (3250, 8150, 16300),
    "minimax-m3":        (3200, 8000, 16000),
    "minimax-m2.7":      (3400, 8500, 17000),
    "qwen3.8-max":       (160, 400, 810),
    "qwen3.7-max":       (340, 840, 1690),
    "qwen3.7-plus":      (4300, 10800, 21600),
    "qwen3.6-plus":      (3300, 8200, 16300),
    "deepseek-v4-pro":   (3450, 8550, 17150),
    "deepseek-v4-flash": (31650, 79050, 158150),
    "hy3":               (4300, 10750, 21500),
}

DISPLAY_NAMES = {
    "grok-4.5": "Grok 4.5", "gpt-5.6-luna": "GPT 5.6 Luna", "glm-5.2": "GLM-5.2",
    "glm-5.1": "GLM-5.1", "kimi-k3": "Kimi K3", "kimi-k2.7-code": "Kimi K2.7 Code",
    "kimi-k2.6": "Kimi K2.6", "mimo-v2.5": "MiMo-V2.5", "mimo-v2.5-pro": "MiMo-V2.5-Pro",
    "minimax-m3": "MiniMax M3", "minimax-m2.7": "MiniMax M2.7", "qwen3.8-max": "Qwen3.8 Max",
    "qwen3.7-max": "Qwen3.7 Max", "qwen3.7-plus": "Qwen3.7 Plus", "qwen3.6-plus": "Qwen3.6 Plus",
    "deepseek-v4-pro": "DeepSeek V4 Pro", "deepseek-v4-flash": "DeepSeek V4 Flash", "hy3": "Hy3",
}

FREE_SUFFIXES = ("-free", ":free", "/free")
FREE_WHITELIST = {"big-pickle"}
PROVIDER_SRC = {"opencode": "zen", "opencode-go": "go", "openkilo": "kilo"}


def is_free_model(model):
    m = (model or "").strip().lower()
    if not m:
        return False
    if m in FREE_WHITELIST:
        return True
    return any(s in m for s in FREE_SUFFIXES)


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def discover_go_key():
    try:
        with open(OPENCODE_AUTH, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = (data.get("opencode-go") or {}).get("key") or ""
        return key.strip() or None
    except Exception:
        return None


def est_cost(model, tokens):
    p = PRICES.get(model)
    if not p:
        return None
    tin = tokens.get("input", 0) or 0
    tout = tokens.get("output", 0) or 0
    tcr = (tokens.get("cache") or {}).get("read", 0) or 0
    tcw = (tokens.get("cache") or {}).get("write", 0) or 0
    hi = p.get("hi_above") and tin > p["hi_above"]
    pin = p.get("in_hi") if hi else p["in"]
    pout = p.get("out_hi") if hi else p["out"]
    pcr = p.get("cr_hi") if hi else p["cr"]
    pcw = p.get("cw_hi") if hi else p.get("cw")
    cost = tin * pin + tout * pout + tcr * pcr
    if pcw:
        cost += tcw * pcw
    return cost / 1_000_000


def read_opencode_all():
    rows = []
    if not os.path.exists(OPENCODE_DB):
        return rows
    try:
        conn = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True, timeout=15)
        cur = conn.execute(
            "select data from message where json_valid(data) "
            "and json_extract(data,'$.role')='assistant'"
        )
        for (data,) in cur:
            try:
                d = json.loads(data)
            except Exception:
                continue
            src = PROVIDER_SRC.get(d.get("providerID"))
            if not src:
                continue
            t = (d.get("time") or {}).get("created")
            if not t:
                continue
            cost = d.get("cost")
            rows.append({
                "ts": int(t),
                "cost": float(cost) if isinstance(cost, (int, float)) else 0.0,
                "model": d.get("modelID") or "?",
                "tokens": d.get("tokens") or {},
                "src": src,
            })
        conn.close()
    except Exception:
        pass
    return rows


def read_codex_logs(cursor):
    rows = []
    last_id = cursor or 0
    if not os.path.exists(CODEX_LOGS):
        return rows, last_id
    try:
        conn = sqlite3.connect(f"file:{CODEX_LOGS}?mode=ro", uri=True, timeout=15)
        cur = conn.execute(
            "select id, ts, feedback_log_body from logs "
            "where id > ? and feedback_log_body like '%response.completed%' "
            "and feedback_log_body like '%usage%'",
            (last_id,),
        )
        pat = re.compile(r'SSE event: (\{.*\})')
        max_id = last_id
        for rid, ts, body in cur:
            if rid > max_id:
                max_id = rid
            m = pat.search(body or "")
            if not m:
                continue
            try:
                ev = json.loads(m.group(1))
            except Exception:
                continue
            resp = ev.get("response") or {}
            usage = resp.get("usage") or {}
            model = resp.get("model") or "?"
            if not usage:
                continue
            det = usage.get("output_tokens_details") or {}
            tokens = {
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
                "cache": {
                    "read": ((usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0),
                    "write": ((usage.get("input_tokens_details") or {}).get("cache_write_tokens", 0) or 0),
                },
                "reasoning": det.get("reasoning_tokens", 0) or 0,
            }
            rows.append({
                "ts": int(ts) * 1000,
                "cost": est_cost(model, tokens),
                "model": model,
                "tokens": tokens,
                "src": "go",
            })
        conn.close()
        return rows, max_id
    except Exception:
        return rows, last_id


def week_bounds(now_ms):
    d = datetime.fromtimestamp(now_ms / 1000, timezone.utc)
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) - timedelta(days=d.weekday())
    end = start + timedelta(days=7)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def month_bounds(now_ms, subscribe_ms):
    a = datetime.fromtimestamp(subscribe_ms / 1000, timezone.utc)
    now = datetime.fromtimestamp(now_ms / 1000, timezone.utc)

    def anchored(y, m):
        last_day = (datetime(y + 1 if m == 12 else y, 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)).day
        return datetime(y, m, min(a.day, last_day), a.hour, a.minute, a.second, tzinfo=timezone.utc)

    y, m = now.year, now.month
    start = anchored(y, m)
    if start > now:
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        start = anchored(y, m)
    ey, em = (y, m + 1) if m < 12 else (y + 1, 1)
    return int(start.timestamp() * 1000), int(anchored(ey, em).timestamp() * 1000)


def build_windows(rows, now_ms):
    costs = [(r["ts"], r["cost"] if r["cost"] is not None else 0.0) for r in rows]
    earliest = min((t for t, _ in costs), default=now_ms)

    session_start = now_ms - SESSION_MS
    ws, we = week_bounds(now_ms)
    ms, me = month_bounds(now_ms, earliest)

    s_used = sum(c for t, c in costs if session_start <= t < now_ms)
    w_used = sum(c for t, c in costs if ws <= t < we)
    m_used = sum(c for t, c in costs if ms <= t < me)

    s_oldest = min((t for t, c in costs if session_start <= t < now_ms), default=now_ms)
    s_reset = s_oldest + SESSION_MS if s_oldest > session_start else now_ms + SESSION_MS

    def mk(kind, used, limit, reset_ms):
        pct = min(100.0, used / limit * 100) if limit else 0.0
        return {"kind": kind, "used": used, "limit": limit, "pct": pct, "reset": reset_ms}

    return [
        mk("session", s_used, LIMITS["session"], s_reset),
        mk("weekly", w_used, LIMITS["weekly"], we),
        mk("monthly", m_used, LIMITS["monthly"], me),
    ]


def model_stats(rows, now_ms):
    stats = {}
    session_start = now_ms - SESSION_MS
    ws, we = week_bounds(now_ms)
    ms, me = month_bounds(now_ms, min((r["ts"] for r in rows), default=now_ms))
    for r in rows:
        m = r["model"]
        src = r.get("src") or "?"
        key = (m, src)
        if key not in stats:
            stats[key] = {"count_s": 0, "count_w": 0, "count_m": 0, "cost_s": 0.0,
                          "tokens_in": 0, "tokens_out": 0, "cost_total": 0.0}
        s = stats[key]
        if session_start <= r["ts"] < now_ms:
            s["count_s"] += 1
            if r["cost"] is not None:
                s["cost_s"] += r["cost"]
        if ws <= r["ts"] < we:
            s["count_w"] += 1
        if ms <= r["ts"] < me:
            s["count_m"] += 1
        tk = r.get("tokens") or {}
        s["tokens_in"] += tk.get("input", 0) or 0
        s["tokens_out"] += tk.get("output", 0) or 0
        if r["cost"] is not None:
            s["cost_total"] += r["cost"]

    src_count = {}
    for (m, src) in stats:
        if is_free_model(m):
            src_count[m] = src_count.get(m, 0) + 1

    out = []
    for (m, src), s in stats.items():
        is_free = is_free_model(m)
        group = "free" if is_free else "go"
        name = DISPLAY_NAMES.get(m, m)
        if is_free and src_count.get(m, 0) > 1:
            name = f"{name} ({src})"
        out.append({
            "model": m,
            "source": src,
            "key": f"{m}|{src}",
            "is_free": is_free,
            "group": group,
            "name": name,
            "count_s": s["count_s"], "count_w": s["count_w"], "count_m": s["count_m"],
            "cost_s": s["cost_s"], "cost_total": s["cost_total"],
            "tokens_in": s["tokens_in"], "tokens_out": s["tokens_out"],
            "req_lim": REQ_LIMITS.get(m),
        })
    out.sort(key=lambda x: -x["count_s"])
    return out


def model_history(rows, days=14):
    buckets = {}
    now_ms = int(time.time() * 1000)
    start = now_ms - days * 86400 * 1000
    for r in rows:
        if r["ts"] < start:
            continue
        d = datetime.fromtimestamp(r["ts"] / 1000, timezone.utc).strftime("%m-%d")
        m = r["model"]
        src = r.get("src") or "?"
        key = (m, src)
        b = buckets.setdefault(key, {}).setdefault(d, [0.0, 0, 0, 0])
        b[0] += r["cost"] if r["cost"] is not None else 0.0
        b[1] += 1
        tk = r.get("tokens") or {}
        b[2] += tk.get("input", 0) or 0
        b[3] += tk.get("output", 0) or 0

    src_count = {}
    for (m, src) in buckets:
        if is_free_model(m):
            src_count[m] = src_count.get(m, 0) + 1

    out = []
    for (m, src), days_map in buckets.items():
        series = [{"date": k, "cost": round(v[0], 4), "count": v[1],
                   "tokens_in": v[2], "tokens_out": v[3]}
                  for k, v in sorted(days_map.items())]
        name = DISPLAY_NAMES.get(m, m)
        if is_free_model(m) and src_count.get(m, 0) > 1:
            name = f"{name} ({src})"
        out.append({"model": m, "source": src, "key": f"{m}|{src}",
                    "name": name, "is_free": is_free_model(m),
                    "series": series})
    out.sort(key=lambda x: -sum(p["cost"] for p in x["series"]))
    return out


def _snapshot_cookie_db(cookie_db):
    try:
        import shutil
        import tempfile
        net_dir = os.path.dirname(cookie_db)
        tmp_dir = os.path.join(tempfile.gettempdir(), "opencode", "webdata_snapshot")
        os.makedirs(tmp_dir, exist_ok=True)
        snap = os.path.join(tmp_dir, "Cookies")
        for name in ("Cookies", "Cookies-journal", "Cookies-wal", "Cookies-shm"):
            src = os.path.join(net_dir, name)
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, os.path.join(tmp_dir, name))
                except Exception:
                    pass
        if os.path.isfile(snap):
            return snap
    except Exception:
        pass
    return cookie_db


def _dpapi_decrypt(data):
    try:
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_ulong),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]
        blob = DATA_BLOB(len(data), ctypes.cast(
            ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
        out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:
        return None


def read_auth_cookie_from_webdata():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception:
        return ""
    try:
        cookie_db = os.path.join(APP_DIR, "webdata", "EBWebView", "Default", "Network", "Cookies")
        local_state = os.path.join(APP_DIR, "webdata", "EBWebView", "Local State")
        if not os.path.isfile(cookie_db) or not os.path.isfile(local_state):
            return ""
        with open(local_state, "r", encoding="utf-8") as f:
            ls = json.load(f)
        enc = ls.get("os_crypt", {}).get("encrypted_key", "")
        if not enc:
            return ""
        import base64
        raw = base64.b64decode(enc)
        if raw[:5] != b"DPAPI":
            return ""
        key = _dpapi_decrypt(raw[5:])
        if not key:
            return ""
        cookie_path = _snapshot_cookie_db(cookie_db)
        con = sqlite3.connect(cookie_path)
        try:
            cur = con.cursor()
            cur.execute("SELECT encrypted_value FROM cookies WHERE host_key=? AND name=?",
                        ("opencode.ai", "auth"))
            row = cur.fetchone()
        finally:
            con.close()
        if not row or not row[0]:
            return ""
        blob = bytes(row[0])
        if blob[:3] != b"v10":
            return ""
        try:
            nonce, ct = blob[3:15], blob[15:-16]
            tag = blob[-16:]
            plain = AESGCM(key).decrypt(nonce, ct + tag, None)
            idx = plain.find(b"Fe26")
            if idx < 0:
                return ""
            return plain[idx:].decode("utf-8", errors="replace")
        except Exception:
            return ""
    except Exception:
        return ""


def scrape_server_usage(auth_cookie, workspace_id, timeout=12):
    if not auth_cookie or not workspace_id:
        return {"ok": False, "error": "缺少 auth cookie 或 workspace ID"}
    url = f"{DASHBOARD_PREFIX}{workspace_id}{DASHBOARD_SUFFIX}"
    try:
        req = Request(url, headers={
            "User-Agent": DASH_USER_AGENT,
            "Accept": "text/html",
            "Cookie": f"auth={auth_cookie}",
        })
        with urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "error": f"请求失败: {e}"}

    items = html.split('data-slot="usage-item"')
    if len(items) <= 1:
        if "auth" in html.lower() and ("login" in html.lower() or "sign in" in html.lower()):
            return {"ok": False, "error": "未登录或 cookie 失效，请重新登录"}
        return {"ok": False, "error": "页面上未找到用量数据"}

    windows = []
    for chunk in items[1:]:
        lm = re.search(r'data-slot="usage-label">([^<]+)<', chunk)
        vm = re.search(r'data-slot="usage-value">[^0-9]*(\d+(?:\.\d+)?)', chunk)
        if not lm or not vm:
            continue
        label = lm.group(1).strip()
        pct = float(vm.group(1))
        rm = re.search(r'data-slot="(?:reset-time|reset-now)">([\s\S]*?)</span>', chunk)
        reset_text = ""
        if rm:
            reset_text = re.sub(r"<!--.*?-->", "", rm.group(1))
            reset_text = re.sub(r"Resets?\s*in\s*", "", reset_text, flags=re.I).strip()
        kind = "monthly" if "month" in label.lower() else ("weekly" if "week" in label.lower() else "session")
        windows.append({"kind": kind, "label": label, "pct": pct, "reset_text": reset_text})
    if not windows:
        return {"ok": False, "error": "未能解析用量窗口"}
    windows.sort(key=lambda w: {"session": 0, "weekly": 1, "monthly": 2}.get(w["kind"], 9))
    return {"ok": True, "windows": windows, "workspace_id": workspace_id}


class Api:
    def __init__(self, window_getter):
        self.cfg = load_config()
        self.api_key = self.cfg.get("api_key") or discover_go_key()
        self.rows = []
        self.windows = []
        self.stats = []
        self.history = []
        self.last_log_id = self.cfg.get("codex_log_id", 0)
        self.last_refresh = 0
        self.lock = threading.Lock()
        self.key_ok = False
        self.models = 0
        self.server_usage = None
        self.server_error = ""
        self.window_getter = window_getter
        self._win = None
        self._console_win = None

    def set_window(self, win):
        self._win = win

    def refresh_data(self):
        go_rows = read_opencode_all()
        cx_rows, new_id = read_codex_logs(self.last_log_id)
        with self.lock:
            if new_id > self.last_log_id:
                self.last_log_id = new_id
                self.cfg["codex_log_id"] = new_id
                save_config(self.cfg)
            self.rows = go_rows + cx_rows
            now_ms = int(time.time() * 1000)
            self.windows = build_windows(self.rows, now_ms)
            self._apply_calibration(self.windows, now_ms)
            self.stats = model_stats(self.rows, now_ms)
            self.history = model_history(self.rows)
            self.last_refresh = now_ms
        self._try_server_sync()

    def _apply_calibration(self, windows, now_ms):
        cal = self.cfg.get("calibration") or {}
        if not cal:
            return
        for w, key in zip(windows, ["session", "weekly", "monthly"]):
            pct = cal.get(key)
            if pct is None or pct <= 0:
                continue
            if w["reset"] <= now_ms:
                continue
            target = LIMITS[key] * pct / 100.0
            if target > w["used"]:
                w["used"] = target
                w["pct"] = min(100.0, target / LIMITS[key] * 100)
            w["calibrated"] = True

    def _try_server_sync(self):
        cookie = (self.cfg.get("server") or {}).get("auth_cookie") or ""
        ws = (self.cfg.get("server") or {}).get("workspace_id") or ""
        if not cookie:
            cookie = read_auth_cookie_from_webdata()
        if not cookie or not ws:
            self.server_usage = None
            return
        res = scrape_server_usage(cookie, ws)
        if res.get("ok"):
            self.server_usage = res
            self.server_error = ""
        else:
            self.server_error = res.get("error", "")
            self.server_usage = None
        self._try_persist_server_data(cookie, ws, res)

    def _try_persist_server_data(self, cookie, ws, usage_res):
        def run():
            try:
                import server_data as sd
                windows = usage_res.get("windows") if usage_res and usage_res.get("ok") else None
                sd.sync_all(cookie, ws, windows=windows, max_pages=20)
            except Exception:
                pass

        t = getattr(self, "_sd_thread", None)
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=run, daemon=True)
        self._sd_thread = t
        t.start()

    def _verify_key(self):
        if not self.api_key:
            self.key_ok = False
            return
        try:
            req = Request(GO_ENDPOINT, headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": DASH_USER_AGENT,
            })
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.models = len(data.get("data", []))
            self.key_ok = True
        except Exception:
            self.key_ok = False

    def get_snapshot(self):
        try:
            self.refresh_data()
        except Exception:
            pass
        cal = {k: v for k, v in (self.cfg.get("calibration") or {}).items() if v}
        srv = self.server_usage or {}
        return {
            "windows": self.windows,
            "stats": self.stats,
            "history": self.history,
            "rows": len(self.rows),
            "key": self.key_ok,
            "models": self.models,
            "calibration": cal if cal else None,
            "server": srv.get("windows") if srv.get("ok") else None,
            "server_error": self.server_error if not srv.get("ok") else None,
            "server_configured": bool((self.cfg.get("server") or {}).get("auth_cookie")),
        }

    def get_calibration(self):
        return self.cfg.get("calibration") or {}

    def save_calibration(self, s=0, w=0, m=0):
        cal = {}
        if s > 0:
            cal["session"] = min(100, s)
        if w > 0:
            cal["weekly"] = min(100, w)
        if m > 0:
            cal["monthly"] = min(100, m)
        self.cfg["calibration"] = cal
        save_config(self.cfg)
        return True

    def get_key(self):
        return self.api_key or ""

    def save_key(self, key):
        self.api_key = key.strip()
        self.cfg["api_key"] = self.api_key
        save_config(self.cfg)
        if self.api_key:
            self._verify_key()
        return True

    def get_server_config(self):
        return self.cfg.get("server") or {}

    def save_server_config(self, auth_cookie, workspace_id):
        self.cfg["server"] = {"auth_cookie": auth_cookie.strip(), "workspace_id": workspace_id.strip()}
        save_config(self.cfg)
        return True

    def get_opacity(self):
        return self.cfg.get("opacity", 0.3)

    def save_opacity(self, v):
        val = max(0.1, min(1.0, float(v)))
        self.cfg["opacity"] = val
        save_config(self.cfg)
        return True

    def apply_opacity(self):
        return True

    def open_console(self):
        try:
            if self._console_win is not None:
                self._console_win.restore()
                self._console_win.move(240, 80)
                self._console_win.show()
                try:
                    self._console_win.evaluate_js("window.focus()")
                except Exception:
                    pass
                return "ok"
        except Exception:
            pass
        return "no_window"

    def grab_cookie(self):
        auth_val = read_auth_cookie_from_webdata()
        source = "webdata"
        if not auth_val:
            if self._console_win is None:
                return {"ok": False, "error": "Console 窗口未创建"}
            try:
                cookies = self._console_win.get_cookies()
            except Exception as e:
                return {"ok": False, "error": f"读取 cookie 失败: {e}"}
            for c in cookies or []:
                if getattr(c, "name", "") == "auth":
                    auth_val = getattr(c, "value", "") or ""
                    source = "console"
                    break
        if not auth_val:
            names = [getattr(c, "name", "?") for c in (cookies or [])][:15] if locals().get("cookies") else []
            return {"ok": False,
                    "error": f"未找到 auth cookie（{names if names else '本地 webdata 无 cookie，请先在 Console 登录'}）。"
                             f"请先在 Console 窗口登录 opencode.ai 并打开用量页后重试"}
        srv = self.cfg.get("server") or {}
        srv["auth_cookie"] = auth_val
        if not srv.get("workspace_id"):
            try:
                url = self._console_win.get_current_url() if self._console_win else ""
                m = re.search(r"/workspace/(wrk_[A-Za-z0-9]+)/", url or "")
                if m:
                    srv["workspace_id"] = m.group(1)
            except Exception:
                pass
        self.cfg["server"] = srv
        save_config(self.cfg)
        self._try_server_sync()
        return {"ok": True, "source": source,
                "workspace_id": srv.get("workspace_id", ""),
                "found": bool(self.server_usage)}

    def move_window(self, dx, dy):
        try:
            if self._win is not None:
                x, y = self._win.x, self._win.y
                self._win.move(x + dx, y + dy)
        except Exception:
            pass

    def resize_window(self, w, h):
        try:
            if self._win is not None:
                self._win.resize(w, h)
                return True
        except Exception:
            pass
        return False

    def quit(self):
        try:
            if self._win is not None:
                self._win.destroy()
        except Exception:
            pass
        os._exit(0)


def _set_layered(hwnd):
    try:
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not (ex & WS_EX_LAYERED):
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
    except Exception:
        pass


def _enable_layered_watcher():
    def run():
        user32 = ctypes.windll.user32
        for _ in range(60):
            for title in ("Go \u7528\u91cf", "Go Console"):
                hwnd = user32.FindWindowW(None, title)
                if hwnd:
                    _set_layered(hwnd)
            time.sleep(0.5)

    threading.Thread(target=run, daemon=True).start()


def _enable_layered(win):
    _enable_layered_watcher()


def main():
    import webview

    api = Api(lambda: None)

    win = webview.create_window(
        "Go 用量",
        INDEX_PATH,
        width=560,
        height=480,
        frameless=True,
        transparent=True,
        on_top=True,
        easy_drag=False,
        js_api=api,
        background_color="#000000",
    )
    api.set_window(win)

    console_win = webview.create_window(
        "Go Console",
        "https://opencode.ai/auth",
        width=980,
        height=760,
        x=200,
        y=60,
        on_top=False,
        hidden=True,
        transparent=True,
        easy_drag=False,
        background_color="#000000",
    )
    api._console_win = console_win

    def boot():
        threading.Thread(target=api._verify_key, daemon=True).start()
        _enable_layered(win)
        api.refresh_data()
        api.apply_opacity()
        try:
            win.evaluate_js("refresh()")
        except Exception:
            pass

    webview.start(boot, debug=False, private_mode=False, storage_path=os.path.join(APP_DIR, "webdata"))


if __name__ == "__main__":
    main()
