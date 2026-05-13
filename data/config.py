#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
选股引擎配置
"""

# ============ Tushare配置 ============
TUSHARE_TOKEN = ""  # 可选，不填则使用免费数据源（新浪+akshare）

# ============ 扫描指数 ============
# 选股引擎从这些指数的成分股中扫描，合并去重
SCAN_INDICES = ["000300", "000905", "000852", "000016"]  # 沪深300 + 中证500 + 中证1000 + 上证50

# ============ 选股阈值 ============
SHORT_TERM_SCORE_THRESHOLD = 50   # 短线推荐最低分
SHORT_TERM_SIGNAL_THRESHOLD = 2   # 短线最少买入信号数
LONG_TERM_SCORE_THRESHOLD = 60    # 中长线推荐最低分
ENHANCED_SCORE_THRESHOLD = 55     # 增强版推荐最低分

# ============ Web服务配置 ============
WEB_HOST = '0.0.0.0'
WEB_PORT = 5001   # 独立端口，与 05-stock-watchlist 的 5000 不冲突
