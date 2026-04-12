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
import os
import sys
from pathlib import Path
from typing import Generator, List, Optional

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
_SYSTEM_PROMPT = """\
你是一位专业的A股分析师，遵循严格的"证据优先、过程透明"分析原则。

## 核心原则
1. **证据绑定**：每个关键结论必须附有来源/数据依据，禁止无依据主观猜测。
2. **双逻辑分离**：所有股票判断必须拆分为 产业逻辑 + 交易逻辑 两层。
3. **三情景输出**：每只股票给出 强/中/弱 三个价格情景及对应操作动作。
4. **不确定性标注**：置信度低于"中"时，必须明确写出不确定因素与修正计划。

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
1. **公司业务定位**（主营、核心产品、产业链位置）— 需联网核实
2. **当前市场叙事与阶段**（启动/强化/分歧/退潮）— 需联网核实
3. **行业龙头与板块阶段** — 需联网核实
4. **技术面** — MA5/MA10/MA20/MA60 排列；当前价格相对均线位置；RSI/MACD/KDJ 状态；量比异动；关键压力位/支撑位/失效位
5. **资金面** — 主力净流入/流出趋势，超大单/大单结构，连续天数；量化短线/中长线评分（若有）
6. **舆情与事件面**（利多/利空/争议点）— 需联网核实
7. **双逻辑判断**
   - 产业逻辑：在 / 弱化 / 失效（附原因）
   - 交易逻辑：在 / 弱化 / 失效（附原因）
8. **明日三情景动作**
   - 强情景：触发条件 → 动作 → 目标位
   - 中情景：触发条件 → 动作 → 目标位
   - 弱情景：触发条件 → 止损位 → 动作
9. **证据卡片**（E1 行情数据 / E2 官方披露 / E3 主流媒体 / E4 板块验证）
10. **置信度**（高/中/低 + 原因）

### 3) 组合分层建议（多只股票时）
- A组（产业+交易逻辑同向）/ B组（产业在/交易弱）/ C组（交易逻辑受损）
- 风险集中度说明；仓位调整优先级排序

### 4) 不确定性与自我修正
- 本轮最不确定的 2~3 个点
- 可能导致错判的条件
- 下一轮补证据与阈值修正计划

### 5) 一句话总结
> 用一句话（≤30字）概括本次分析的核心结论，格式：**「{代码} {名称}：{状态} — {建议动作}」**，多只股票依次列出。

## 注意事项
- 仅基于调用方提供的结构化数据写作"数据摘要"；基本面/舆情部分须明确标注"需联网核实"。
- 如果结构化数据不足（如技术指标缺失），在不确定性部分说明并建议补充。
- 输出语言：中文为主，技术指标名词可中英混写。
- 报告末尾必须有"一句话总结"章节。
"""


# ── 数据构建 ──────────────────────────────────────────────────────────────

def _build_prompt(codes: List[str], sentiment: dict, stocks_data: list) -> str:
    """将缓存数据组装成 GPT 用户消息"""
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "timestamp": ts,
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
                "kdj_k": ti.get("kdj_k"),
                "kdj_d": ti.get("kdj_d"),
                "kdj_j": ti.get("kdj_j"),
                "boll_upper": ti.get("boll_upper"),
                "boll_mid": ti.get("boll_mid"),
                "boll_lower": ti.get("boll_lower"),
                "atr": ti.get("atr"),
                "short_score": ti.get("short_score"),
                "long_score": ti.get("long_score"),
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
            }
        payload["stocks"].append(entry)

    return (
        "## 分析请求\n"
        f"- 时间戳：{ts}\n"
        f"- 分析标的：{', '.join(codes)}\n\n"
        "## 结构化市场数据（JSON）\n"
        "```json\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n```\n\n"
        "请按照报告结构输出完整分析。"
    )


# ── 后端选择 ─────────────────────────────────────────────────────────────

def _resolve_model_and_backend() -> tuple:
    """
    根据 .env 中的 LLM_MODEL 确定 (backend, model_name)。
    优先级：LLM_MODEL > DEEPSEEK_API_KEY 存在 > 默认 GPT
    """
    m = _LLM_MODEL.strip()
    if m.lower().startswith("deepseek"):
        return "deepseek", m
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
    print(f"[LLM] 使用后端={backend} 模型={actual_model}", flush=True)
    user_msg = _build_prompt(codes, sentiment, stocks_data)

    if backend == "deepseek":
        return _run_deepseek(actual_model, user_msg, stream)
    else:
        return _run_gpt(actual_model, user_msg, stream)


# ── GPT (Responses API) ──────────────────────────────────────────────────

def _run_gpt(model: str, user_msg: str, stream: bool):
    client = _get_gpt_client()
    input_messages = _build_responses_messages(user_msg)
    chat_messages = _build_chat_messages(user_msg)
    if stream:
        return _gpt_stream(client, model, input_messages, chat_messages)
    try:
        # 优先使用 Responses API（与当前项目默认保持一致）
        resp = client.responses.create(model=model, input=input_messages)
        return resp.output[0].content[0].text
    except Exception:
        # 兼容 02 项目的 Chat Completions 路径
        resp = client.chat.completions.create(model=model, messages=chat_messages, stream=False)
        return resp.choices[0].message.content


def _build_responses_messages(user_msg: str) -> list:
    return [
        {"role": "system", "content": [{"type": "input_text", "text": _SYSTEM_PROMPT}]},
        {"role": "user",   "content": [{"type": "input_text", "text": user_msg}]},
    ]


def _build_chat_messages(user_msg: str) -> list:
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
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

def _run_deepseek(model: str, user_msg: str, stream: bool):
    client = _get_deepseek_client()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": user_msg},
    ]
    if stream:
        return _deepseek_stream(client, model, messages)
    resp = client.chat.completions.create(model=model, messages=messages, stream=False)
    return resp.choices[0].message.content


def _deepseek_stream(client, model: str, messages: list) -> Generator[str, None, None]:
    resp = client.chat.completions.create(model=model, messages=messages, stream=True)
    for chunk in resp:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
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
