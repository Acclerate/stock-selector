#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版A选股引擎 - 单元测试
直接实例化并分析几只股票，验证所有评分维度不报错
"""

import sys
import os

# 路径注入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'data'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'selectors'))

from enhanced_long_term_selector_a import EnhancedLongTermSelectorA


def test_analyze_single_stock():
    """测试单只股票分析，验证所有维度评分正常"""
    selector = EnhancedLongTermSelectorA()

    # 选几只代表性股票测试
    test_codes = ["600519", "000001", "601318"]

    for code in test_codes:
        print(f"\n{'='*50}")
        print(f"测试股票: {code}")
        print(f"{'='*50}")

        result = selector.analyze_single_stock(code)

        if result is None:
            print(f"  [WARN] {code}: returned None")
            continue

        # 验证必要字段
        assert 'score' in result, f"{code}: 缺少 score"
        assert 'rating' in result, f"{code}: 缺少 rating"
        assert 'details' in result, f"{code}: 缺少 details"
        assert 'recommend' in result, f"{code}: 缺少 recommend"
        assert 'buy_signals' in result, f"{code}: 缺少 buy_signals"

        d = result['details']

        # 验证11个评分维度全部存在
        expected_dims = [
            'trend',           # 1. 技术趋势
            'fundamental',     # 2. 基本面
            'financial_quality',  # 3. 财务质量
            'valuation',       # 4. 估值
            'momentum',        # 5. 动量
            'volume',          # 6. 量能
            'dmi',             # 7. DMI
            'short_term_timing',  # 8. 短线择时
            'fund_flow',       # 9. 资金流
            'lhb',             # 10. 龙虎榜
            'industry_strength',  # 11. 行业强弱
        ]

        for dim in expected_dims:
            assert dim in d, f"{code}: 缺少评分维度 {dim}"

        # 验证各维度有 score 字段
        for dim in expected_dims:
            assert 'score' in d[dim], f"{code}: 维度 {dim} 缺少 score"

        # 验证分数范围
        assert 0 <= result['score'] <= 100, f"{code}: 分数 {result['score']} 超出 0-100"

        # 打印结果摘要
        print(f"  [OK] {result['name']} ({code})")
        print(f"     score: {result['score']:.1f} ({result['rating']})")
        print(f"     recommend: {result['recommend']}")
        print(f"     buy_signals: {result['buy_signal_count']}")
        for dim in expected_dims:
            print(f"     {dim}: {d[dim]['score']:.1f} 分")

    selector.close()
    print(f"\n{'='*50}")
    print("[OK] ALL TESTS PASSED")
    print(f"{'='*50}")


if __name__ == '__main__':
    test_analyze_single_stock()
