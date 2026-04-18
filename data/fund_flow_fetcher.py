#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个股资金流获取模块 — 填充 fund_flow 表的超大单/大单/中单/小单数据
数据来源: akshare stock_individual_fund_flow
"""

from typing import Dict, Optional


class FundFlowFetcher:
    """获取个股详细资金流（超大单/大单/中单/小单净流入）"""

    def __init__(self, cache=None):
        """
        :param cache: StockCache 实例。传入后数据会被缓存（盘中 30 分钟，盘后到次日）。
        """
        self._cache = cache

    def fetch_and_save(self, code: str) -> Optional[Dict]:
        """
        获取个股资金流数据。先查缓存，miss 时调用 akshare。

        返回示例:
        {
            'main_in': 123456.78,       # 主力净流入（万元）
            'retail_in': -54321.0,      # 散户净流入（万元）
            'main_ratio': 5.23,         # 主力净流入占比(%)
            'super_large_net': 88000.0, # 超大单净流入（万元）
            'large_net': 35456.78,      # 大单净流入（万元）
            'medium_net': -22000.0,     # 中单净流入（万元）
            'small_net': -32456.78,     # 小单净流入（万元）
            'days_continuous': 3,       # 连续净流入天数（正为流入，负为流出）
        }
        """
        # ── 缓存检查 ─────────────────────────────────────────────────
        if self._cache is not None:
            cached = self._cache.get_fund_flow(code)
            if cached is not None:
                return cached

        result = self._fetch_from_api(code)
        if result and self._cache is not None:
            try:
                self._cache.save_fund_flow(code, result)
            except Exception:
                pass

        return result

    def _fetch_from_api(self, code: str) -> Optional[Dict]:
        """从 akshare 获取资金流数据"""
        try:
            import akshare as ak

            # 判断市场: 6 开头为沪市，其余为深市
            market = "sh" if code.startswith("6") else "sz"

            df = ak.stock_individual_fund_flow(stock=code, market=market)
            if df is None or df.empty:
                return None

            row = df.iloc[-1]  # 最新一天数据

            def _safe_get(key_patterns, default=0.0):
                """从 DataFrame 列中安全取值"""
                for col in df.columns:
                    col_str = str(col)
                    for pattern in key_patterns:
                        if pattern in col_str:
                            val = row[col]
                            try:
                                return float(val)
                            except (ValueError, TypeError):
                                return default
                return default

            result = {
                'main_in': _safe_get(['主力净流入-净额', '主力净流入']),
                'retail_in': _safe_get(['散户净流入-净额', '散户净流入']),
                'main_ratio': _safe_get(['主力净流入-净占比', '主力净流入占比']),
                'super_large_net': _safe_get(['超大单净流入-净额', '超大单净流入']),
                'large_net': _safe_get(['大单净流入-净额', '大单净流入']),
                'medium_net': _safe_get(['中单净流入-净额', '中单净流入']),
                'small_net': _safe_get(['小单净流入-净额', '小单净流入']),
                'days_continuous': 0,
            }

            # 计算连续净流入/流出天数
            try:
                main_col = None
                for col in df.columns:
                    if '主力净流入-净额' in str(col) or '主力净流入' == str(col):
                        main_col = col
                        break
                if main_col:
                    count = 0
                    for i in range(len(df) - 1, -1, -1):
                        val = float(df.iloc[i][main_col])
                        if i == len(df) - 1:
                            positive = val > 0
                        if (val > 0) == positive:
                            count += 1
                        else:
                            break
                    result['days_continuous'] = count if positive else -count
            except Exception:
                pass

            return result

        except Exception as e:
            print(f"[FundFlowFetcher] 获取 {code} 资金流失败: {e}", flush=True)
            return None


# ── 测试 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    fetcher = FundFlowFetcher()
    for code in ["601138", "600519", "002475"]:
        result = fetcher.fetch_and_save(code)
        if result:
            print(f"\n{code}:")
            print(f"  主力净流入: {result['main_in']:.0f} 万元")
            print(f"  超大单: {result['super_large_net']:.0f}, 大单: {result['large_net']:.0f}")
            print(f"  中单: {result['medium_net']:.0f}, 小单: {result['small_net']:.0f}")
            print(f"  连续天数: {result['days_continuous']}")
        else:
            print(f"\n{code}: 获取失败")
