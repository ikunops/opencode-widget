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

# 官方"每次请求 token 数"估算 (输入 + 缓存 + 输出), 用于未使用模型的剩余折算
TOKENS_PER_REQ = {
    "grok-4.5":          1100 + 71500 + 220,
    "gpt-5.6-luna":      1000 + 50000 + 220,
    "glm-5.2":           700 + 52000 + 150,
    "glm-5.1":           700 + 52000 + 150,
    "kimi-k3":           1050 + 76500 + 300,
    "kimi-k2.7-code":    870 + 55000 + 200,
    "kimi-k2.6":         870 + 55000 + 200,
    "mimo-v2.5":         830 + 71500 + 295,
    "mimo-v2.5-pro":     790 + 86000 + 305,
    "minimax-m3":        510 + 56000 + 190,
    "minimax-m2.7":      300 + 55000 + 125,
    "qwen3.8-max":       420 + 66000 + 200,
    "qwen3.7-max":       420 + 66000 + 200,
    "qwen3.7-plus":      500 + 57000 + 190,
    "qwen3.6-plus":      500 + 57000 + 190,
    "deepseek-v4-pro":   750 + 82000 + 290,
    "deepseek-v4-flash": 790 + 68000 + 280,
    "hy3":               830 + 71500 + 295,
}

DISPLAY_NAMES = {
    "grok-4.5": "Grok 4.5", "gpt-5.6-luna": "GPT 5.6 Luna", "glm-5.2": "GLM-5.2",
    "glm-5.1": "GLM-5.1", "kimi-k3": "Kimi K3", "kimi-k2.7-code": "Kimi K2.7 Code",
    "kimi-k2.6": "Kimi K2.6", "mimo-v2.5": "MiMo-V2.5", "mimo-v2.5-pro": "MiMo-V2.5 Pro", "mimo-v2.5-free": "MiMo-V2.5",
    "minimax-m3": "MiniMax M3", "minimax-m2.7": "MiniMax M2.7", "qwen3.8-max": "Qwen3.8 Max",
    "qwen3.7-max": "Qwen3.7 Max", "qwen3.7-plus": "Qwen3.7 Plus", "qwen3.6-plus": "Qwen3.6 Plus",
    "deepseek-v4-pro": "DeepSeek V4 Pro", "deepseek-v4-flash": "DeepSeek V4 Flash", "hy3": "Hy3",
    "hy3-free": "Hy3", "hy3:free": "Hy3", "tencent/hy3:free": "Hy3",
    "ling-3.0-flash-free": "Ling 3.0 Flash", "nemotron-3-ultra-free": "Nemotron 3 Ultra",
    "cohere/north-mini-code:free": "North Mini Code", "north-mini-code-free": "North Mini Code",
    "google/lyria-3-pro-preview": "Lyria 3 Pro", "kilo-auto-free": "Kilo Auto",
    "nemotron-3-ultra-550b-a55b-free": "Nemotron 3 Ultra 550B",
    "nemotron-3-super-120b-a12b-free": "Nemotron 3 Super 120B", "gemma-4-31b-it-free": "Gemma 4 31B",
    "hy3-preview": "Hy3 Preview",
    "big-pickle": "Big Pickle",
    "nemotron-3-nano-omni-30b-a3b-reasoning-free": "Nemotron 3 Nano Omni",
    "nemotron-3.5-content-safety-free": "Nemotron 3.5 Safety",
    "nemotron-3-nano-30b-a3b-free": "Nemotron 3 Nano 30B",
    "laguna-s-2.1-free": "Laguna S 2.1",
    "laguna-xs-2.1-free": "Laguna XS 2.1",
    "step-3.7-flash-free": "Step 3.7 Flash",
    "longcat-2.0-free": "Longcat 2.0",
    "ling-3.0-tiny-free": "Ling 3.0 Tiny",
    "gemma-4-26b-a4b-it-free": "Gemma 4 26B",
    "nemotron-nano-12b-v2-vl-free": "Nemotron Nano 12B VL",
    "nemotron-nano-9b-v2-free": "Nemotron Nano 9B",
    "gpt-oss-20b-free": "GPT-OSS 20B",
    "deepseek-v4-flash-free": "DeepSeek V4 Flash",
    "mimo-v2.5-free": "MiMo-V2.5",
}

FREE_SUFFIXES = ("-free", ":free", "/free")
FREE_WHITELIST = {"big-pickle", "hy3", "hy3-preview"}

# 排除项: 路由占位/非真实模型 (不以 free 后缀过滤它们, 单独排除)
FREE_EXCLUDE = {"openrouter/free", "kilo-auto/free", "openrouter-free"}

# 动态扫描 opencode 模型列表中的 free 模型 (缓存到 config, TTL 6h)
_FREE_SCAN_TTL = 6 * 3600 * 1000


def scan_free_models(cfg=None, force=False):
    """调用 `opencode models` 解析所有带 free 后缀的模型, 返回归一化后的规范名列表。
    结果缓存到 config["free_models_cache"] (含时间戳), TTL 内不重复调用 CLI。
    特殊免费模型 (big-pickle/hy3 等, 不带 free 后缀) 由 FREE_WHITELIST 补充。"""
    cfg = cfg if cfg is not None else load_config()
    cached = cfg.get("free_models_cache") or {}
    if not force and cached.get("ts") and (time.time() * 1000 - cached["ts"] < _FREE_SCAN_TTL):
        return list(cached.get("models") or [])
    models = set()
    try:
        import subprocess
        # 优先 npm 全局的 opencode.cmd (python 子进程 PATH 通常不含 npm 目录)
        candidates = [
            os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm", "opencode.cmd"),
            "opencode.cmd",
            "opencode",
        ]
        out = None
        for cand in candidates:
            try:
                out = subprocess.run([cand, "models"], capture_output=True, text=True,
                                     timeout=30, encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if out.returncode == 0:
                    break
            except Exception:
                continue
        if out and out.returncode == 0:
            for line in (out.stdout or "").splitlines():
                line = line.strip()
                if not line or "/" not in line:
                    continue
                low = line.lower()
                if any(s in low for s in FREE_SUFFIXES):
                    nm = norm_model(line)
                    if nm and nm not in FREE_EXCLUDE and nm != "free" and not nm.startswith("openrouter-"):
                        models.add(nm)
    except Exception:
        pass
    # 补充特殊免费模型 (无 free 后缀)
    for w in FREE_WHITELIST:
        models.add(w)
    models = sorted(models)
    try:
        cfg["free_models_cache"] = {"ts": int(time.time() * 1000), "models": models}
        save_config(cfg)
    except Exception:
        pass
    return models
PROVIDER_SRC = {"opencode": "zen", "opencode-go": "go", "openkilo": "kilo",
                "tencent-tokenhub": "zen", "openrouter": "router"}
# 模型 ID 中的 provider 前缀 (区别于模型名本身, 如 kilo-auto 不是前缀)
PROVIDER_PREFIXES = {"tencent", "cohere", "nvidia", "google", "inclusionai",
                     "opencode", "openkilo", "openrouter", "poolside", "stepfun", "openai"}

# 模型别名: 不同来源/写法的同一模型合并 (key -> 规范名)
MODEL_ALIASES = {
    "nemotron-3-ultra-free": "nemotron-3-ultra-550b-a55b-free",
    "nemotron-3-nano-omni-30b-a3b-reasoning-free": "nemotron-3-nano-omni-30b-a3b-reasoning-free",
}


def is_free_model(model):
    m = (model or "").strip().lower()
    if not m:
        return False
    if m in FREE_WHITELIST:
        return True
    return any(s in m for s in FREE_SUFFIXES)


def norm_model(model):
    """归一化模型名: 仅剥离已知的 provider 前缀 (tencent/, cohere/, nvidia/...),
    并统一 free 标记, 使同一模型的不同来源/写法合并为一条
    (如 hy3-free / tencent/hy3:free / hy3:free -> hy3-free)。
    注意: kilo-auto/free 的 kilo-auto 是模型名一部分, 不是 provider 前缀, 故保留。
    若去 free 后缀后的基类在 FREE_WHITELIST 中 (如 hy3), 则进一步去掉 -free, 使 hy3-free 与 hy3 合并。"""
    m = (model or "").strip()
    # 循环剥已知 provider 前缀 (支持 openkilo/nvidia/... 多层)
    changed = True
    while changed:
        changed = False
        for p in PROVIDER_PREFIXES:
            if m.startswith(p + "/"):
                m = m[len(p) + 1:]
                changed = True
                break
    m = m.replace(":free", "-free").replace("/free", "-free")
    base = m[:-5] if m.endswith("-free") else m
    if base in FREE_WHITELIST:
        return base
    return MODEL_ALIASES.get(m, m)


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


def read_opencode_all(srcs=None):
    rows = []
    if not os.path.exists(OPENCODE_DB):
        return rows
    if srcs:
        srcs = set(srcs)
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
            if srcs is not None and src not in srcs:
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
    paid = [(t, c) for t, c in costs if c]
    earliest = min((t for t, _ in paid), default=now_ms)

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


def model_stats(rows, now_ms, cost_map=None):
    cost_map = cost_map or {}
    stats = {}
    session_start = now_ms - SESSION_MS
    ws, we = week_bounds(now_ms)
    paid_rows = [r for r in rows if r.get("cost")]
    ms, me = month_bounds(now_ms, min((r["ts"] for r in paid_rows), default=now_ms))
    # 先统计每个模型的总 cost: 有付费记录的模型归 go 组, 完全无 cost 的归 free (如 kilo 音频模型)
    cost_by_model = {}
    for r in rows:
        nm = norm_model(r["model"])
        if r.get("cost"):
            cost_by_model[nm] = cost_by_model.get(nm, 0.0) + r["cost"]
    for r in rows:
        m = r["model"]
        nm = norm_model(m)
        src = r.get("src") or "?"
        free = is_free_model(nm) or (cost_by_model.get(nm, 0.0) <= 0)
        key = (nm, "free") if free else (nm, "go")
        if key not in stats:
            stats[key] = {"count_s": 0, "count_w": 0, "count_m": 0, "count_total": 0,
                          "cost_s": 0.0,
                          "cost_w": 0.0, "cost_m": 0.0, "cost_total": 0.0,
                          "tokens_in": 0, "tokens_out": 0, "tokens_cache": 0,
                          "tokens_in_s": 0, "tokens_out_s": 0, "tokens_cache_s": 0,
                          "tokens_in_w": 0, "tokens_out_w": 0, "tokens_cache_w": 0,
                          "tokens_in_m": 0, "tokens_out_m": 0, "tokens_cache_m": 0,
                          "srcs": set()}
        s = stats[key]
        s["srcs"].add(src)
        s["count_total"] += 1
        if session_start <= r["ts"] < now_ms:
            s["count_s"] += 1
            if r["cost"] is not None:
                s["cost_s"] += r["cost"]
        if ws <= r["ts"] < we:
            s["count_w"] += 1
            if r["cost"] is not None:
                s["cost_w"] += r["cost"]
        if ms <= r["ts"] < me:
            s["count_m"] += 1
            if r["cost"] is not None:
                s["cost_m"] += r["cost"]
        tk = r.get("tokens") or {}
        ti = tk.get("input", 0) or 0
        to = tk.get("output", 0) or 0
        cache = tk.get("cache") or {}
        tc = (cache.get("read", 0) or 0) + (cache.get("write", 0) or 0)
        s["tokens_in"] += ti
        s["tokens_out"] += to
        s["tokens_cache"] += tc
        if session_start <= r["ts"] < now_ms:
            s["tokens_in_s"] += ti
            s["tokens_out_s"] += to
            s["tokens_cache_s"] += tc
        if ws <= r["ts"] < we:
            s["tokens_in_w"] += ti
            s["tokens_out_w"] += to
            s["tokens_cache_w"] += tc
        if ms <= r["ts"] < me:
            s["tokens_in_m"] += ti
            s["tokens_out_m"] += to
            s["tokens_cache_m"] += tc
        if r["cost"] is not None:
            s["cost_total"] += r["cost"]

    src_count = {}
    for (m, grp) in stats:
        if grp == "free":
            src_count[m] = src_count.get(m, 0) + 1

    out = []
    for (m, grp), s in stats.items():
        is_free = (grp == "free")
        group = "free" if is_free else "go"
        base = m.replace("-free", "").replace(":free", "")
        name = DISPLAY_NAMES.get(m) or DISPLAY_NAMES.get(base) or base
        if is_free and src_count.get(m, 0) > 1:
            name = f"{name} ({len(s['srcs'])})"
        if not is_free:
            if m in cost_map:
                s["cost_total"] = cost_map[m]
            # 反推 token 配额: go 模型只有 $60/月 费用配额, 用当月均价换算 token 配额
            # 用行级当月费用 (与 tokens_m 同一窗口), 避免 cost_map 跨月汇总导致比例失真
            monthly_cost = s["cost_m"] if s["cost_m"] > 0 else cost_map.get(m, 0)
            monthly_tok = s["tokens_in_m"] + s["tokens_out_m"] + s["tokens_cache_m"]
            if monthly_tok > 0 and monthly_cost > 0:
                avg = monthly_cost / monthly_tok  # USD / token
                cq_m = 60.0
                cq_w = 60.0 / 4.345
                cq_s = 60.0 / (30 * 24 / 5)
                s["tq_m"] = cq_m / avg
                s["tq_w"] = cq_w / avg
                s["tq_s"] = cq_s / avg
                tok_m = monthly_tok
                tok_w = s["tokens_in_w"] + s["tokens_out_w"] + s["tokens_cache_w"]
                tok_s = s["tokens_in_s"] + s["tokens_out_s"] + s["tokens_cache_s"]
                s["tp_m"] = min(100.0, tok_m / s["tq_m"] * 100) if s["tq_m"] else 0.0 
                s["tp_w"] = min(100.0, tok_w / s["tq_w"] * 100) if s["tq_w"] else 0.0
                s["tp_s"] = min(100.0, tok_s / s["tq_s"] * 100) if s["tq_s"] else 0.0
        out_srcs = sorted(s["srcs"])
        # 官方估算: 次均费用 = 月配额$60 / 官方月请求数; 每 token 费用 = 次均 / 官方每次请求 token
        req_lim = REQ_LIMITS.get(m)
        est_req_cost = (60.0 / req_lim[2]) if req_lim and req_lim[2] else 0.0
        est_tok_cost = (est_req_cost / TOKENS_PER_REQ[m]) if (m in TOKENS_PER_REQ and TOKENS_PER_REQ[m]) else 0.0
        out.append({
            "model": m,
            "source": out_srcs[0] if len(out_srcs) == 1 else "/".join(out_srcs),
            "key": f"{m}|{grp}",
            "is_free": is_free,
            "group": group,
            "name": name,
            "count_s": s["count_s"], "count_w": s["count_w"], "count_m": s["count_m"],
            "count_total": s["count_total"],
            "cost_s": s["cost_s"], "cost_w": s["cost_w"], "cost_m": s["cost_m"],
            "cost_total": s["cost_total"],
            "tokens_in": s["tokens_in"], "tokens_out": s["tokens_out"],
            "tokens_cache": s["tokens_cache"],
            "tokens_in_s": s["tokens_in_s"], "tokens_out_s": s["tokens_out_s"], "tokens_cache_s": s["tokens_cache_s"],
            "tokens_in_w": s["tokens_in_w"], "tokens_out_w": s["tokens_out_w"], "tokens_cache_w": s["tokens_cache_w"],
            "tokens_in_m": s["tokens_in_m"], "tokens_out_m": s["tokens_out_m"], "tokens_cache_m": s["tokens_cache_m"],
            "tq_s": s.get("tq_s", 0.0), "tq_w": s.get("tq_w", 0.0), "tq_m": s.get("tq_m", 0.0),
            "tp_s": s.get("tp_s", 0.0), "tp_w": s.get("tp_w", 0.0), "tp_m": s.get("tp_m", 0.0),
            "req_lim": req_lim,
            "est_req_cost": est_req_cost,
            "est_tok_cost": est_tok_cost,
        })
    # 注入扫描到的 free 模型 (无使用记录也显示, 带 used 标记)
    existing = {x["model"] for x in out}
    free_list = scan_free_models()
    for fm in free_list:
        if fm not in existing:
            out.append({
                "model": fm, "source": "known", "key": f"{fm}|free",
                "is_free": True, "group": "free",
                "name": DISPLAY_NAMES.get(fm, fm),
                "used": False,
                "count_s": 0, "count_w": 0, "count_m": 0, "count_total": 0,
                "cost_s": 0.0, "cost_w": 0.0, "cost_m": 0.0, "cost_total": 0.0,
                "tokens_in": 0, "tokens_out": 0, "tokens_cache": 0,
                "tokens_in_s": 0, "tokens_out_s": 0, "tokens_cache_s": 0,
                "tokens_in_w": 0, "tokens_out_w": 0, "tokens_cache_w": 0,
                "tokens_in_m": 0, "tokens_out_m": 0, "tokens_cache_m": 0,
                "tq_s": 0.0, "tq_w": 0.0, "tq_m": 0.0,
                "tp_s": 0.0, "tp_w": 0.0, "tp_m": 0.0,
                "req_lim": None, "est_req_cost": 0.0, "est_tok_cost": 0.0,
            })
    for x in out:
        if x["group"] == "free" and "used" not in x:
            x["used"] = True
    out.sort(key=lambda x: -x["count_s"])
    return out


def model_history(rows, days=14):
    buckets = {}
    now_ms = int(time.time() * 1000)
    start = now_ms - days * 86400 * 1000
    cost_by_model = {}
    for r in rows:
        if r["ts"] < start:
            continue
        if r.get("cost"):
            cost_by_model[r["model"]] = cost_by_model.get(r["model"], 0.0) + r["cost"]
    for r in rows:
        if r["ts"] < start:
            continue
        d = datetime.fromtimestamp(r["ts"] / 1000, timezone.utc).strftime("%m-%d")
        m = r["model"]
        src = r.get("src") or "?"
        free = is_free_model(m) or (cost_by_model.get(m, 0.0) <= 0)
        key = (m, src) if free else (m, "go")
        b = buckets.setdefault(key, {}).setdefault(d, [0.0, 0, 0, 0, 0])
        b[0] += r["cost"] if r["cost"] is not None else 0.0
        b[1] += 1
        tk = r.get("tokens") or {}
        b[2] += tk.get("input", 0) or 0
        b[3] += tk.get("output", 0) or 0
        cache = tk.get("cache") or {}
        b[4] += (cache.get("read", 0) or 0) + (cache.get("write", 0) or 0)

    src_count = {}
    for (m, src) in buckets:
        if is_free_model(m):
            src_count[m] = src_count.get(m, 0) + 1

    out = []
    for (m, src), days_map in buckets.items():
        series = [{"date": k, "cost": round(v[0], 4), "count": v[1],
                   "tokens_in": v[2], "tokens_out": v[3], "tokens_cache": v[4]}
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
        now_ms = int(time.time() * 1000)
        rows, cost_map = self._collect_rows()
        with self.lock:
            self.rows = rows
            self.windows = build_windows(self.rows, now_ms)
            self._apply_calibration(self.windows, now_ms)
            self.stats = model_stats(self.rows, now_ms, cost_map)
            self.history = model_history(self.rows)
            self.last_refresh = now_ms
        self._try_server_sync()

    def _collect_rows(self):
        """收集行数据。服务器数据可用时: go 组以服务器 usage_records 为权威 (含 VS Code/Codex/所有 agent),
        本地 opencode.db 补充服务器没有的模型 (如 hy3 等通过 opencode/zen 使用的免费模型), 避免覆盖服务器已有模型造成重复计数。
        服务器不可用时回退本地 + codex 日志。"""
        srv_rows = []
        try:
            import server_data as sd
            srv_rows = sd.read_server_rows()
        except Exception:
            pass
        if srv_rows:
            cost_map = {}
            try:
                import server_data as sd
                cost_map = sd.read_cost_map()
            except Exception:
                pass
            srv_models = {r["model"] for r in srv_rows}
            local_rows = read_opencode_all()
            extra = [r for r in local_rows if r["model"] not in srv_models]
            return srv_rows + extra, cost_map

        go_rows = read_opencode_all()
        cx_rows, new_id = read_codex_logs(self.last_log_id)
        if new_id > self.last_log_id:
            self.last_log_id = new_id
            self.cfg["codex_log_id"] = new_id
            save_config(self.cfg)
        return go_rows + cx_rows, {}

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
                sd.sync_all(cookie, ws, windows=windows, max_pages=130)
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
        return self.cfg.get("opacity", 0.7)

    def save_opacity(self, v):
        val = max(0.1, min(1.0, float(v)))
        self.cfg["opacity"] = val
        save_config(self.cfg)
        return True

    def apply_opacity(self):
        return True

    def open_console(self):
        try:
            self._ensure_console()
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

    def _ensure_console(self):
        """按需创建 Console 窗口 (懒加载)，避免启动时闪出。"""
        if self._console_win is not None:
            return
        try:
            import webview
            self._console_win = webview.create_window(
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
        except Exception:
            self._console_win = None

    def grab_cookie(self):
        auth_val = read_auth_cookie_from_webdata()
        source = "webdata"
        if not auth_val:
            self._ensure_console()
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
