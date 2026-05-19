#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 深度分析模块 — 支持 GPT-4.1（Responses API）和 DeepSeek（Chat API）

环境变量（读自 .env 或 shell）：
  CI_TOKEN             GPT Bearer Token
  OPENAI_BASE_URL      GPT 代理地址（可选）
  OPENAI_MODEL         GPT 模型名（可选，默认 gpt-4.1）

  DEEPSEEK_API_KEY     DeepSeek API Key
  DEEPSEEK_BASE_URL    DeepSeek 地址（可选，默认 https://api.deepseek.com/v1）
  DEEPSEEK_MODEL       DeepSeek 模型名（可选，默认 deepseek-chat）

  LLM_MODEL            统一切换入口（可选）
    LLM_MODEL=gpt-4.1           → 使用 GPT（需要 CI_TOKEN）
    LLM_MODEL=deepseek-chat     → 使用 DeepSeek（需要 DEEPSEEK_API_KEY）
    LLM_MODEL=deepseek-reasoner → 使用 DeepSeek R1
"""

import datetime as dt
import json
import logging
import os
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Generator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 网络时间常量 ────────────────────────────────────────────────────────────
_NTP_TIMEOUT    = 2               # SNTP 超时（秒）
_TIME_CACHE_TTL = 300             # 5 分钟缓存
_TIME_TOL_MIN   = 2               # 与本地时间允许偏差（分钟）

# NTP 服务器（国内优先，SNTP 协议零依赖）
_NTP_SERVERS = [
    'ntp.aliyun.com',             # 阿里云
    'time.cloud.tencent.com',     # 腾讯云
    'ntp1.nim.ac.cn',             # 国家授时中心
    'cn.ntp.org.cn',              # 中国 NTP 联合池
    'time.windows.com',           # 微软（兜底）
]

# 运行时状态
_time_cache: Optional[Tuple[str, dt.datetime, float]] = None
_local_offset: Optional[float] = None  # 网络时间 - 本地时间（秒），断网兜底

# ── 自动加载 .env ─────────────────────────────────────────────────────────
_env_path = Path(__file__).resolve().parents[1] / ".env"
if not _env_path.exists():
    _env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── 配置 ──────────────────────────────────────────────────────────────────
# 统一模型配置：在 .env 中设置 LLM_MODEL 即可切换后端
_LLM_MODEL = os.environ.get("LLM_MODEL", "")

# GPT
_BASE_URL  = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
_API_KEY   = os.environ.get("CI_TOKEN") or os.environ.get("OPENAI_API_KEY", "")
_GPT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1")
_APP_NAME  = "a-stock-selector"

# DeepSeek
_DS_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
_DS_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
_DS_MODEL    = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# ── System Prompt ─────────────────────────────────────────────────────────

_PROMPT_HEADER = """\
你是一位专业的A股分析师，遵循严格的"证据优先、过程透明"分析原则。

## 核心原则
1. **证据绑定**：每个关键结论必须附有来源/数据依据，禁止无依据主观猜测。
2. **双逻辑分离**：所有股票判断必须拆分为 产业逻辑 + 交易逻辑 两层。
3. **三情景输出**：每只股票给出 强/中/弱 三个价格情景及对应操作动作。
4. **不确定性标注**：置信度低于"中"时，必须明确写出不确定因素与修正计划。
5. **时间敏感判断**：基于提供的网络北京时间进行交易决策判断，考虑当前是否在交易时段、收盘时间、次日开盘预期等。

## 时间维度分析要求
根据提供的网络北京时间（含星期几、小时、是否交易时间），在分析中必须考虑：
- **交易时段判断**：当前是否在A股交易时间（9:30-11:30, 13:00-15:00）
- **收盘前后策略**：临近收盘时的操作建议与盘中不同
- **隔夜风险**：当前时间距次日开盘的时间跨度，对持仓建议的影响
- **交易日历**：是否为周末/节假日，对资金安排和次日预期的影响
- **时间节点操作**：针对不同时间段（早盘/尾盘/盘前）给出差异化操作建议

## 分析报告必须包含以下结构（按顺序）

### 0) 数据摘要（Data Summary）
- 展示从调用方提供的结构化数据中读取到的关键指标。
- 时间戳，代码列表，市场情绪评分与状态。
- 各股票核心行情数据（价格、涨跌、量比、PE）、技术指标（MA/RSI/MACD/KDJ）、资金流向。

### 1) 市场情绪底色
- 综合情绪评分解读（恐慌/谨慎/中性/乐观/极度乐观）及各维度得分
- 当前市场所处阶段（缩量调整/量能温和/放量上攻/情绪过热）
- 一句话结论：普涨 / 分化 / 退潮 / 修复，以及对监控标的整体影响

### 2) 逐股深度分析（每只股票必须包含以下子节）
1. **公司业务定位**（主营、核心产品、产业链位置）— 基于提供的 company_profile 数据（行业、经营范围），结合自身知识补充
2. **当前市场叙事与阶段**（启动/强化/分歧/退潮）— 基于 recent_news 和资金流数据判断
3. **行业龙头与板块阶段** — 基于公司所属行业和自身知识分析
4. **技术面** — MA5/MA10/MA20/MA60 排列；当前价格相对均线位置；RSI/MACD/KDJ 状态；量比异动；关键压力位/支撑位/失效位
5. **资金面** — 主力净流入/流出趋势，超大单/大单/中单/小单结构，连续天数；量化短线/中长线评分（若有）
6. **舆情与事件面**（利多/利空/争议点）— 基于 recent_news 数据判断，结合自身知识补充
7. **双逻辑判断**
   - 产业逻辑：在 / 弱化 / 失效（附原因）
   - 交易逻辑：在 / 弱化 / 失效（附原因）
8. **明日三情景动作**
   - 强情景：触发条件 → 动作 → 目标位
   - 中情景：触发条件 → 动作 → 目标位
   - 弱情景：触发条件 → 止损位 → 动作
9. **证据卡片**（E1 行情数据 / E2 官方披露 / E3 主流媒体 / E4 板块验证）
10. **置信度**（高/中/低 + 原因）
"""

_PROMPT_TAIL_SINGLE = """\

### 3) 同业竞争格局深度对比
- 列出该股所在细分行业的 3~5 家核心竞争对手（A股上市公司），给出股票代码和简称
- 对比核心指标：市值、PE(TTM)、ROE、营收增速、利润增速、毛利率，用表格呈现（数据来自自身知识，标注"估算"）
- 该股在行业中的竞争地位（龙头/次龙头/追赶者/边缘），优势与劣势分别列出
- 行业当前所处周期阶段（导入期/成长期/成熟期/衰退期）及对个股的映射

### 4) 催化剂与风险日历
- **潜在催化剂**（按时间紧迫度排序）：
  - 近期即将发生的事件（财报披露、解禁、股东大会、行业政策等）
  - 中期可预见的事件（产品发布、产能投产、行业展会等）
  - 估算每个催化剂对股价的可能影响幅度（+3~5% / +5~10% / >+10%）
- **风险事件**（按杀伤力排序）：
  - 每个风险的发生概率估算（低/中/高）及潜在跌幅
  - 最大尾部风险是什么，如何提前识别信号

### 5) 机构持仓与聪明资金动向
- 基于提供的资金流数据（超大单/大单净流入趋势），判断机构资金近期行为：
  - 近 5 日 / 20 日机构资金是净流入还是净流出？趋势是加速还是衰减？
  - 散户 vs 机构的博弈态势（散户接盘 / 机构吸筹 / 双方对峙）
- 结合自身知识补充：最新一季基金/北向对该股的持仓变化趋势（如已知）

### 6) 历史相似形态回溯
- 基于当前的技术面特征（均线排列、MACD/RSI 位置、量能变化），回忆 A 股历史上（近 2 年）出现过类似形态的案例
- 列出 1~3 个历史案例，说明：当时的股票代码、形态描述、后续 5~20 日走势
- 从历史案例中提炼规律，对本次判断的参考意义

### 7) 综合操作建议
- **持仓状态判断**：假设投资者当前持有该股，处于什么状态（深套/浅套/微盈/获利）
- **三种持仓场景的对应操作**：
  - 场景A（已重仓）：建议动作、目标减仓/加仓价位
  - 场景B（轻仓观望）：建议动作、建仓价位和仓位比例
  - 场景C（空仓关注）：建议动作、关注触发条件
- **关键价位标注**：强支撑位 / 弱支撑位 / 多空分界线 / 弱压力位 / 强压力位
- **建议操作周期**：短线（1~5 日）/ 波段（1~4 周）/ 中线（1~3 月），附理由

### 8) 不确定性与自我修正
- 本轮最不确定的 2~3 个点
- 可能导致错判的条件
- 下一轮补证据与阈值修正计划

### 9) 一句话总结
> 用一句话（≤30字）概括本次分析的核心结论，格式：**「{代码} {名称}：{状态} — {建议动作}」**

## 注意事项
- 优先使用提供的结构化数据（company_profile、recent_news、fund_flow 细分、fundamental 扩展指标）写作相应章节；如果某部分数据缺失，则标注"数据缺失，建议补充"。
- 如果结构化数据不足（如技术指标缺失），在不确定性部分说明并建议补充。
- 输出语言：中文为主，技术指标名词可中英混写。
- 报告末尾必须有"一句话总结"章节。
- 禁止因篇幅原因精简或省略任何章节。
"""

_PROMPT_TAIL_MULTI = """\

### 3) 组合分层建议
- A组（产业+交易逻辑同向）/ B组（产业在/交易弱）/ C组（交易逻辑受损）
- 风险集中度说明；仓位调整优先级排序

### 4) 不确定性与自我修正
- 本轮最不确定的 2~3 个点
- 可能导致错判的条件
- 下一轮补证据与阈值修正计划

### 5) 一句话总结
> 用一句话（≤30字）概括本次分析的核心结论，格式：**「{代码} {名称}：{状态} — {建议动作}」**，多只股票依次列出。

## 注意事项
- 优先使用提供的结构化数据（company_profile、recent_news、fund_flow 细分、fundamental 扩展指标）写作相应章节；如果某部分数据缺失，则标注"数据缺失，建议补充"。
- 如果结构化数据不足（如技术指标缺失），在不确定性部分说明并建议补充。
- 输出语言：中文为主，技术指标名词可中英混写。
- 报告末尾必须有"一句话总结"章节。
- 禁止因篇幅原因精简或省略任何章节，每只股票必须包含全部10个子节。
"""


# ── 网络时间获取 ────────────────────────────────────────────────────────────

# NTP 纪元起点：1900-01-01 00:00:00
_NTP_EPOCH = dt.datetime(1900, 1, 1)


def _sntp_query(server: str, timeout: float = _NTP_TIMEOUT) -> Optional[dt.datetime]:
    """纯 socket SNTP 查询，零外部依赖，单次 UDP 收发 ~50ms"""
    # NTP 客户端请求：48 字节，首字节 0x1B (LI=0, VN=3, Mode=3=client)
    buf = struct.pack('!48B', 0x1B, *([0] * 47))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(buf, (server, 123))
        data, _ = sock.recvfrom(48)
    # 响应的第 40-47 字节是 Transmit Timestamp（NTP 秒 + 小数秒）
    secs = struct.unpack('!II', data[40:48])
    ntp_seconds = secs[0] + secs[1] / (2 ** 32)
    # NTP 时间戳从 1900 起算，转成 UTC+8
    utc8 = _NTP_EPOCH + dt.timedelta(seconds=ntp_seconds + 8 * 3600)
    # SNTP 往返延迟约几十毫秒，对股票分析精度足够
    return utc8


def _fetch_network_time() -> Tuple[Optional[str], Optional[dt.datetime]]:
    """SNTP 优先，失败则用历史偏移量修正本地时间"""
    global _time_cache, _local_offset

    now = time.time()
    if _time_cache and now - _time_cache[2] < _TIME_CACHE_TTL:
        return _time_cache[0], _time_cache[1]

    # ── SNTP（国内 NTP 服务器，UDP ~50ms）──
    for server in _NTP_SERVERS:
        try:
            network_dt = _sntp_query(server)
            if network_dt and network_dt.year > 2020:
                _local_offset = (network_dt - dt.datetime.now()).total_seconds()
                _time_cache = (f'SNTP/{server}', network_dt, now)
                return _time_cache[0], network_dt
        except Exception as e:
            logger.debug(f"SNTP failed ({server}): {e}")

    # ── 用历史偏移量修正本地时间 ──
    if _local_offset is not None:
        corrected = dt.datetime.now() + dt.timedelta(seconds=_local_offset)
        _time_cache = ('本地+偏移', corrected, now)
        return '本地+偏移', corrected

    return None, None


def _get_current_time_info() -> Tuple[str, dt.datetime, str]:
    """返回 (时间描述, datetime, 来源)"""
    source_name, network_dt = _fetch_network_time()
    local_dt = dt.datetime.now()

    if not network_dt:
        return (
            local_dt.strftime('%Y-%m-%d %H:%M:%S') + " (本地时间)",
            local_dt,
            "本地时间",
        )

    diff_min = int((network_dt - local_dt).total_seconds() / 60)
    if abs(diff_min) <= _TIME_TOL_MIN:
        suffix = "与本地时间一致"
    else:
        direction = "快" if diff_min > 0 else "慢"
        suffix = f"比本地时间{direction}{abs(diff_min / 60):.1f}小时"

    ts = network_dt.strftime('%Y-%m-%d %H:%M:%S')
    return f"{ts} (北京时间，{suffix})", network_dt, f"网络时间({source_name})"


# ── 数据构建 ──────────────────────────────────────────────────────────────

def _build_prompt(codes: List[str], sentiment: dict, stocks_data: list) -> str:
    """将缓存数据组装成 GPT 用户消息"""
    time_desc, current_dt, time_source = _get_current_time_info()
    ts = current_dt.strftime("%Y-%m-%d %H:%M:%S")
    # 交易时间判断 (9:30-11:30, 13:00-15:00)，排除午休和周末
    h, m = current_dt.hour, current_dt.minute
    is_weekend = current_dt.weekday() >= 5
    is_lunch_break = (h == 11 and m >= 30) or (h == 12) or (h == 13 and m == 0)
    is_trading = not is_weekend and not is_lunch_break and (
        (9 < h < 11) or (h == 9 and m >= 30) or (h == 11 and m < 30) or
        (13 < h < 15) or (h == 13 and m > 0) or (h == 15 and m == 0)
    )
    payload = {
        "timestamp": ts,
        "time_description": time_desc,
        "time_source": time_source,
        "current_weekday": current_dt.strftime("%A"),
        "current_hour": h,
        "current_minute": m,
        "is_trading_time": is_trading,
        "is_weekend": is_weekend,
        "codes": codes,
        "market_sentiment": {
            "score": sentiment.get("score"),
            "level": sentiment.get("level"),
            "emoji": sentiment.get("emoji"),
            "description": sentiment.get("description"),
            "stats": sentiment.get("stats", {}),
        },
        "stocks": [],
    }

    for s in stocks_data:
        code = s.get("code", "")
        entry = {
            "code": code,
            "name": s.get("name", ""),
            "price": s.get("price"),
            "change_pct": s.get("change_pct"),
            "change_amount": s.get("change_amount"),
            "open": s.get("open"),
            "high": s.get("high"),
            "low": s.get("low"),
            "volume": s.get("volume"),
            "turnover": s.get("turnover"),
            "volume_ratio": s.get("volume_ratio"),
            "pe_ttm": s.get("pe_ttm"),
            "market_cap": s.get("market_cap"),
            "update_time": s.get("update_time"),
        }
        # 技术指标
        ti = s.get("tech_indicators") or {}
        if ti:
            entry["tech_indicators"] = {
                "ma5": ti.get("ma5"),
                "ma10": ti.get("ma10"),
                "ma20": ti.get("ma20"),
                "ma60": ti.get("ma60"),
                "rsi": ti.get("rsi"),
                "macd": ti.get("macd"),
                "dif": ti.get("dif"),
                "dea": ti.get("dea"),
                "kdj_k": ti.get("kdj_k"),
                "kdj_d": ti.get("kdj_d"),
                "kdj_j": ti.get("kdj_j"),
                "boll_upper": ti.get("boll_upper"),
                "boll_mid": ti.get("boll_mid"),
                "boll_lower": ti.get("boll_lower"),
                "atr": ti.get("atr"),
                "adx": ti.get("adx"),
                "short_score": ti.get("short_score"),
                "long_score": ti.get("long_score"),
            }
        # 趋势详情
        trend = s.get("trend") or {}
        if trend:
            entry["trend"] = {
                "rating": trend.get("rating"),
                "reasons": trend.get("reasons", []),
            }
        # 动量
        momentum = s.get("momentum") or {}
        if momentum:
            entry["momentum"] = {
                "returns_5d": momentum.get("returns_5d"),
                "returns_20d": momentum.get("returns_20d"),
            }
        # 量能
        vol = s.get("volume") or {}
        if vol:
            entry["volume_analysis"] = {
                "obv_trend": vol.get("obv_trend"),
                "volume_ratio": vol.get("volume_ratio"),
            }
        # 波动率
        vix = s.get("volatility") or {}
        if vix:
            entry["volatility"] = {
                "value": vix.get("value"),
                "rating": vix.get("rating"),
            }
        # 乖离率
        bias = s.get("bias") or {}
        if bias:
            entry["bias"] = {
                "bias_20": bias.get("bias_20"),
                "bias_60": bias.get("bias_60"),
            }
        # 资金流
        ff = s.get("fund_flow") or {}
        if ff:
            entry["fund_flow"] = {
                "main_net_inflow": ff.get("main_net_inflow"),
                "main_net_inflow_pct": ff.get("main_net_inflow_pct"),
                "super_large_net": ff.get("super_large_net"),
                "large_net": ff.get("large_net"),
                "medium_net": ff.get("medium_net"),
                "small_net": ff.get("small_net"),
                "days_continuous": ff.get("days_continuous"),
                "signal": ff.get("signal"),
            }
        # 选股信号
        if s.get("buy_signals"):
            entry["buy_signals"] = s["buy_signals"]
        if s.get("sell_signals"):
            entry["sell_signals"] = s["sell_signals"]
        if s.get("stop_loss"):
            entry["trade_plan"] = {
                "stop_loss": s.get("stop_loss"),
                "take_profit": s.get("take_profit"),
                "stop_loss_pct": s.get("stop_loss_pct"),
                "take_profit_pct": s.get("take_profit_pct"),
                "risk_reward_ratio": s.get("risk_reward_ratio"),
            }
        # 公司画像（行业、经营范围）
        profile = s.get("company_profile") or {}
        if profile:
            entry["company_profile"] = {
                "industry": profile.get("industry"),
                "business_scope": profile.get("business_scope"),
                "pb_ratio": profile.get("pb_ratio"),
                "total_market_cap": profile.get("total_market_cap"),
            }
        # 扩展基本面（毛利率、净利率、负债率等）
        fund_ext = s.get("fundamental") or {}
        if fund_ext:
            entry["fundamental"] = {
                "roe": fund_ext.get("roe"),
                "pe": fund_ext.get("pe"),
                "pb": fund_ext.get("pb"),
                "gross_margin": fund_ext.get("gross_margin"),
                "net_margin": fund_ext.get("net_margin"),
                "debt_to_asset": fund_ext.get("debt_to_asset"),
                "profit_growth": fund_ext.get("profit_growth"),
                "dividend_yield": fund_ext.get("dividend_yield"),
            }
        # 最近新闻
        news = s.get("recent_news") or []
        if news:
            entry["recent_news"] = news
        payload["stocks"].append(entry)

    return (
        "## 分析请求\n"
        f"- 当前时间：{time_desc}\n"
        f"- 时间来源：{time_source}\n"
        f"- 分析标的：{', '.join(codes)}\n\n"
        "## 结构化市场数据（JSON）\n"
        "```json\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n```\n\n"
        "请按照报告结构对每只标的输出完整、详细的分析。"
        "禁止因篇幅原因精简或省略任何章节，每只股票必须包含全部10个子节。"
    )


# ── 后端选择 ─────────────────────────────────────────────────────────────

def _resolve_model_and_backend() -> tuple:
    """
    根据 .env 中的 LLM_MODEL 确定 (backend, model_name)。
    优先级：LLM_MODEL > DEEPSEEK_API_KEY 存在 > 默认 GPT
    """
    m = _LLM_MODEL.strip()
    if m.lower().startswith("deepseek"):
        # LLM_MODEL 决定后端，DEEPSEEK_MODEL 决定实际模型名
        return "deepseek", _DS_MODEL or m
    if m:  # 明确指定了非 deepseek 模型（如 gpt-4.1）
        return "gpt", m
    # 未设置 LLM_MODEL：有 DeepSeek key 则用 DeepSeek，否则用 GPT
    if _DS_API_KEY:
        return "deepseek", _DS_MODEL
    return "gpt", _GPT_MODEL


def _get_gpt_client():
    if not _API_KEY:
        raise RuntimeError("未设置 CI_TOKEN 或 OPENAI_API_KEY，无法调用 GPT。")
    from openai import OpenAI
    return OpenAI(
        base_url=_BASE_URL,
        api_key=_API_KEY,
        default_headers={"x-cisco-app": _APP_NAME},
    )


def _get_deepseek_client():
    if not _DS_API_KEY:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY，无法调用 DeepSeek。")
    from openai import OpenAI
    return OpenAI(base_url=_DS_BASE_URL, api_key=_DS_API_KEY)


def _build_system_prompt(codes: List[str]) -> str:
    """根据标的数量选择单股深度或组合分析的 prompt"""
    tail = _PROMPT_TAIL_SINGLE if len(codes) == 1 else _PROMPT_TAIL_MULTI
    return _PROMPT_HEADER + tail


def run_analysis(
    codes: List[str],
    sentiment: dict,
    stocks_data: list,
    stream: bool = False,
):
    """
    执行 LLM 分析，后端和模型由 .env 中 LLM_MODEL 决定。
    - stream=False：返回完整报告字符串
    - stream=True：返回 Generator[str, None, None]，逐 token yield
    """
    backend, actual_model = _resolve_model_and_backend()
    system_prompt = _build_system_prompt(codes)
    print(f"[LLM] 使用后端={backend} 模型={actual_model} 标的数={len(codes)}", flush=True)
    user_msg = _build_prompt(codes, sentiment, stocks_data)

    if backend == "deepseek":
        return _run_deepseek(actual_model, user_msg, stream, system_prompt)
    else:
        return _run_gpt(actual_model, user_msg, stream, system_prompt)


# ── GPT (Responses API) ──────────────────────────────────────────────────

def _run_gpt(model: str, user_msg: str, stream: bool, system_prompt: str):
    client = _get_gpt_client()
    input_messages = _build_responses_messages(user_msg, system_prompt)
    chat_messages = _build_chat_messages(user_msg, system_prompt)
    if stream:
        return _gpt_stream(client, model, input_messages, chat_messages)
    try:
        resp = client.responses.create(model=model, input=input_messages)
        return resp.output[0].content[0].text
    except Exception:
        resp = client.chat.completions.create(model=model, messages=chat_messages, stream=False)
        return resp.choices[0].message.content


def _build_responses_messages(user_msg: str, system_prompt: str) -> list:
    return [
        {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
        {"role": "user",   "content": [{"type": "input_text", "text": user_msg}]},
    ]


def _build_chat_messages(user_msg: str, system_prompt: str) -> list:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]


def _gpt_stream(client, model: str, input_messages: list, chat_messages: list) -> Generator[str, None, None]:
    try:
        with client.responses.stream(model=model, input=input_messages) as s:
            for event in s:
                if hasattr(event, "delta") and hasattr(event.delta, "text"):
                    yield event.delta.text
                elif getattr(event, "type", None) == "response.output_text.delta":
                    yield getattr(event, "delta", "")
        return
    except Exception:
        pass

    # 响应流接口不可用时，回退到 chat.completions 流式输出（参照 02 项目）
    resp = client.chat.completions.create(model=model, messages=chat_messages, stream=True)
    for chunk in resp:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content


# ── DeepSeek (Chat Completions API) ──────────────────────────────────────

def _run_deepseek(model: str, user_msg: str, stream: bool, system_prompt: str):
    client = _get_deepseek_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_msg},
    ]
    print(f"[DeepSeek] model={model}, stream={stream}, msg_len={len(user_msg)}", flush=True)
    if stream:
        return _deepseek_stream(client, model, messages)
    resp = client.chat.completions.create(model=model, messages=messages, stream=False, max_tokens=16384)
    return resp.choices[0].message.content


def _deepseek_stream(client, model: str, messages: list) -> Generator[str, None, None]:
    resp = client.chat.completions.create(model=model, messages=messages, stream=True, max_tokens=16384)
    has_content = False
    for chunk in resp:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        # 兼容 reasoning_content（DeepSeek-R1 等）和普通 content
        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
            if not has_content:
                yield "【思考过程】\n"
                has_content = True
            yield delta.reasoning_content
        if delta and delta.content:
            if has_content:
                yield "\n\n【分析报告】\n"
                has_content = False
            yield delta.content


# ── 连通性测试 ────────────────────────────────────────────────────────────

def test_connection() -> bool:
    """
    发送一条极简请求，验证 API Key 和网络是否正常。
    成功返回 True，失败打印原因并返回 False。
    """
    backend, model = _resolve_model_and_backend()
    print(f"Backend  : {backend}")
    print(f"Model    : {model}")
    if backend == "gpt":
        print(f"BASE_URL : {_BASE_URL}")
        print(f"API_KEY  : {'已设置 (' + _API_KEY[:8] + '...)' if _API_KEY else '❌ 未设置'}")
        if not _API_KEY:
            print("❌ 未设置 CI_TOKEN 或 OPENAI_API_KEY，请先配置环境变量或 .env 文件。")
            return False
        try:
            client = _get_gpt_client()
            resp = client.responses.create(
                model=model,
                input=[{"role": "user", "content": [{"type": "input_text", "text": "reply with: ok"}]}],
            )
            reply = resp.output[0].content[0].text.strip()
            print(f"✅ GPT 连通正常，模型回复：{reply!r}")
            return True
        except Exception as e:
            print(f"❌ 连接失败：{e}")
            return False
    else:  # deepseek
        print(f"DS_URL   : {_DS_BASE_URL}")
        print(f"DS_KEY   : {'已设置 (' + _DS_API_KEY[:8] + '...)' if _DS_API_KEY else '❌ 未设置'}")
        if not _DS_API_KEY:
            print("❌ 未设置 DEEPSEEK_API_KEY。")
            return False
        try:
            client = _get_deepseek_client()
            resp = client.chat.completions.create(
                model=model, stream=False,
                messages=[{"role": "user", "content": "reply with: ok"}]
            )
            reply = resp.choices[0].message.content.strip()
            print(f"✅ DeepSeek 连通正常，模型回复：{reply!r}")
            return True
        except Exception as e:
            print(f"❌ 连接失败：{e}")
            return False


if __name__ == "__main__":
    test_connection()
