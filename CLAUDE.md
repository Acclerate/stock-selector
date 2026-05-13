# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A股量化选股系统 — Flask Web 应用，扫描沪深300成分股（过滤创业板/科创板，约250只），提供短线/中长线/增强版三种选股策略，并集成 LLM（GPT-4.1/DeepSeek）深度研报生成。

## Commands

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 Flask 服务 (端口 5001)
python app/selector_app.py
# 访问 http://localhost:5001  账号: admin / admin123

# 单独运行选股引擎（CLI 模式，无需 Web）
python -m selectors.short_term_selector
python -m selectors.long_term_selector
python -m selectors.enhanced_long_term_selector

# 测试数据源连通性
python data/hybrid_data_source.py
python data/smart_data_source.py

# 测试 LLM 连接
python app/gpt_analyst.py

# 测试缓存层
python data/stock_cache_db.py
```

无 linter/test 配置，无 CI。

## Architecture

### 请求流

```
浏览器 → Flask (selector_app.py) → Selector 实例 → SmartDataSource → HybridDataSource → 多数据源
                                ↘ StockCache (SQLite)  ← 缓存 miss 时自动补填
                                ↘ gpt_analyst.py → OpenAI/DeepSeek API
```

### 三层架构

**Web 层** (`app/`): Flask 路由、登录鉴权 (Flask-Login)、API 端点。`selector_app.py` 是唯一入口，通过路径注入使 `data/`、`selectors/`、`utils/` 可直接 import。

**选股层** (`selectors/`): 三个独立 Selector 类，各自实现 `select_top_stocks(top_n)` → `analyze_single_stock(code)` → 返回评分结果。共享 `get_index_stocks()` 方法（从 akshare 获取沪深300成分股并过滤创业板/科创板）。

**数据层** (`data/`): `SmartDataSource`（兼容层）→ `HybridDataSource`（多源融合，含当天内存缓存）→ `StockCache`（SQLite 持久缓存）。缓存 miss 时自动实时拉取。

### 数据源优先级

| 数据类型 | 优先级 |
|---------|--------|
| 实时行情 | 东方财富 HTTPS → 腾讯 fqkline → 新浪 → akshare |
| 历史K线（选股用） | 腾讯 fqkline → 东方财富 → akshare |
| 基本面（ROE/PE等） | 同花顺 → 东方财富 → SQLite 缓存 24h |

### 三种选股策略

| 策略 | type 参数 | 满分 | 核心维度 | 推荐阈值 |
|------|----------|------|---------|---------|
| 短线 | `short` | 100 | RSI(20)+KDJ(20)+MACD(15)+布林(15)+量价(15)+资金(15) | ≥60 & 信号≥2 |
| 中长线 | `long` | 100 | 趋势(30)+动量(15)+量能(15)+ADX(10)+波动(10)+乖离(10)+资金(10) | ≥70 |
| 增强版 | `enhanced` | 130→归一化100 | 中长线技术面+基本面(ROE/利润/股息30)+估值(PEG15)+DMI(15) | ≥65 |

### LLM 分析

`gpt_analyst.py` 通过 `.env` 中的 `LLM_MODEL` 自动选择后端：`deepseek-reasoner` / `deepseek-chat` 走 DeepSeek Chat API；`gpt-4.1` 走 OpenAI Responses API（失败回退 Chat Completions）。支持流式输出。

### 缓存策略

- **SQLite** (`data/stock_cache.db`): stocks / fund_flow / lhb / tech_indicators / fundamental / history_kline 六张表
- 历史K线：当天内存缓存 + SQLite 持久化（跨进程复用），次日自动失效
- 基本面数据：SQLite 缓存 24 小时
- `StockCache.get_stock()` 缓存 miss 时自动调东方财富 API 补填

## Key Conventions

- `selector_app.py` 启动时通过 `sys.path.insert(0, ...)` 注入 `data/`、`selectors/`、`utils/` 目录，所以模块内使用裸 import（如 `from stock_cache_db import StockCache`）即可
- 所有 Selector 返回结果中的 `details` 使用 `_convert_to_json_safe()` 处理 numpy/pandas 类型和 NaN
- `.env` 文件存放 LLM API Key 和模型配置，`gpt_analyst.py` 启动时自动加载
- 选股扫描范围由 `data/config.py` 中 `SCAN_INDICES` 控制，默认 `["000300", "000905", "000852", "000016"]`（沪深300+中证500+中证1000+上证50）
