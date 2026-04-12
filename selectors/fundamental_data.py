#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基本面数据获取模块 (FundamentalData) — 通过 akshare 免费接口"""

import pandas as pd
from typing import Dict


class FundamentalData:

    def __init__(self, cache=None):
        """
        :param cache: StockCache 实例（可选）。传入后基本面数据会被缓存 24 小时，
                     避免每次分析都重复调用慢速 THS/EM 接口。
        """
        self._cache = cache

    def get_stock_fundamental(self, code: str) -> Dict:
        """
        获取股票基本面数据
        返回: roe, profit_growth, dividend_yield, revenue_growth, pe
        失败时返回默认零值，保证不影响选股流程
        """
        result = {
            'roe': 0.0,
            'profit_growth': 0.0,
            'dividend_yield': 0.0,
            'revenue_growth': 0.0,
            'pe': 0.0,
        }

        # ── 缓存命中直接返回 ─────────────────────────────────────────
        if self._cache is not None:
            cached = self._cache.get_fundamental(code)
            if cached is not None:
                return cached

        try:
            import akshare as ak

            # ── 财务指标（ROE、利润增长等） ──────────────────────────────
            try:
                df = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    # 净资产收益率
                    for col in df.columns:
                        if '净资产收益率' in str(col) or 'ROE' in str(col).upper():
                            val = _to_float(row[col])
                            if val is not None:
                                result['roe'] = val
                            break
                    # 净利润增长率
                    for col in df.columns:
                        if '净利润增长' in str(col) or '净利润同比' in str(col):
                            val = _to_float(row[col])
                            if val is not None:
                                result['profit_growth'] = val
                            break
                    # 营收增长率
                    for col in df.columns:
                        if '营业收入增长' in str(col) or '营收同比' in str(col):
                            val = _to_float(row[col])
                            if val is not None:
                                result['revenue_growth'] = val
                            break
            except Exception:
                pass

            # ── 市盈率 & 股息率 ─────────────────────────────────────────
            try:
                spot = ak.stock_individual_info_em(symbol=code)
                if spot is not None and not spot.empty:
                    spot_dict = dict(zip(spot.iloc[:, 0], spot.iloc[:, 1]))
                    pe_val = spot_dict.get('市盈率(动)', spot_dict.get('市盈率', 0))
                    result['pe'] = _to_float(pe_val) or 0.0
                    div_val = spot_dict.get('股息率', 0)
                    result['dividend_yield'] = _to_float(div_val) or 0.0
            except Exception:
                pass

        except Exception:
            pass

        # ── 写入缓存 ─────────────────────────────────────────────────
        if self._cache is not None:
            try:
                self._cache.save_fundamental(code, result)
            except Exception:
                pass

        return result

    def close(self):
        """占位方法，保持接口一致"""
        pass


def _to_float(val) -> float | None:
    try:
        s = str(val).replace('%', '').replace(',', '').strip()
        return float(s)
    except Exception:
        return None
