#!/usr/bin/env python3
"""views.py — 云端公式引擎 (POC)

核心想法:
  - 云端 (Cloudflare Worker + KV) 只提供一份「公式」: params(口径/限额/来源) + views(命名查询定义)。
  - 本地数据不动, 本地解释器按公式对 rows 执行过滤/切窗/分组/求和/后处理。
  - 改云端公式 → 所有用户本地展示随之变化, 无需 git pull。

公式来源 (FormulaStore):
  1. FORMULA_URL (env 或 config["formula_url"]) 指向云端/本地 mock, 定时拉取 + 失败回退内置默认;
  2. 无 URL → 内置默认公式 (DEFAULT_FORMULA), source="default"。

范围语义 (与当前 UI 完全一致):
  - 日期分桶: 本地时区 %Y-%m-%d
  - 截止日期 (字符串比较): today/1 = UTC 今天, 7d/7 = UTC 今天-6, 30d/30 = UTC 今天-29, all = 无过滤
  - tokens = input + output + cache.read + cache.write
  - post.meterRatio: 只作用于 totals.cost (daily 保持原始), 当 scope 为 all 或 source 在 params.sources.paid 时 ×params.meter.ratio
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta

DEFAULT_FORMULA = {
    "version": 4,
    "source_note": "内置默认公式 (离线回退, 与云端 v4 一致)",
    "params": {
        "meter": {
            "ratio": 1.4212,
            "note": "计量器≈账单×1.42（≤限额）",
        },
        "limits": {
            "session": 12.0,
            "weekly": 30.0,
            "monthly": 60.0,
            "credit_per_applied": 5.0,
        },
        "windows": {
            "session_ms": 18000000,
            "week_ms": 604800000,
        },
        "sources": {
            "paid": ["go"],
            "subset_of": {"gateway": "go"},
            "label": {"go": "OpenCode Go", "gateway": "AI Gateway",
                      "zen": "OpenCode Zen", "kilo": "Kilo", "router": "OpenRouter"},
            "order": ["go", "zen", "kilo", "router", "gateway"],
        },
        "free_models": {
            "whitelist": ["big-pickle"],
            "suffixes": ["-free", ":free", "/free"],
            "exclude": ["openrouter/free", "kilo-auto/free", "openrouter-free"],
        },
        "providers": {
            "src": {
                "opencode": "zen",
                "opencode-go": "go",
                "openkilo": "kilo",
                "tencent-tokenhub": "zen",
                "openrouter": "router",
            },
            "prefixes": [
                "tencent", "cohere", "nvidia", "google", "inclusionai",
                "opencode", "openkilo", "openrouter", "poolside", "stepfun",
                "openai", "tencent-tokenhub", "opencode-go",
            ],
            "aliases": {
                "nemotron-3-ultra-free": "nemotron-3-ultra-550b-a55b-free",
                "nemotron-3-nano-omni-30b-a3b-reasoning-free": "nemotron-3-nano-omni-30b-a3b-reasoning-free",
            },
        },
        "prices": {
            "grok-4.5": {"in": 2.00, "out": 6.00, "cr": 0.30, "cw": None},
            "gpt-5.6-luna": {"in": 0.20, "out": 1.20, "cr": 0.02, "cw": 0.25, "in_hi": 0.40, "out_hi": 1.80, "cr_hi": 0.04, "cw_hi": 0.50, "hi_above": 272000},
            "glm-5.2": {"in": 1.40, "out": 4.40, "cr": 0.26, "cw": None},
            "glm-5.1": {"in": 1.40, "out": 4.40, "cr": 0.26, "cw": None},
            "kimi-k3": {"in": 3.00, "out": 15.00, "cr": 0.30, "cw": None},
            "kimi-k2.7-code": {"in": 0.95, "out": 4.00, "cr": 0.19, "cw": None},
            "kimi-k2.6": {"in": 0.95, "out": 4.00, "cr": 0.16, "cw": None},
            "mimo-v2.5": {"in": 0.14, "out": 0.28, "cr": 0.0028, "cw": None},
            "mimo-v2.5-pro": {"in": 0.435, "out": 0.87, "cr": 0.003625, "cw": None},
            "minimax-m3": {"in": 0.30, "out": 1.20, "cr": 0.06, "cw": None},
            "minimax-m2.7": {"in": 0.30, "out": 1.20, "cr": 0.06, "cw": 0.375},
            "qwen3.8-max": {"in": 2.00, "out": 6.00, "cr": 0.25, "cw": 2.50},
            "qwen3.7-max": {"in": 2.50, "out": 7.50, "cr": 0.50, "cw": 3.125},
            "qwen3.7-plus": {"in": 0.40, "out": 1.60, "cr": 0.04, "cw": 0.50, "in_hi": 1.20, "out_hi": 4.80, "cr_hi": 0.12, "cw_hi": 1.50, "hi_above": 256000},
            "qwen3.6-plus": {"in": 0.50, "out": 3.00, "cr": 0.05, "cw": 0.625, "in_hi": 2.00, "out_hi": 6.00, "cr_hi": 0.20, "cw_hi": 2.50, "hi_above": 256000},
            "deepseek-v4-pro": {"in": 0.435, "out": 0.87, "cr": 0.003625, "cw": None},
            "deepseek-v4-flash": {"in": 0.14, "out": 0.28, "cr": 0.0028, "cw": None},
            "hy3": {"in": 0.14, "out": 0.58, "cr": 0.035, "cw": None},
        },
        "model_quotas": {
            "grok-4.5": 15, "gpt-5.6-luna": 15, "glm-5.2": 60, "glm-5.1": 60,
            "kimi-k3": 15, "kimi-k2.7-code": 60, "kimi-k2.6": 60,
            "mimo-v2.5": 60, "mimo-v2.5-pro": 15,
            "minimax-m3": 60, "minimax-m2.7": 60,
            "qwen3.8-max": 15, "qwen3.7-max": 60, "qwen3.7-plus": 60, "qwen3.6-plus": 60,
            "deepseek-v4-pro": 60, "deepseek-v4-flash": 60,
            "hy3": 60,
        },
        "req_limits": {
            "grok-4.5": [120, 300, 600],
            "gpt-5.6-luna": [2050, 5100, 10250],
            "glm-5.2": [880, 2150, 4300],
            "glm-5.1": [880, 2150, 4300],
            "kimi-k3": [110, 250, 490],
            "kimi-k2.7-code": [1350, 3380, 6750],
            "kimi-k2.6": [1150, 2880, 5750],
            "mimo-v2.5": [30100, 75200, 150400],
            "mimo-v2.5-pro": [3250, 8150, 16300],
            "minimax-m3": [3200, 8000, 16000],
            "minimax-m2.7": [3400, 8500, 17000],
            "qwen3.8-max": [160, 400, 810],
            "qwen3.7-max": [340, 840, 1690],
            "qwen3.7-plus": [4300, 10800, 21600],
            "qwen3.6-plus": [3300, 8200, 16300],
            "deepseek-v4-pro": [3450, 8550, 17150],
            "deepseek-v4-flash": [31650, 79050, 158150],
            "hy3": [4300, 10750, 21500],
        },
        "tokens_per_req": {
            "grok-4.5": 72820,
            "gpt-5.6-luna": 51220,
            "glm-5.2": 52850,
            "glm-5.1": 52850,
            "kimi-k3": 77850,
            "kimi-k2.7-code": 56070,
            "kimi-k2.6": 56070,
            "mimo-v2.5": 72625,
            "mimo-v2.5-pro": 87095,
            "minimax-m3": 56700,
            "minimax-m2.7": 55425,
            "qwen3.8-max": 66620,
            "qwen3.7-max": 66620,
            "qwen3.7-plus": 57690,
            "qwen3.6-plus": 57690,
            "deepseek-v4-pro": 83040,
            "deepseek-v4-flash": 69070,
            "hy3": 72625,
        },
        "display_names": {
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
        },
        "refresh": {"formula_s": 900},
    },
    "constants": {
        "weeks_per_month": 4.345,
        "periods_per_day": 6.0,
        "note": "魔法常数，云端可调，本地回退默认值"
    },
    "formulas": {
        "tok_agg": {"display": "token 总量", "source": "官方 usage_records", "expr": "tokens_in + tokens_out + tokens_cache", "params": ["tokens_in", "tokens_out", "tokens_cache"], "used_by": ["go-usage-widget.py", "data_server.py"]},
        "cost_agg": {"display": "原始账单 cost", "source": "官方 API 返回原始值", "expr": "Σcost", "params": ["cost"], "used_by": ["go-usage-widget.py"]},
        "meter_total": {"display": "官方口径总用量", "source": "官方 cost_summary + 云端 params.meter", "expr": "Σcost × meter.ratio  (当前 1.4212)", "params": ["cost", "meter_ratio", "scope_all", "paid_sources", "source"], "used_by": ["views.py"]},
        "model_quota": {"display": "模型月度额度", "source": "云端 params.model_quotas", "expr": "MODEL_QUOTAS[m] (缺省 LIMITS.monthly)", "params": ["model", "model_quotas", "monthly_limit"], "used_by": ["go-usage-widget.py"]},
        "est_req_cost": {"display": "官方估算次均费用", "source": "云端 params.model_quotas + req_limits", "expr": "model_quota / req_limits[2]", "params": ["model_quota", "req_limits"], "used_by": ["go-usage-widget.py"]},
        "est_tok_cost": {"display": "官方估算每 token 费用", "source": "est_req_cost + 云端 params.tokens_per_req", "expr": "est_req_cost / tokens_per_req[m]", "params": ["est_req_cost", "tokens_per_req"], "used_by": ["go-usage-widget.py"]},
        "model_used": {"display": "模型已用费用", "source": "官方 cost_map 权威 / 本地聚合", "expr": "cost_map[m] (go优先) 或 cost_total", "params": ["cost_map", "model", "source", "cost_total"], "used_by": ["go-usage-widget.py"]},
        "model_remain": {"display": "模型剩余额度", "source": "model_quota + model_used", "expr": "max(0, model_quota - model_used)", "params": ["model_quota", "model_used"], "used_by": ["go-usage-widget.py"]},
        "global_limit": {"display": "全局限额", "source": "官方 limits + sync_meta", "expr": "monthly + applied_credits × credit_per_applied", "params": ["monthly_limit", "applied_credits", "credit_per_applied"], "used_by": ["go-usage-widget.py"]},
        "used_all": {"display": "全局已用", "source": "官方 cost_summary", "expr": "Σcost (付费来源)", "params": ["used_all"], "used_by": ["go-usage-widget.py"]},
        "cq_weekly": {"display": "周配额", "source": "model_quota + 云端 params.weeks_per_month", "expr": "model_quota / weeks_per_month  (当前 4.345)", "params": ["model_quota", "weeks_per_month"], "used_by": ["go-usage-widget.py"]},
        "cq_session": {"display": "时段配额", "source": "model_quota + 云端 params.periods_per_day", "expr": "model_quota / periods_per_day  (当前 6.0)", "params": ["model_quota", "periods_per_day"], "used_by": ["go-usage-widget.py"]},
        "tq_monthly": {"display": "token 配额反推(月)", "source": "model_quota + 实际月度均价", "expr": "cq_monthly / avg_monthly_cost_per_token", "params": ["cq_monthly", "avg_monthly_cost_per_token"], "used_by": ["go-usage-widget.py"]},
        "tq_weekly": {"display": "token 配额反推(周)", "source": "model_quota + 实际周均价", "expr": "cq_weekly / avg_weekly_cost_per_token", "params": ["cq_weekly", "avg_weekly_cost_per_token"], "used_by": ["go-usage-widget.py"]},
        "tq_session": {"display": "token 配额反推(时段)", "source": "model_quota + 实际时段均价", "expr": "cq_session / avg_session_cost_per_token", "params": ["cq_session", "avg_session_cost_per_token"], "used_by": ["go-usage-widget.py"]},
        "tp_monthly": {"display": "token 使用百分比(月)", "source": "monthly_tok + tq_m", "expr": "min(100, monthly_tok / tq_m × 100)", "params": ["tok_monthly", "tq_monthly"], "used_by": ["go-usage-widget.py"]},
        "tp_weekly": {"display": "token 使用百分比(周)", "source": "weekly_tok + tq_w", "expr": "min(100, weekly_tok / tq_w × 100)", "params": ["tok_weekly", "tq_weekly"], "used_by": ["go-usage-widget.py"]},
        "tp_session": {"display": "token 使用百分比(时段)", "source": "session_tok + tq_s", "expr": "min(100, session_tok / tq_s × 100)", "params": ["tok_session", "tq_session"], "used_by": ["go-usage-widget.py"]},
        "effective_remain": {"display": "有效剩余", "source": "model_remain + global_remain", "expr": "min(model_remain, global_remain)", "params": ["model_remain", "global_remain"], "used_by": ["electron/app/index.html"]},
        "avg_per_req": {"display": "次均费用", "source": "实际已用 或 官方估算", "expr": "有数据时 c / usedCnt，否则 est_req_cost", "params": ["cost_of_period", "used_count", "est_req_cost"], "used_by": ["electron/app/index.html"]},
        "remain_cnt": {"display": "剩余次数", "source": "effectiveRemain + avgPerReq", "expr": "effective_remain / avg_per_req", "params": ["effective_remain", "avg_per_req"], "used_by": ["electron/app/index.html"]},
        "cache_hit": {"display": "缓存命中率", "source": "本地 usage_records", "expr": "cache_read / (cache_read + tokens_in) × 100", "params": ["cache_read", "tokens_in"], "used_by": ["go-usage-widget.py", "electron/app/index.html"]},
        "rate": {"display": "速率", "source": "本地 usage_records", "expr": "tok_sec_sum / tok_sec_n  (tok/s)", "params": ["tok_sec_sum", "tok_sec_n"], "used_by": ["go-usage-widget.py", "electron/app/index.html"]},
        "pct": {"display": "百分比", "source": "计算", "expr": "min(100, part / total × 100)", "params": ["part", "total"], "used_by": ["go-usage-widget.py", "electron/app/index.html"]},
        "token_quota_reverse": {"display": "token 配额反推(前端)", "source": "model_quota + 实际月度均价", "expr": "used_tokens + remain_cost / avg_cost_per_token", "params": ["used_tokens", "remain_cost", "avg_cost_per_token"], "used_by": ["electron/app/index.html"]},
        "dedup_rule": {"display": "去重规则", "source": "remote_rows(官方) + extra(本地)", "expr": "(model, src) 联合去重，官方优先", "params": [], "used_by": ["data_server.py"]},
        "supplier_agg": {"display": "供应商聚合", "source": "本地聚合（按 src）", "expr": "Σtokens / Σcount / Σcost", "params": [], "used_by": ["data_server.py"]}
    },
    "views": [
        {"id": "all_today",  "label": "全部 · 今天",   "scope": {"all": True}, "range": "today", "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "all_7d",     "label": "全部 · 近7天",  "scope": {"all": True}, "range": "7d",    "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "all_30d",    "label": "全部 · 近30天", "scope": {"all": True}, "range": "30d",   "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "all_all",    "label": "全部 · 全部",   "scope": {"all": True}, "range": "all",   "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "go_today",   "label": "GO · 今天",     "scope": {"source": "go"}, "range": "today", "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "go_7d",      "label": "GO · 近7天",    "scope": {"source": "go"}, "range": "7d",    "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "go_30d",     "label": "GO · 近30天",   "scope": {"source": "go"}, "range": "30d",   "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "go_all",     "label": "GO · 全部",     "scope": {"source": "go"}, "range": "all",   "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "gateway_all", "label": "AI Gateway · 全部", "scope": {"source": "gateway"}, "range": "all", "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": []},
        {"id": "all_daily",  "label": "全部 · 逐日",   "scope": {"all": True}, "range": "all", "group": "day",
         "agg": ["cost", "tokens", "count"], "post": []},
        {"id": "go_daily",   "label": "GO · 逐日",     "scope": {"source": "go"}, "range": "all", "group": "day",
         "agg": ["cost", "tokens", "count"], "post": []},
    ],
}

_RANGES = {"today": "today", "1": "today", "7d": "7d", "7": "7d",
           "30d": "30d", "30": "30d", "all": "all"}


class FormulaError(Exception):
    pass


class FormulaStore:
    """拉取/缓存/回退公式。url 可为 http(s) 或本地文件路径。"""

    def __init__(self, url=None, ttl=900):
        self.url = url or os.environ.get("FORMULA_URL") or "https://opencode-formula.opencode-widget.workers.dev/formula"
        self.ttl = ttl
        self._formula = None
        self._ts = 0.0
        self._meta = {"source": "default", "url": self.url, "error": None, "fetched_at": 0}

    def refresh(self, force=False):
        if self.url:
            try:
                data = self._fetch(self.url)
                f = json.loads(data)
                self._validate(f)
                self._formula = f
                self._meta = {"source": "cloud", "url": self.url, "error": None,
                              "fetched_at": int(time.time() * 1000)}
                return True
            except Exception as e:
                self._meta = {"source": "cloud" if self._formula is not None else "default",
                              "url": self.url,
                              "error": str(e), "fetched_at": self._meta.get("fetched_at", 0)}
        else:
            self._meta = {"source": "default", "url": "", "error": None,
                          "fetched_at": 0}
        if self._formula is None:
            self._formula = DEFAULT_FORMULA
        return False

    def get(self, force=False):
        if force or self._formula is None or time.time() - self._ts > self.ttl:
            self.refresh(force=force)
            self._ts = time.time()
        return self._formula

    def meta(self):
        return dict(self._meta)

    @staticmethod
    def _fetch(url):
        if url.startswith("http://") or url.startswith("https://"):
            req = urllib.request.Request(url, headers={
                "User-Agent": "opencode-widget/1.0 (+formula)",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.read().decode("utf-8")
        path = url
        if url.startswith("file://"):
            path = url[len("file://"):]
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        raise FormulaError(f"无法读取公式来源: {url}")

    @staticmethod
    def _validate(f):
        if not isinstance(f, dict):
            raise FormulaError("公式必须是 JSON 对象")
        if "params" not in f or "views" not in f:
            raise FormulaError("公式缺少 params 或 views")
        if "formulas" in f and not isinstance(f["formulas"], dict):
            raise FormulaError("formulas 必须是对象")


class ViewEngine:
    """按公式对 rows 执行 view 查询。norm/is_free/local_tz 注入自数据源, 保证口径一致。"""

    def __init__(self, formula, norm=None, is_free=None, local_tz=None):
        self.f = formula
        self.params = formula.get("params", {}) if isinstance(formula, dict) else {}
        self.meter = self.params.get("meter", {})
        self.sources = self.params.get("sources", {})
        self.subset = self.sources.get("subset_of", {})
        self.paid = set(self.sources.get("paid", []))
        self.ratio = float(self.meter.get("ratio", 1.0))
        # v5 分模型折算率: 官方 used = intercept + Σ(行消费 × rate)
        self.rates = dict(self.meter.get("rates") or {})
        self.rate_default = float(self.meter.get("rate_default", 1.0))
        self.rate_intercept = float(self.meter.get("rate_intercept", 0.0))
        # 动态抵扣总额 = 抵扣次数 × $5, 由宿主(data_server)按账户实际状态注入
        self.credit_deduct = 0.0
        self.norm = norm or (lambda m: m)
        self.is_free = is_free or (lambda m: False)
        self.local_tz = local_tz or datetime.now().astimezone().tzinfo

    def rate_for(self, model, src=None):
        if src and src not in self.paid:
            return 1.0
        return self.rates.get(self.norm(model), self.rate_default)

    def _day(self, ts):
        return datetime.fromtimestamp(ts / 1000, self.local_tz).strftime("%Y-%m-%d")

    @staticmethod
    def _cutoff(rng):
        r = _RANGES.get(rng or "all")
        if r is None or r == "all":
            return None
        today = datetime.now(timezone.utc)
        if r == "7d":
            today = today - timedelta(days=6)
        elif r == "30d":
            today = today - timedelta(days=29)
        return today.strftime("%Y-%m-%d")

    def resolve_view(self, vid):
        views = self.f.get("views", []) if isinstance(self.f, dict) else []
        for v in views:
            if v.get("id") == vid:
                return dict(v)
        dyn = self._parse_dynamic(vid)
        if dyn is None:
            raise FormulaError(f"未知 view: {vid}")
        return dyn

    def _parse_dynamic(self, vid):
        # model:<key>_<range|daily>
        if vid.startswith("model:"):
            rest = vid[len("model:"):]
            parts = rest.rsplit("_", 1)
            tail = parts[1] if len(parts) == 2 and parts[1] in _RANGES or (len(parts) == 2 and parts[1] == "daily") else "all"
            key = parts[0]
            if len(parts) == 2 and parts[1] == "daily":
                return {"id": vid, "label": key, "scope": {"model": key},
                        "range": "all", "group": "day", "agg": [], "post": []}
            return {"id": vid, "label": key, "scope": {"model": key},
                    "range": tail, "group": None, "agg": [], "post": []}
        # <scope>_<range|daily>
        if vid.endswith("_daily"):
            head = vid[: -len("_daily")]
            scope = self._scope_from(head)
            return {"id": vid, "label": head, "scope": scope,
                    "range": "all", "group": "day", "agg": [], "post": self._post_for(scope)}
        if "_" in vid:
            head, tail = vid.rsplit("_", 1)
            if tail in _RANGES:
                scope = self._scope_from(head)
                return {"id": vid, "label": head, "scope": scope,
                        "range": tail, "group": None, "agg": [], "post": self._post_for(scope)}
        return None

    def _post_for(self, scope):
        if scope.get("all") or scope.get("source") in self.paid:
            return [{"op": "meterRatio", "on": "cost"}]
        return []

    def _scope_from(self, head):
        if head == "all":
            return {"all": True}
        if head == "free":
            return {"free": True}
        return {"source": head}

    def _scope_rows(self, rows, scope):
        if not scope or scope.get("all"):
            sub = self.subset
            return [r for r in rows if r.get("src") not in sub]
        if "source" in scope:
            s = scope["source"]
            return [r for r in rows if r.get("src") == s]
        if "free" in scope:
            return [r for r in rows if self.is_free(r.get("model"))]
        if "model" in scope:
            key = scope["model"]
            if "|" in key:
                m, src = key.split("|", 1)
                return [r for r in rows
                        if r.get("src") == src and self.norm(r.get("model")) == m]
            m = self.norm(key)
            return [r for r in rows if self.norm(r.get("model")) == m]
        return []

    def execute(self, vid, rows, now_ms=None):
        view = self.resolve_view(vid)
        cutoff = self._cutoff(view.get("range"))
        scoped = self._scope_rows(rows, view.get("scope"))
        daily = {}
        for r in scoped:
            d = self._day(r["ts"])
            if cutoff is not None and (len(d) != 10 or d < cutoff):
                continue
            tk = r.get("tokens") or {}
            ti = tk.get("input", 0) or 0
            to = tk.get("output", 0) or 0
            cache = tk.get("cache") or {}
            tc = (cache.get("read", 0) or 0) + (cache.get("write", 0) or 0)
            cost = (r.get("cost") or 0.0) * self.rate_for(r.get("model"), r.get("src"))
            b = daily.setdefault(d, [0.0, 0, 0, 0, 0])
            b[0] += cost
            b[1] += 1
            b[2] += ti
            b[3] += to
            b[4] += tc
        totals = {"cost": 0.0, "tokens": 0, "input": 0, "output": 0,
                  "cache": 0, "count": 0, "days": len(daily)}
        series = []
        for d in sorted(daily):
            cost, count, ti, to, tc = daily[d]
            totals["cost"] += cost
            totals["tokens"] += ti + to + tc
            totals["input"] += ti
            totals["output"] += to
            totals["cache"] += tc
            totals["count"] += count
            series.append({"date": d, "cost": round(cost, 4), "count": count,
                           "tokens": int(ti + to + tc), "input": int(ti),
                           "output": int(to), "cache": int(tc)})
        self._apply_post(view, totals, series)
        return {
            "id": view.get("id", vid),
            "label": view.get("label", vid),
            "scope": view.get("scope"),
            "range": view.get("range"),
            "group": view.get("group"),
            "totals": totals,
            "daily": series,
        }

    def _apply_post(self, view, totals, series):
        for op in view.get("post", []):
            if op.get("op") == "meterRatio":
                # v6: 行级已按折算率累计, 这里补截距; 全周期视图再减动态抵扣(次数×$5)
                scope = view.get("scope") or {}
                hit = bool(scope.get("all")) or (scope.get("source") in self.paid)
                if hit:
                    deduct = self.credit_deduct if (view.get("range") == "all") else 0.0
                    totals["cost"] = round(max(0.0, totals["cost"] + self.rate_intercept - deduct), 4)
            elif op.get("op") == "round4":
                f = op.get("on", "cost")
                totals[f] = round(totals.get(f, 0), 4)
                for p in series:
                    if f in p:
                        p[f] = round(p[f], 4)