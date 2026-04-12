#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高级中长线指标库 (AdvancedLongTermIndicators)"""

import numpy as np
import pandas as pd
from typing import Tuple, Dict, Optional


class AdvancedLongTermIndicators:

    # ── DMI ───────────────────────────────────────────────────────────────────
    def calc_dmi(self, df: pd.DataFrame, period: int = 14) \
            -> Tuple[pd.Series, pd.Series, pd.Series]:
        """返回 (plus_di, minus_di, adx)"""
        high, low, close = df['high'], df['low'], df['close']
        prev_close = close.shift(1)
        tr = pd.concat([high - low,
                        (high - prev_close).abs(),
                        (low  - prev_close).abs()], axis=1).max(axis=1)
        up_move   = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        atr      = tr.ewm(span=period, adjust=False).mean()
        plus_di  = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr
        dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.ewm(span=period, adjust=False).mean()
        return plus_di, minus_di, adx

    def analyze_dmi_signal(self, plus_di: float, minus_di: float, adx: float) -> Dict:
        """分析DMI信号"""
        if plus_di > minus_di and adx > 30:
            signal, strength = 'strong_buy', '强势多头'
        elif plus_di > minus_di and adx > 20:
            signal, strength = 'buy', '多头'
        elif minus_di > plus_di and adx > 30:
            signal, strength = 'strong_sell', '强势空头'
        elif minus_di > plus_di:
            signal, strength = 'sell', '空头'
        else:
            signal, strength = 'neutral', '中性'
        return {
            'signal': signal,
            'strength': strength,
            'plus_di': plus_di,
            'minus_di': minus_di,
            'adx': adx,
        }

    # ── PEG ───────────────────────────────────────────────────────────────────
    def calc_peg_ratio(self, pe: float, growth_rate: float) -> Dict:
        """计算PEG比率"""
        if pe <= 0 or growth_rate <= 0:
            return {'peg': None, 'level': '无效'}
        peg = pe / growth_rate
        if   peg < 0.8:  level = '低估'
        elif peg < 1.2:  level = '合理'
        elif peg < 2.0:  level = '偏高'
        else:             level = '高估'
        return {'peg': round(peg, 3), 'level': level}

    # ── Signal Optimizer ─────────────────────────────────────────────────────
    def optimize_signal_trigger(self, signals: dict) -> Dict:
        """
        综合多维信号，输出最终买卖决策
        signals: {name: {'signal': 'buy'|'sell'|'neutral', ...}, ...}
        """
        buy_count = sum(1 for v in signals.values() if v.get('signal') in ('buy', 'strong_buy'))
        sell_count = sum(1 for v in signals.values() if v.get('signal') in ('sell', 'strong_sell'))
        total = len(signals)

        if buy_count >= total * 0.7:
            decision = '强烈买入'
        elif buy_count >= total * 0.5:
            decision = '买入'
        elif sell_count >= total * 0.5:
            decision = '卖出'
        else:
            decision = '观望'

        reasons = []
        for name, v in signals.items():
            sig = v.get('signal', '')
            if sig in ('buy', 'strong_buy'):
                reasons.append(f'{name}多头信号')
            elif sig in ('sell', 'strong_sell'):
                reasons.append(f'{name}空头信号')

        return {
            'decision': decision,
            'buy_count': buy_count,
            'sell_count': sell_count,
            'reasons': reasons,
        }
