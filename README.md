# A股量化选股神器

> 基于 Flask + 东方财富/腾讯 HTTPS 接口（主）+ 新浪/akshare（备），扫描沪深300成分股（过滤创业板/科创板，约 250 只），提供实时行情监控、7 维市场情绪评分、多策略智能选股与 Web 可视化看板。

---

## 功能概览

| 功能 | 说明 |
| --- | --- |
| 7 维市场情绪评分 | 综合涨跌比、均涨幅、涨停率、强势股比例、成交活跃度等，0-100 分量化大盘 |
| 短线选股（1-5 日） | 基于**历史K线**（近30日）计算 RSI / MACD / KDJ / 布林带 / 量价共振，多指标共振评分 |
| 中长线选股（20-180 日） | 基于**历史K线**（近120日）计算 MA 趋势 / 动量 / OBV / ADX / ATR / 偏离 等 7 维评分 |
| **LLM 深度分析（GPT/DeepSeek）** | 一键调用 GPT-4.1 或 DeepSeek（V3/R1），结合行情/技术指标/资金流/情绪评分生成专业研报 |
| 龙虎榜 & 舆情监控 | 每日龙虎榜抓取 + 政策新闻监控 |
| Web 看板 | Flask + ECharts 可视化，角色权限管理，5 秒轮询刷新 |


---

## 目录结构

```
scripts/
  app/
    selector_app.py          # Flask 入口 (端口 5001)
    gpt_analyst.py           # GPT-4.1 深度分析模块
    templates/
      index.html             # 主界面
      login.html             # 登录页
  data/
    config.py                # 选股阈值 & 扫描指数配置
    smart_data_source.py     # 数据源适配器
    hybrid_data_source.py    # 多来源行情融合
    stock_cache_db.py        # SQLite 缓存层（stocks/fund_flow/lhb/tech_indicators/history_kline/fundamental）
    lhb_fetcher.py           # 龙虎榜数据
    eastmoney_api.py         # 东方财富 API
  selectors/
    short_term_selector.py   # 短线引擎
    long_term_selector.py    # 中长线引擎
    enhanced_long_term_selector.py  # 增强版（含基本面）
  utils/
    is_trading_time.py       # 交易时段判断
```

## 扫描范围说明

选股扫描沪深300成分股（共300只），并**自动过滤创业板（3开头）和科创板（688开头）**，
实际分析约 250 只主板/中小板蓝筹股（随指数季度调仓略有变化）。

---

## 数据源优先级

| 场景 | 优先顺序 |
|------|----------|
| 实时行情（单只/批量，仅用于展示现价） | 东方财富 HTTPS → 腾讯 HTTPS → 新浪 → akshare |
| 历史K线（**用于选股指标计算**，非实时） | 腾讯 fqkline HTTPS → 东方财富 → akshare |
| 成分股列表 | 东方财富 `index_stock_cons` → 中证官网（备用） |
| 基础信息缓存 miss | 东方财富实时接口自动补填 SQLite 缓存 |
| 基本面数据（ROE/PE/股息率等） | 同花顺 THS 财务接口 → 东方财富个股信息 → **SQLite 缓存 24 小时** |

> **注意**：选股的技术指标（RSI / MACD / KDJ / 布林带等）均基于**历史收盘K线**计算，并非实时 tick 数据。
> 同一天内多次运行选股时，历史K线数据会**自动复用当天内存缓存**，无需重复抓取，显著提速。

> **基本面缓存**：`FundamentalData` 首次查询每只股票时调用同花顺/东方财富接口（约 20s/只），结果写入 SQLite `fundamental` 表，之后 24 小时内复用缓存（≈0s），大幅加速批量选股。

> 注：新浪/akshare 仅作兜底，服务器需保留系统代理以访问外网。

---


```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python app/selector_app.py
# 访问 http://localhost:5001  帐号: admin / admin123
```

### LLM 环境变量示例

```bash
# 二选一：gpt-4.1 / deepseek-chat / deepseek-reasoner
LLM_MODEL=deepseek-reasoner

# DeepSeek（可选）
DEEPSEEK_API_KEY=sk-xxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# GPT 官方（可选）
# OPENAI_API_KEY=sk-xxxx
# OPENAI_BASE_URL=https://api.openai.com/v1
# OPENAI_MODEL=gpt-4.1

# 企业代理（可选）
# CI_TOKEN=xxxx
# OPENAI_BASE_URL=https://llm-proxy.us-east-2.int.infra.intelligence.webex.com/openai/v1
```

> `app/gpt_analyst.py` 会按 `LLM_MODEL` 自动选择后端。  
> GPT 路径默认优先 Responses API，失败时会自动回退到 Chat Completions API。

---

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/selector/run` | 运行选股 `{"type":"long\|short\|enhanced","top_n":5}` |
| POST | `/api/selector/report` | 生成文字报告 |
| POST | `/api/selector/gpt-analyze` | LLM 深度研报（GPT/DeepSeek，含 `"stream":true` 流式模式） |

---

## 选股策略说明

| 策略 | type 参数 | 推荐阈值 | 维度 |
|------|-----------|----------|------|
| 短线 | `short` | ≥60分 & 信号≥2 | RSI/KDJ/MACD/布林/量价/资金 |
| 中长线 | `long` | ≥70分 | 趋势/动量/OBV/ADX/ATR/偏离/资金 |
| 增强版 | `enhanced` | ≥65分 | 中长线+基本面+PEG+DMI（基本面数据 SQLite 缓存 24h）|

---

## 代码设计

```
┌─────────────────────────────────────────────────────────────────┐
│                        浏览器 / API 客户端                       │
└──────────┬──────────────────────────────────┬───────────────────┘
           │ POST /api/selector/run           │ POST /api/selector/gpt-analyze
           │ POST /api/selector/report        │   (stream: true/false)
           ▼                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                     selector_app.py  (Flask)                     │
│  登录鉴权  →  解析 type / top_n  →  实例化对应 Selector          │
└──────┬────────────────┬──────────────────────┬───────────────────┘
       │ type=short     │ type=long             │ type=enhanced
       ▼                ▼                       ▼
┌────────────┐  ┌────────────────┐  ┌─────────────────────────┐
│ ShortTerm  │  │   LongTerm     │  │  EnhancedLongTerm        │
│ Selector   │  │   Selector     │  │  Selector                │
└─────┬──────┘  └──────┬─────────┘  └───────────┬─────────────┘
      │                │                         │
      └────────────────┴────────────┬────────────┘
                                    │ get_index_stocks()
                                    ▼
                        ┌───────────────────────┐
                        │  akshare 沪深300成分股  │
                        │  过滤创业板/科创板 ≈250只 │
                        └───────────┬───────────┘
                                    │ 逐股 analyze_single_stock()
                                    ▼
              ┌─────────────────────────────────────────────┐
              │               数据获取层                      │
              │                                             │
              │  SmartDataSource                            │
              │    ├─ is_trading_time()  判断交易时段         │
              │    └─ HybridDataSource                      │
              │          ├─ 东方财富 HTTPS (实时主源)        │
              │          ├─ 腾讯 fqkline HTTPS (历史K线)      │
              │          ├─ 新浪财经  (备用)                  │
              │          └─ akshare    (兜底)                  │
              │                                             │
              │  StockCache  (SQLite)                       │
              │    ├─ stock_info      名称/价格/涨跌幅        │
              │    ├─ fund_flow       主力净流入              │
              │    ├─ history_kline   历史K线 (当天复用)       │
              │    └─ fundamental     基本面 (24h TTL)        │
              └─────────────────────┬───────────────────────┘
                                    │
                                    ▼
              ┌─────────────────────────────────────────────┐
              │               指标计算层                      │
              │                                             │
              │  ShortTermIndicators                        │
              │    RSI / KDJ / MACD / 布林带 / 量价 / 资金   │
              │                                             │
              │  AdvancedIndicators  (中长线)                │
              │    趋势 / 动量 / OBV / ADX / ATR / 偏离       │
              │                                             │
              │  AdvancedLongTermIndicators  (增强版技术面)   │
              │    DMI / PEG / 综合信号优化                   │
              │                                             │
              │  FundamentalData  (增强版基本面)              │
              │    ROE / 利润增长 / 股息率 / PEG              │
              └─────────────────────┬───────────────────────┘
                                    │ 评分 → 排序 → TOP N
                                    ▼
                        ┌───────────────────────┐
                        │   选股结果 JSON        │
                        └───────────┬───────────┘
                                    │
               ┌────────────────────┴─────────────────────┐
               │ 直接返回浏览器                              │ POST gpt-analyze
               ▼                                           ▼
   ┌───────────────────────┐              ┌───────────────────────────────┐
   │   JSON 响应返回浏览器   │              │         gpt_analyst.py        │
   └───────────────────────┘              │  输入: 选股结果 + 技术指标      │
                                          │        + 资金流 + 市场情绪      │
                                          │  调用: GPT-4.1 / DeepSeek       │
                                          │  输出: 专业研报                 │
                                          │    ├─ 产业逻辑 + 交易逻辑       │
                                          │    ├─ 强/中/弱 三情景价格       │
                                          │    ├─ 买入/止损/止盈建议        │
                                          │    └─ stream=true 流式输出      │
                                          └───────────────────────────────┘
```
