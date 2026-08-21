#!/usr/bin/env python3
"""Data-only server for the Go usage widget.

Python 只负责取数/计算，前端与窗口交互交给 Electron (electron/main.js)。
Serves JSON on http://127.0.0.1:8765/api/*
"""
import json
import os
import sqlite3
import sys
import threading
import time
import importlib.util
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

_spec = importlib.util.spec_from_file_location("gw", os.path.join(APP_DIR, "go-usage-widget.py"))
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

try:
    import server_data as sd
except Exception:
    sd = None

try:
    import usage_remote as ur
except Exception:
    ur = None

import views as vw
import formula_registry as fr

PORT = 8765
CACHE = {"state": None, "ts": 0, "lock": threading.Lock()}

_FORMULA_STORE = vw.FormulaStore(url="")
_FORMULA_ENGINE = None
_FORMULA_LOCK = threading.Lock()


def _formula_url():
    try:
        cfg = gw.load_config()
        return (cfg.get("formula_url") or "").strip()
    except Exception:
        return ""


def apply_params_to_gw(formula):
    """把云端公式 params 覆盖到 gw 模块级规则表, 使本地行为随云端公式变化。
    缺 key 保持本地默认, 保证降级一致。"""
    if not isinstance(formula, dict):
        return
    p = formula.get("params") or {}
    if not isinstance(p, dict):
        return
    limits = p.get("limits") or {}
    if isinstance(limits, dict):
        if limits.get("session"):
            gw.LIMITS["session"] = float(limits["session"])
        if limits.get("weekly"):
            gw.LIMITS["weekly"] = float(limits["weekly"])
        if limits.get("monthly"):
            gw.LIMITS["monthly"] = float(limits["monthly"])
        if limits.get("credit_per_applied"):
            gw.CREDIT_PER_APPLIED = float(limits["credit_per_applied"])
    windows = p.get("windows") or {}
    if isinstance(windows, dict):
        if windows.get("session_ms"):
            gw.SESSION_MS = int(windows["session_ms"])
        if windows.get("week_ms"):
            gw.WEEK_MS = int(windows["week_ms"])
    providers = p.get("providers") or {}
    if isinstance(providers, dict):
        if isinstance(providers.get("src"), dict):
            gw.PROVIDER_SRC = dict(providers["src"])
        if isinstance(providers.get("prefixes"), list):
            gw.PROVIDER_PREFIXES |= set(providers["prefixes"])
        if isinstance(providers.get("aliases"), dict):
            gw.MODEL_ALIASES = dict(providers["aliases"])
    free = p.get("free_models") or {}
    if isinstance(free, dict):
        if isinstance(free.get("whitelist"), list):
            gw.FREE_WHITELIST = set(free["whitelist"])
        if isinstance(free.get("suffixes"), list):
            gw.FREE_SUFFIXES = tuple(free["suffixes"])
        if isinstance(free.get("exclude"), list):
            gw.FREE_EXCLUDE = set(free["exclude"])
    if isinstance(p.get("prices"), dict):
        gw.PRICES = dict(p["prices"])
    if isinstance(p.get("req_limits"), dict):
        gw.REQ_LIMITS = dict(p["req_limits"])
    if isinstance(p.get("tokens_per_req"), dict):
        gw.TOKENS_PER_REQ = dict(p["tokens_per_req"])
    if isinstance(p.get("display_names"), dict):
        gw.DISPLAY_NAMES = dict(p["display_names"])
    if isinstance(p.get("model_quotas"), dict):
        gw.MODEL_QUOTAS = dict(p["model_quotas"])
    meter = p.get("meter") or {}
    if isinstance(meter, dict):
        if isinstance(meter.get("rates"), dict):
            gw.MODEL_RATES = dict(meter["rates"])
        if meter.get("rate_default") is not None:
            gw.RATE_DEFAULT = float(meter["rate_default"])
        if meter.get("rate_intercept") is not None:
            gw.RATE_INTERCEPT = float(meter["rate_intercept"])


def get_formula(force=False):
    """返回当前公式 (云端/默认), 并同步重建引擎 + 覆盖本地规则表。"""
    global _FORMULA_STORE, _FORMULA_ENGINE
    url = _formula_url()
    if _FORMULA_STORE.url != url:
        _FORMULA_STORE = vw.FormulaStore(url=url)
    f = _FORMULA_STORE.get(force=force)
    apply_params_to_gw(f)
    fr.apply_formulas(f.get("formulas") if isinstance(f, dict) else None)
    with _FORMULA_LOCK:
        _FORMULA_ENGINE = vw.ViewEngine(f, norm=gw.norm_model,
                                        is_free=gw.is_free_model,
                                        local_tz=gw.LOCAL_TZ)
    return f


FORMULA_SYNC_INTERVAL_S = 900


_FORMULA_LAST_VERSION = None


def formula_sync_loop():
    """后台主动同步云端公式: 周期性拉取云端, 检测版本变化并应用到本地。
    与 auto_sync_loop 不同, 这里同步的是公式规则而非 server_usage.db。
    失败静默保留上一版已生效公式, 等下一轮重试。"""
    global _FORMULA_LAST_VERSION
    while True:
        try:
            f = get_formula(force=True)
            ver = f.get("version")
            if ver != _FORMULA_LAST_VERSION:
                print(f"[formula-sync] version {_FORMULA_LAST_VERSION} -> {ver} (source={_FORMULA_STORE.meta().get('source')})")
                _FORMULA_LAST_VERSION = ver
        except Exception:
            pass
        time.sleep(FORMULA_SYNC_INTERVAL_S)


def run_view(vid, rows):
    get_formula()
    with _FORMULA_LOCK:
        engine = _FORMULA_ENGINE
    return engine.execute(vid, rows)


def build_state():
    now_ms = int(time.time() * 1000)
    try:
        get_formula()
    except Exception:
        pass
    applied_credits = _applied_credits()
    try:
        rows, cost_map = _collect_rows()
    except Exception:
        rows, cost_map = [], {}
    try:
        key = _effective_key()
        key_ok = bool(key)
        models = 0
        all_go_models = []
        if key:
            all_go_models = gw.fetch_go_model_list(key)
            models = len(all_go_models)
    except Exception:
        key_ok = False
        models = 0
        all_go_models = []

    windows = gw.build_windows(rows, now_ms, applied_credits=applied_credits)
    _apply_calibration(windows)
    _apply_server_quota(windows, applied_credits)
    # 行级折算率: 前端聚合/明细按模型折算 (v5 口径, 与总进度对账闭合)
    for r in rows:
        r["rate"] = gw.rate_for(gw.norm_model(r.get("model") or ""), r.get("src"))
    stats = gw.model_stats(rows, now_ms, cost_map, all_go_models)
    history = gw.model_history(rows, days=0)
    suppliers = gw.supplier_stats(rows)
    heatmap = gw.heatmap(rows)

    cfg = {}
    try:
        cfg = gw.load_config()
    except Exception:
        pass
    cal = {k: v for k, v in (cfg.get("calibration") or {}).items() if v}
    srv_cfg = cfg.get("server") or {}
    srv = _latest_server_quota() or None

    # ---- 用注册表补充展示类数值指标 ----
    try:
        quota_limit = gw.limit_for("monthly") or 60.0
        global_limit = quota_limit
        used_all = sum((x.get("cost_total") or 0.0) for x in stats)
        global_remain = max(0.0, global_limit - used_all)
        weeks_per_month = float((_FORMULA_STORE.get({}).get("constants", {}) or {}).get("weeks_per_month", 4.345))
        periods_per_day = float((_FORMULA_STORE.get({}).get("constants", {}) or {}).get("periods_per_day", 6.0))

        for x in stats:
            mq = x.get("model_quota") or 0.0
            mu = x.get("model_used") or 0.0
            mr = max(0.0, mq - mu)
            x["model_remain"] = fr.compute("model_remain", model_quota=mq, model_used=mu) or mr

            cq_w = fr.compute("cq_weekly", model_quota=mq, weeks_per_month=weeks_per_month)
            cq_s = fr.compute("cq_session", model_quota=mq, periods_per_day=periods_per_day)
            x["cq_weekly"] = cq_w
            x["cq_session"] = cq_s

            monthly_tok = (x.get("tokens_in_m") or 0) + (x.get("tokens_out_m") or 0) + (x.get("tokens_cache_m") or 0)
            weekly_tok = (x.get("tokens_in_w") or 0) + (x.get("tokens_out_w") or 0) + (x.get("tokens_cache_w") or 0)
            session_tok = (x.get("tokens_in_s") or 0) + (x.get("tokens_out_s") or 0) + (x.get("tokens_cache_s") or 0)

            monthly_cost = x.get("cost_m") or 0.0
            if monthly_cost > 0 and monthly_tok > 0:
                avg_monthly = monthly_cost / monthly_tok
                weekly_cost = x.get("cost_w") or 0.0
                avg_weekly = weekly_cost / weekly_tok if weekly_tok > 0 else 0.0
                session_cost = x.get("cost_s") or 0.0
                avg_session = session_cost / session_tok if session_tok > 0 else 0.0
            else:
                avg_monthly = avg_weekly = avg_session = 0.0

            x["tq_monthly"] = fr.compute("tq_monthly", cq_monthly=mq, avg_monthly_cost_per_token=avg_monthly)
            x["tq_weekly"] = fr.compute("tq_weekly", cq_weekly=cq_w, avg_weekly_cost_per_token=avg_weekly)
            x["tq_session"] = fr.compute("tq_session", cq_session=cq_s, avg_session_cost_per_token=avg_session)
            x["tp_monthly"] = fr.compute("tp_monthly", tok_monthly=monthly_tok, tq_monthly=x.get("tq_monthly") or 1)
            x["tp_weekly"] = fr.compute("tp_weekly", tok_weekly=weekly_tok, tq_weekly=x.get("tq_weekly") or 1)
            x["tp_session"] = fr.compute("tp_session", tok_session=session_tok, tq_session=x.get("tq_session") or 1)

            x["effective_remain"] = fr.compute("effective_remain", model_remain=mr, global_remain=global_remain) or min(mr, global_remain)
            x["avg_per_req"] = fr.compute("avg_per_req", cost_of_period=x.get("cost_m") or 0.0, used_count=x.get("count_m") or 0, est_req_cost=x.get("est_req_cost") or 0.0)
            x["remain_cnt"] = fr.compute("remain_cnt", effective_remain=x.get("effective_remain") or 0.0, avg_per_req=x.get("avg_per_req") or 0.0)
            x["cache_hit"] = fr.compute("cache_hit", cache_read=x.get("cache_read") or 0, tokens_in=x.get("tokens_in") or 0)
            x["rate"] = fr.compute("rate", tok_sec_sum=x.get("tok_sec_sum") or 0.0, tok_sec_n=x.get("tok_sec_n") or 0)
    except Exception:
        pass

    return {
        "windows": windows,
        "stats": stats,
        "history": history,
        "suppliers": suppliers,
        "heatmap": heatmap,
        "rows": len(rows),
        "key": key_ok,
        "models": models,
        "calibration": cal if cal else None,
        "server": srv,
        "credits": applied_credits,
        "credits_dollars": round(applied_credits * gw.CREDIT_PER_APPLIED, 2),
        "server_error": None,
        "server_configured": bool(srv_cfg.get("auth_cookie")) or bool(srv),
        "ts": now_ms,
    }


def _effective_key():
    try:
        cfg = gw.load_config()
        k = (cfg.get("api_key") or "").strip()
        if k:
            return k
    except Exception:
        pass
    return gw.discover_go_key() or ""


def _applied_credits():
    """已应用的 referral credit 数：优先 usage_remote.db sync_meta，fallback 0。"""
    if ur is not None:
        try:
            return ur.read_remote_credits()
        except Exception:
            return 0
    return 0


def _apply_calibration(windows):
    try:
        cfg = gw.load_config()
        cal = cfg.get("calibration") or {}
    except Exception:
        return
    for w, key in zip(windows, ["session", "weekly", "monthly"]):
        pct = cal.get(key)
        if not pct or pct <= 0:
            continue
        limit = gw.limit_for(key)
        if not limit:
            continue
        target = limit * pct / 100.0
        if target > w["used"]:
            w["used"] = target
            w["pct"] = min(100.0, target / limit * 100)
        w["calibrated"] = True


def _latest_server_quota():
    """从 quota_snapshot 读最新一次抓取的滚动窗口百分比（官网实时值）。
    优先读 usage_remote.db，fallback 到 server_usage.db。"""
    db = None
    if ur is not None:
        db = ur.REMOTE_DB
    if not db or not os.path.exists(db):
        db = (sd.DB_PATH if sd else os.path.join(APP_DIR, "server_usage.db"))
    if not os.path.exists(db):
        return []
    try:
        conn = sqlite3.connect(db)
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


def _apply_server_quota(windows, applied_credits=0):
    """用官网 quota 覆盖本地百分比。官方 pct 分母恒为基础额度, 直接反推 used 并同步 limit/pct。"""
    srv = _latest_server_quota()
    if not srv:
        return
    srv_map = {w["kind"]: w for w in srv}
    for w in windows:
        sw = srv_map.get(w["kind"])
        if not sw:
            continue
        base = gw.limit_for(w["kind"])          # 基础限额 12/30/60 (分母恒定, credits 不扩容)
        used = base * sw["pct"] / 100.0         # 官网 pct 反推用量
        pct = sw["pct"]                          # 直接采用官网 pct
        w["used"] = used
        w["limit"] = base
        w["pct"] = pct
        w["reset"] = sw["reset_text"]
        w["calibrated"] = True


def _collect_rows():
    remote_rows = []
    cost_map = {}
    if ur is not None:
        try:
            remote_rows = ur.read_remote_rows()
            cost_map = ur.read_remote_cost_map()
        except Exception:
            remote_rows = []
            cost_map = {}
    if remote_rows:
        srv_keys = {(r["model"], r.get("src")) for r in remote_rows}
        latest_fetched = ur.read_remote_latest_fetched_at() if ur is not None else 0
        local_rows = gw.read_opencode_all()
        extra = [r for r in local_rows
                 if (r["model"], r.get("src")) not in srv_keys
                 or r["ts"] > latest_fetched]
        return remote_rows + extra, cost_map

    srv_rows = []
    if sd is not None:
        try:
            srv_rows = sd.read_server_rows()
        except Exception:
            srv_rows = []
    if srv_rows:
        cost_map = {}
        if sd is not None:
            try:
                cost_map = sd.read_cost_map()
            except Exception:
                pass
        srv_keys = {(r["model"], r.get("src")) for r in srv_rows}
        latest_fetched = sd.read_latest_fetched_at() if sd is not None else 0
        local_rows = gw.read_opencode_all()
        extra = [r for r in local_rows
                 if (r["model"], r.get("src")) not in srv_keys
                 or r["ts"] > latest_fetched]
        return srv_rows + extra, cost_map
    go_rows = gw.read_opencode_all()
    try:
        cfg = gw.load_config()
        last_id = cfg.get("codex_log_id") or 0
    except Exception:
        last_id = 0
    cx_rows, new_id = gw.read_codex_logs(last_id)
    if new_id > last_id:
        try:
            cfg = gw.load_config()
            cfg["codex_log_id"] = new_id
            gw.save_config(cfg)
        except Exception:
            pass
    return go_rows + cx_rows, {}


def do_sync():
    try:
        cfg = gw.load_config()
    except Exception:
        cfg = {}
    srv = cfg.get("server") or {}
    cookie = srv.get("auth_cookie") or ""
    ws = srv.get("workspace_id") or ""
    if not cookie or not ws:
        return {"ok": False, "error": "缺少 auth cookie 或 workspace ID"}
    try:
        res = gw.scrape_server_usage(cookie, ws)
    except Exception as e:
        return {"ok": False, "error": f"抓取失败: {e}"}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "抓取失败")}
    if ur is not None:
        try:
            r = ur.full_sync(cookie, ws, windows=res.get("windows"),
                             applied_credits=res.get("applied_credits"))
        except Exception as e:
            return {"ok": False, "error": f"落库失败: {e}"}
    else:
        r = {"ok": True}
    with CACHE["lock"]:
        CACHE["state"] = None
        CACHE["ts"] = 0
    return {"ok": True, "windows": res.get("windows"), "remote": r}


def _config_json():
    try:
        cfg = gw.load_config()
    except Exception:
        cfg = {}
    return {
        "api_key": cfg.get("api_key") or "",
        "server": cfg.get("server") or {"auth_cookie": "", "workspace_id": ""},
        "calibration": cfg.get("calibration") or {},
    }


def do_grab():
    """尽力从本机浏览器 cookie 库抓取 auth。现代 Chrome app-bound 加密下返回空。"""
    try:
        cookie = gw.read_auth_cookie_from_webdata()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not cookie:
        return {"ok": False, "error": "未找到可解密的有效 cookie（现代 Chrome 使用 app-bound 加密，请用应用内登录窗口抓取）"}
    try:
        cfg = gw.load_config()
        srv = cfg.get("server") or {}
        ws = srv.get("workspace_id") or ""
        if not ws:
            # 尝试通过 /auth 重定向自动发现 workspace
            from urllib.request import Request, urlopen
            req = Request("https://opencode.ai/auth", headers={
                "User-Agent": "Mozilla/5.0",
                "Cookie": "auth=" + cookie,
            })
            try:
                urlopen(req, timeout=10)
            except Exception as e:
                loc = None
                if isinstance(e, Exception) and hasattr(e, "headers"):
                    loc = e.headers.get("Location") or ""
                m = __import__("re").search(r"/workspace/(wrk_[A-Za-z0-9]+)", str(loc))
                if m:
                    ws = m.group(1)
            if not ws:
                ws = srv.get("workspace_id") or ""
    except Exception:
        ws = ""
    if ws:
        try:
            cfg = gw.load_config()
            cfg["server"] = {"auth_cookie": cookie, "workspace_id": ws}
            gw.save_config(cfg)
        except Exception:
            pass
    return {"ok": True, "auth_cookie": cookie, "workspace_id": ws}


def _send_json(self, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    self.send_response(code)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    self.send_header("Access-Control-Allow-Headers", "Content-Type")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/state":
            now = time.time()
            with CACHE["lock"]:
                if CACHE["state"] is None or now - CACHE["ts"] > 30:
                    try:
                        CACHE["state"] = build_state()
                        CACHE["ts"] = now
                    except Exception as e:
                        CACHE["state"] = {"error": str(e)}
                        CACHE["ts"] = now
                body = json.dumps(CACHE["state"], ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/config":
            _send_json(self, _config_json())
        elif path == "/api/formula":
            q = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            force = "refresh" in q or "force" in q
            f = get_formula(force=force)
            meta = _FORMULA_STORE.meta()
            _send_json(self, {
                "version": f.get("version"),
                "source": meta["source"],
                "url": meta["url"],
                "error": meta["error"],
                "fetched_at": meta["fetched_at"],
                "params": f.get("params"),
                "views": f.get("views"),
                "constants": f.get("constants"),
                "formulas": fr.FORMULA_TABLE(),
            })
        elif path == "/api/views":
            f = get_formula()
            _send_json(self, {
                "version": f.get("version"),
                "views": [{k: v.get(k) for k in ("id", "label", "scope", "range", "group")}
                          for v in (f.get("views") or [])],
            })
        elif path.startswith("/api/view/"):
            vid = urllib.parse.unquote(path[len("/api/view/"):])
            try:
                rows, _ = _collect_rows()
                _send_json(self, run_view(vid, rows))
            except vw.FormulaError as e:
                _send_json(self, {"ok": False, "error": str(e)}, 404)
            except Exception as e:
                _send_json(self, {"ok": False, "error": repr(e)}, 500)
        elif path == "/api/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            _send_json(self, {"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        data = self._read_body()
        try:
            cfg = gw.load_config()
        except Exception:
            cfg = {}
        if path == "/api/key":
            cfg["api_key"] = (data.get("key") or "").strip()
            gw.save_config(cfg)
            _send_json(self, {"ok": True})
        elif path == "/api/server":
            cfg["server"] = {
                "auth_cookie": (data.get("auth_cookie") or "").strip(),
                "workspace_id": (data.get("workspace_id") or "").strip(),
            }
            gw.save_config(cfg)
            _send_json(self, {"ok": True})
        elif path == "/api/calibrate":
            cal = {}
            for k in ("session", "weekly", "monthly"):
                v = data.get(k)
                if v is not None:
                    try:
                        fv = float(v)
                        if fv > 0:
                            cal[k] = min(100.0, fv)
                    except Exception:
                        pass
            cfg["calibration"] = cal
            gw.save_config(cfg)
            _send_json(self, {"ok": True})
        elif path == "/api/sync":
            _send_json(self, do_sync())
        elif path == "/api/grab":
            _send_json(self, do_grab())
        else:
            _send_json(self, {"ok": False, "error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def preheat():
    """启动时后台预热：提前算好 state 并写入缓存，前端首请求立即命中。"""
    try:
        with CACHE["lock"]:
            CACHE["state"] = build_state()
            CACHE["ts"] = time.time()
    except Exception:
        pass
    try:
        get_formula()
    except Exception:
        pass


AUTO_SYNC_INTERVAL_S = 1800


def auto_sync_loop():
    """后台定期同步远程 server_usage.db（启动一次 + 每 30 分钟）。
    server 数据是权威 go/zen 源，但会滞后于本地 opencode.db；
    不自动同步会导致 widget 显示旧的用量。失败静默，等下一轮。"""
    while True:
        try:
            do_sync()
        except Exception:
            pass
        time.sleep(AUTO_SYNC_INTERVAL_S)


def main():
    threading.Thread(target=preheat, daemon=True).start()
    threading.Thread(target=auto_sync_loop, daemon=True).start()
    threading.Thread(target=formula_sync_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[data-server] listening on http://127.0.0.1:{PORT}/api/state")
    srv.serve_forever()


if __name__ == "__main__":
    main()
