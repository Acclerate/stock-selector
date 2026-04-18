#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基本面数据获取模块 (FundamentalData) — 通过 akshare 免费接口"""

import pandas as pd
from typing import Dict, List, Optional


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
        返回: roe, profit_growth, dividend_yield, revenue_growth, pe,
              gross_margin, net_margin, debt_to_asset, pb
        失败时返回默认零值，保证不影响选股流程
        """
        result = {
            'roe': 0.0,
            'profit_growth': 0.0,
            'dividend_yield': 0.0,
            'revenue_growth': 0.0,
            'pe': 0.0,
            'gross_margin': 0.0,
            'net_margin': 0.0,
            'debt_to_asset': 0.0,
            'pb': 0.0,
        }

        # ── 缓存命中直接返回 ─────────────────────────────────────────
        if self._cache is not None:
            cached = self._cache.get_fundamental(code)
            if cached is not None:
                return cached

        try:
            import akshare as ak

            # ── 财务指标（ROE、利润增长、毛利率等） ─────────────────────
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
                    # 销售毛利率
                    for col in df.columns:
                        if '销售毛利率' in str(col) or '毛利率' in str(col):
                            val = _to_float(row[col])
                            if val is not None:
                                result['gross_margin'] = val
                            break
                    # 销售净利率
                    for col in df.columns:
                        if '销售净利率' in str(col) or '净利率' in str(col):
                            val = _to_float(row[col])
                            if val is not None:
                                result['net_margin'] = val
                            break
                    # 资产负债率
                    for col in df.columns:
                        if '资产负债率' in str(col):
                            val = _to_float(row[col])
                            if val is not None:
                                result['debt_to_asset'] = val
                            break
            except Exception:
                pass

            # ── 市盈率 & 股息率 & 市净率 ────────────────────────────────
            try:
                spot = ak.stock_individual_info_em(symbol=code)
                if spot is not None and not spot.empty:
                    spot_dict = dict(zip(spot.iloc[:, 0], spot.iloc[:, 1]))
                    pe_val = spot_dict.get('市盈率(动)', spot_dict.get('市盈率', 0))
                    result['pe'] = _to_float(pe_val) or 0.0
                    div_val = spot_dict.get('股息率', 0)
                    result['dividend_yield'] = _to_float(div_val) or 0.0
                    pb_val = spot_dict.get('市净率', 0)
                    result['pb'] = _to_float(pb_val) or 0.0
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

    def get_company_profile(self, code: str) -> Dict:
        """
        获取公司画像：行业、经营范围、市值等
        缓存 24 小时
        """
        if self._cache is not None:
            cached = self._cache.get_company_profile(code)
            if cached is not None:
                return cached

        result = {
            'industry': '',
            'business_scope': '',
            'pb': 0.0,
            'total_market_cap': 0.0,
            'float_market_cap': 0.0,
        }

        try:
            import akshare as ak
            spot = ak.stock_individual_info_em(symbol=code)
            if spot is not None and not spot.empty:
                spot_dict = dict(zip(spot.iloc[:, 0], spot.iloc[:, 1]))
                result['industry'] = str(spot_dict.get('行业', ''))
                result['business_scope'] = str(spot_dict.get('经营范围', ''))
                pb_val = spot_dict.get('市净率', 0)
                result['pb'] = _to_float(pb_val) or 0.0
                cap_val = spot_dict.get('总市值', 0)
                result['total_market_cap'] = _to_float(cap_val) or 0.0
                fcap_val = spot_dict.get('流通市值', 0)
                result['float_market_cap'] = _to_float(fcap_val) or 0.0
        except Exception:
            pass

        if self._cache is not None:
            try:
                self._cache.save_company_profile(code, result)
            except Exception:
                pass

        return result

    def get_stock_news(self, code: str) -> List[Dict]:
        """
        获取股票最近新闻，缓存 2 小时
        返回: [{"title", "content", "source", "time"}, ...]
        """
        if self._cache is not None:
            cached = self._cache.get_stock_news(code)
            if cached is not None:
                return cached

        news_list = []
        try:
            import akshare as ak
            df = ak.stock_news_em(symbol=code)
            if df is not None and not df.empty:
                for _, row in df.head(5).iterrows():
                    news_list.append({
                        'title': str(row.get('新闻标题', '')),
                        'content': str(row.get('新闻内容', ''))[:200],
                        'source': str(row.get('文章来源', '')),
                        'time': str(row.get('发布时间', '')),
                    })
        except Exception:
            pass

        if self._cache is not None and news_list:
            try:
                self._cache.save_stock_news(code, news_list)
            except Exception:
                pass

        return news_list

    def close(self):
        """占位方法，保持接口一致"""
        pass


def _to_float(val) -> float | None:
    try:
        s = str(val).replace('%', '').replace(',', '').strip()
        if s in ('-', '--', '', 'nan', 'None'):
            return None
        return float(s)
    except Exception:
        return None
