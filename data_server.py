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

PORT = 8765
CACHE = {"state": None, "ts": 0, "lock": threading.Lock()}


def build_state():
    now_ms = int(time.time() * 1000)
    try:
        rows, _ = _collect_rows()
    except Exception:
        rows = []
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

    windows = gw.build_windows(rows, now_ms)
    _apply_calibration(windows)
    _apply_server_quota(windows)
    stats = gw.model_stats(rows, now_ms, None, all_go_models)
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
        limit = gw.LIMITS.get(key)
        if not limit:
            continue
        target = limit * pct / 100.0
        if target > w["used"]:
            w["used"] = target
            w["pct"] = min(100.0, target / limit * 100)
        w["calibrated"] = True


def _latest_server_quota():
    """从 quota_snapshot 读最新一次抓取的滚动窗口百分比（官网实时值）。"""
    db = (sd.DB_PATH if sd else os.path.join(APP_DIR, "server_usage.db"))
    if not os.path.exists(db):
        return []
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute("""
            SELECT kind, label, pct, reset_text
            FROM quota_snapshot
            WHERE fetched_at = (SELECT MAX(fetched_at) FROM quota_snapshot)
        """).fetchall()
        conn.close()
        return [{"kind": r[0], "label": r[1], "pct": r[2], "reset_text": r[3]} for r in rows]
    except Exception:
        return []


def _apply_server_quota(windows):
    """用官网 quota 覆盖本地计算的百分比（订阅滚动窗口 vs 本地估算）。"""
    srv = _latest_server_quota()
    if not srv:
        return
    srv_map = {w["kind"]: w for w in srv}
    for w in windows:
        sw = srv_map.get(w["kind"])
        if not sw:
            continue
        w["pct"] = sw["pct"]
        w["used"] = gw.LIMITS[w["kind"]] * sw["pct"] / 100.0
        w["reset"] = sw["reset_text"]
        w["calibrated"] = True


def _collect_rows():
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
        srv_models = {r["model"] for r in srv_rows}
        latest_fetched = sd.read_latest_fetched_at() if sd is not None else 0
        local_rows = gw.read_opencode_all()
        # server 是权威源，但可能滞后：本地记录要么是 server 里没有的模型，
        # 要么时间晚于 server 最近一次抓取（本地 opencode.db 实时，server 是定时抓取）。
        # 非 go/zen 来源（gateway/kilo/router 等动态供应商）server 不覆盖，必须保留。
        extra = [r for r in local_rows
                 if r["model"] not in srv_models or r["ts"] > latest_fetched
                 or r.get("src") not in ("go", "zen")]
        return srv_rows + extra, cost_map
    go_rows = gw.read_opencode_all()
    # codex 日志增量读取（记住上次 id，避免每次全量）
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
    if sd is not None:
        try:
            sd.sync_all(cookie, ws, windows=res.get("windows"), max_pages=130)
        except Exception as e:
            return {"ok": False, "error": f"落库失败: {e}"}
    with CACHE["lock"]:
        CACHE["state"] = None
        CACHE["ts"] = 0
    return {"ok": True, "windows": res.get("windows")}


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
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[data-server] listening on http://127.0.0.1:{PORT}/api/state")
    srv.serve_forever()


if __name__ == "__main__":
    main()
