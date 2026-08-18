"""formula_registry.py — 单一公式注册表

设计:
  - 定义/追溯: cloud formula.json + 本地 DEFAULT_FORMULA 兜底
  - 执行: 本地 Python 函数 (云端不执行任意代码)
  - 按需取用: compute(id, **kw) 或 FORMULA_TABLE() 生成文档/xlsx/API
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


# ========== 本地默认实现 ==========


def _tok_agg(**kw: Any) -> float:
    return (kw.get("tokens_in") or 0) + (kw.get("tokens_out") or 0) + (kw.get("tokens_cache") or 0)


def _cost_agg(**kw: Any) -> float:
    return kw.get("cost") or 0.0


def _meter_total(**kw: Any) -> float:
    ratio = kw.get("meter_ratio", 1.0) or 1.0
    scope_all = kw.get("scope_all", False)
    paid = kw.get("paid_sources") or []
    src = kw.get("source", "")
    cost = kw.get("cost") or 0.0
    if scope_all or src in paid:
        return cost * ratio
    return cost


def _model_quota(**kw: Any) -> float:
    m = kw.get("model", "")
    quotas = kw.get("model_quotas") or {}
    fallback = kw.get("monthly_limit")
    return float(quotas.get(m, fallback) if fallback is not None else 0.0)


def _est_req_cost(**kw: Any) -> float:
    q = kw.get("model_quota")
    rl = kw.get("req_limits")
    if q is None or not rl or len(rl) < 3 or not rl[2]:
        return 0.0
    return q / rl[2]


def _est_tok_cost(**kw: Any) -> float:
    erc = kw.get("est_req_cost", 0.0)
    tpr = kw.get("tokens_per_req")
    if erc and tpr:
        return erc / tpr
    return 0.0


def _model_used(**kw: Any) -> float:
    cost_map = kw.get("cost_map") or {}
    m = kw.get("model", "")
    src = kw.get("source", "")
    cost_total = kw.get("cost_total") or 0.0
    if src == "go" and m in cost_map:
        return cost_map[m]
    return cost_total


def _model_remain(**kw: Any) -> float:
    return max(0.0, (kw.get("model_quota") or 0.0) - (kw.get("model_used") or 0.0))


def _global_limit(**kw: Any) -> float:
    monthly = kw.get("monthly_limit", 0.0) or 0.0
    applied = kw.get("applied_credits", 0) or 0
    credit = kw.get("credit_per_applied", 0.0) or 0.0
    return monthly + applied * credit


def _used_all(**kw: Any) -> float:
    return kw.get("used_all") or 0.0


def _cq_weekly(**kw: Any) -> float:
    q = kw.get("model_quota") or 0.0
    return q / (kw.get("weeks_per_month") or 4.345)


def _cq_session(**kw: Any) -> float:
    q = kw.get("model_quota") or 0.0
    return q / (kw.get("periods_per_day") or 6.0)


def _tq_monthly(**kw: Any) -> float:
    avg = kw.get("avg_monthly_cost_per_token")
    cq = kw.get("cq_monthly")
    if avg and cq is not None and avg > 0:
        return cq / avg
    return 0.0


def _tq_weekly(**kw: Any) -> float:
    avg = kw.get("avg_weekly_cost_per_token")
    cq = kw.get("cq_weekly")
    if avg and cq is not None and avg > 0:
        return cq / avg
    return 0.0


def _tq_session(**kw: Any) -> float:
    avg = kw.get("avg_session_cost_per_token")
    cq = kw.get("cq_session")
    if avg and cq is not None and avg > 0:
        return cq / avg
    return 0.0


def _tp_monthly(**kw: Any) -> float:
    return min(100.0, (kw.get("tok_monthly") or 0) / (kw.get("tq_monthly") or 1) * 100)


def _tp_weekly(**kw: Any) -> float:
    return min(100.0, (kw.get("tok_weekly") or 0) / (kw.get("tq_weekly") or 1) * 100)


def _tp_session(**kw: Any) -> float:
    return min(100.0, (kw.get("tok_session") or 0) / (kw.get("tq_session") or 1) * 100)


def _effective_remain(**kw: Any) -> float:
    return min(kw.get("model_remain") or 0.0, kw.get("global_remain") or 0.0)


def _avg_per_req(**kw: Any) -> float:
    c = kw.get("cost_of_period") or 0.0
    used_cnt = kw.get("used_count") or 0
    if c > 0 and used_cnt > 0:
        return c / used_cnt
    return kw.get("est_req_cost") or 0.0


def _remain_cnt(**kw: Any) -> float:
    er = kw.get("effective_remain") or 0.0
    apr = kw.get("avg_per_req") or 0.0
    if apr > 0:
        return er / apr
    return 0.0


def _cache_hit(**kw: Any) -> float:
    cr = kw.get("cache_read") or 0
    ti = kw.get("tokens_in") or 0
    if cr + ti > 0:
        return cr / (cr + ti) * 100
    return 0.0


def _rate(**kw: Any) -> float:
    n = kw.get("tok_sec_n") or 0
    if n:
        return (kw.get("tok_sec_sum") or 0.0) / n
    return 0.0


def _pct(**kw: Any) -> float:
    part = kw.get("part") or 0
    total = kw.get("total") or 1
    return min(100.0, part / total * 100)


def _token_quota_reverse(**kw: Any) -> float:
    used = kw.get("used_tokens") or 0
    remain_cost = kw.get("remain_cost") or 0.0
    avg = kw.get("avg_cost_per_token") or 0.0
    if avg > 0:
        return used + remain_cost / avg
    return used


# ========== 注册表 ==========

FORMULAS: Dict[str, Dict[str, Any]] = {
    "tok_agg": {
        "display": "token 总量",
        "source": "官方 usage_records",
        "expr": "tokens_in + tokens_out + tokens_cache",
        "func": _tok_agg,
        "params": ["tokens_in", "tokens_out", "tokens_cache"],
        "used_by": ["go-usage-widget.py", "data_server.py", "xlsx", "index.html"],
    },
    "cost_agg": {
        "display": "原始账单 cost",
        "source": "官方 API 返回原始值",
        "expr": "Σcost",
        "func": _cost_agg,
        "params": ["cost"],
        "used_by": ["go-usage-widget.py", "views.py", "xlsx"],
    },
    "meter_total": {
        "display": "官方口径总用量",
        "source": "官方 cost_summary + 云端 params.meter",
        "expr": "Σcost × meter.ratio  (当前 1.4212)",
        "func": _meter_total,
        "params": ["cost", "meter_ratio", "scope_all", "paid_sources", "source"],
        "used_by": ["views.py", "/api/state", "小屏"],
    },
    "model_quota": {
        "display": "模型月度额度",
        "source": "云端 params.model_quotas",
        "expr": "MODEL_QUOTAS[m] (缺省 LIMITS.monthly)",
        "func": _model_quota,
        "params": ["model", "model_quotas", "monthly_limit"],
        "used_by": ["go-usage-widget.py", "electron/app/index.html"],
    },
    "est_req_cost": {
        "display": "官方估算次均费用",
        "source": "云端 params.model_quotas + req_limits",
        "expr": "model_quota / req_limits[2]",
        "func": _est_req_cost,
        "params": ["model_quota", "req_limits"],
        "used_by": ["go-usage-widget.py", "xlsx", "README"],
    },
    "est_tok_cost": {
        "display": "官方估算每 token 费用",
        "source": "est_req_cost + 云端 params.tokens_per_req",
        "expr": "est_req_cost / tokens_per_req[m]",
        "func": _est_tok_cost,
        "params": ["est_req_cost", "tokens_per_req"],
        "used_by": ["go-usage-widget.py", "xlsx"],
    },
    "model_used": {
        "display": "模型已用费用",
        "source": "官方 cost_map 权威 / 本地聚合",
        "expr": "cost_map[m] (go优先) 或 cost_total",
        "func": _model_used,
        "params": ["cost_map", "model", "source", "cost_total"],
        "used_by": ["go-usage-widget.py", "electron/app/index.html"],
    },
    "model_remain": {
        "display": "模型剩余额度",
        "source": "model_quota + model_used",
        "expr": "max(0, model_quota - model_used)",
        "func": _model_remain,
        "params": ["model_quota", "model_used"],
        "used_by": ["go-usage-widget.py", "electron/app/index.html", "xlsx"],
    },
    "global_limit": {
        "display": "全局限额",
        "source": "官方 limits + sync_meta",
        "expr": "monthly + applied_credits × credit_per_applied",
        "func": _global_limit,
        "params": ["monthly_limit", "applied_credits", "credit_per_applied"],
        "used_by": ["go-usage-widget.py", "data_server.py", "xlsx"],
    },
    "used_all": {
        "display": "全局已用",
        "source": "官方 cost_summary",
        "expr": "Σcost (付费来源)",
        "func": _used_all,
        "params": ["used_all"],
        "used_by": ["go-usage-widget.py", "electron/app/index.html"],
    },
    "cq_weekly": {
        "display": "周配额",
        "source": "model_quota + 云端 params.weeks_per_month",
        "expr": "model_quota / weeks_per_month  (当前 4.345)",
        "func": _cq_weekly,
        "params": ["model_quota", "weeks_per_month"],
        "used_by": ["go-usage-widget.py", "data_server.py"],
    },
    "cq_session": {
        "display": "时段配额",
        "source": "model_quota + 云端 params.periods_per_day",
        "expr": "model_quota / periods_per_day  (当前 6.0)",
        "func": _cq_session,
        "params": ["model_quota", "periods_per_day"],
        "used_by": ["go-usage-widget.py", "data_server.py"],
    },
    "tq_monthly": {
        "display": "token 配额反推(月)",
        "source": "model_quota + 实际月度均价",
        "expr": "cq_monthly / avg_monthly_cost_per_token",
        "func": _tq_monthly,
        "params": ["cq_monthly", "avg_monthly_cost_per_token"],
        "used_by": ["go-usage-widget.py", "data_server.py"],
    },
    "tq_weekly": {
        "display": "token 配额反推(周)",
        "source": "model_quota + 实际周均价",
        "expr": "cq_weekly / avg_weekly_cost_per_token",
        "func": _tq_weekly,
        "params": ["cq_weekly", "avg_weekly_cost_per_token"],
        "used_by": ["go-usage-widget.py", "data_server.py"],
    },
    "tq_session": {
        "display": "token 配额反推(时段)",
        "source": "model_quota + 实际时段均价",
        "expr": "cq_session / avg_session_cost_per_token",
        "func": _tq_session,
        "params": ["cq_session", "avg_session_cost_per_token"],
        "used_by": ["go-usage-widget.py", "data_server.py"],
    },
    "tp_monthly": {
        "display": "token 使用百分比(月)",
        "source": "monthly_tok + tq_m",
        "expr": "min(100, monthly_tok / tq_m × 100)",
        "func": _tp_monthly,
        "params": ["tok_monthly", "tq_monthly"],
        "used_by": ["go-usage-widget.py", "data_server.py"],
    },
    "tp_weekly": {
        "display": "token 使用百分比(周)",
        "source": "weekly_tok + tq_w",
        "expr": "min(100, weekly_tok / tq_w × 100)",
        "func": _tp_weekly,
        "params": ["tok_weekly", "tq_weekly"],
        "used_by": ["go-usage-widget.py", "data_server.py"],
    },
    "tp_session": {
        "display": "token 使用百分比(时段)",
        "source": "session_tok + tq_s",
        "expr": "min(100, session_tok / tq_s × 100)",
        "func": _tp_session,
        "params": ["tok_session", "tq_session"],
        "used_by": ["go-usage-widget.py", "data_server.py"],
    },
    "effective_remain": {
        "display": "有效剩余",
        "source": "model_remain + global_remain",
        "expr": "min(model_remain, global_remain)",
        "func": _effective_remain,
        "params": ["model_remain", "global_remain"],
        "used_by": ["electron/app/index.html", "data_server.py"],
    },
    "avg_per_req": {
        "display": "次均费用",
        "source": "实际已用 或 官方估算",
        "expr": "有数据时 c / usedCnt，否则 est_req_cost",
        "func": _avg_per_req,
        "params": ["cost_of_period", "used_count", "est_req_cost"],
        "used_by": ["electron/app/index.html", "data_server.py"],
    },
    "remain_cnt": {
        "display": "剩余次数",
        "source": "effectiveRemain + avgPerReq",
        "expr": "effective_remain / avg_per_req",
        "func": _remain_cnt,
        "params": ["effective_remain", "avg_per_req"],
        "used_by": ["electron/app/index.html", "data_server.py"],
    },
    "cache_hit": {
        "display": "缓存命中率",
        "source": "本地 usage_records",
        "expr": "cache_read / (cache_read + tokens_in) × 100",
        "func": _cache_hit,
        "params": ["cache_read", "tokens_in"],
        "used_by": ["go-usage-widget.py", "electron/app/index.html"],
    },
    "rate": {
        "display": "速率",
        "source": "本地 usage_records",
        "expr": "tok_sec_sum / tok_sec_n  (tok/s)",
        "func": _rate,
        "params": ["tok_sec_sum", "tok_sec_n"],
        "used_by": ["go-usage-widget.py", "electron/app/index.html"],
    },
    "pct": {
        "display": "百分比",
        "source": "计算",
        "expr": "min(100, part / total × 100)",
        "func": _pct,
        "params": ["part", "total"],
        "used_by": ["go-usage-widget.py", "electron/app/index.html", "data_server.py"],
    },
    "token_quota_reverse": {
        "display": "token 配额反推(前端)",
        "source": "model_quota + 实际月度均价",
        "expr": "used_tokens + remain_cost / avg_cost_per_token",
        "func": _token_quota_reverse,
        "params": ["used_tokens", "remain_cost", "avg_cost_per_token"],
        "used_by": ["electron/app/index.html"],
    },
    "dedup_rule": {
        "display": "去重规则",
        "source": "remote_rows(官方) + extra(本地)",
        "expr": "(model, src) 联合去重，官方优先",
        "func": None,
        "params": [],
        "used_by": ["data_server.py"],
    },
    "supplier_agg": {
        "display": "供应商聚合",
        "source": "本地聚合（按 src）",
        "expr": "Σtokens / Σcount / Σcost",
        "func": None,
        "params": [],
        "used_by": ["data_server.py", "xlsx"],
    },
}

# 云端可选元数据覆盖（本地默认兜底）
_FORMULA_META: Dict[str, Dict[str, Any]] = {}
_ACTIVE: Dict[str, Dict[str, Any]] = {}


def apply_formulas(cloud_formulas: Optional[Dict[str, Any]]) -> None:
    """合并云端公式元数据到本地注册表。
    云端只提供 display/source/expr/enabled 等元数据; func 永远保留本地实现。
    与 apply_params_to_gw 同一语义: 缺 key 保持本地默认。
    """
    global _FORMULA_META, _ACTIVE
    _FORMULA_META = {}
    cf = cloud_formulas or {}
    items = cf.get("formulas") or {}
    if isinstance(items, dict):
        _FORMULA_META = dict(items)
    _ACTIVE = {}
    for fid, local in FORMULAS.items():
        cloud = _FORMULA_META.get(fid) or {}
        merged = dict(local)
        if cloud.get("display"):
            merged["display"] = cloud["display"]
        if cloud.get("source"):
            merged["source"] = cloud["source"]
        if cloud.get("expr"):
            merged["expr"] = cloud["expr"]
        if cloud.get("params"):
            merged["params"] = cloud["params"]
        if cloud.get("used_by"):
            merged["used_by"] = cloud["used_by"]
        merged.setdefault("enabled", True)
        _ACTIVE[fid] = merged


def compute(fid: str, **kw: Any) -> Any:
    """按公式 id 执行计算。找不到 id 或 func 为 None 返回 None。"""
    f = _ACTIVE.get(fid) or FORMULAS.get(fid)
    if not f:
        return None
    func = f.get("func")
    if func is None:
        return None
    return func(**kw)


def FORMULA_TABLE() -> List[Dict[str, Any]]:
    """生成公式追溯表 (用于 xlsx Sheet2 / README / /api/formulas)。"""
    rows: List[Dict[str, Any]] = []
    for fid, f in sorted(_ACTIVE.items()):
        rows.append({
            "id": fid,
            "display": f.get("display", fid),
            "source": f.get("source", ""),
            "expr": f.get("expr", ""),
            "params": ", ".join(f.get("params", [])),
            "used_by": ", ".join(f.get("used_by", [])),
        })
    return rows


def get_formula(fid: str) -> Optional[Dict[str, Any]]:
    return _ACTIVE.get(fid) or FORMULAS.get(fid)


# 启动时先加载本地默认（无云端时直接可用）
apply_formulas(None)
