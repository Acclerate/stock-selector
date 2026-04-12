#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中长线技术指标计算库 (AdvancedIndicators)"""

import numpy as np
import pandas as pd
from typing import Tuple, Dict


class AdvancedIndicators:

    # ── Trend Score ───────────────────────────────────────────────────────────
    def score_trend(self, df: pd.DataFrame) -> Dict:
        """MA多头排列趋势评分，满分100分"""
        close = df['close']
        ma5  = close.rolling(5).mean()
        ma10 = close.rolling(10).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()

        price = close.iloc[-1]
        score, reasons = 0, []

        # 多头排列
        if ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]:
            score += 40; reasons.append('均线完美多头排列')
        elif ma5.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]:
            score += 30; reasons.append('均线多头排列')
        elif ma5.iloc[-1] > ma20.iloc[-1]:
            score += 20; reasons.append('短期均线在中期上方')

        # 价格位置
        if price > ma5.iloc[-1]:
            score += 20; reasons.append('价格站上MA5')
        if price > ma20.iloc[-1]:
            score += 20; reasons.append('价格站上MA20')
        if price > ma60.iloc[-1]:
            score += 20; reasons.append('价格站上MA60')

        # 斜率（MA20 5日内是否上行）
        if ma20.iloc[-1] > ma20.iloc[-5]:
            score = min(100, score + 10); reasons.append('MA20向上')

        if   score >= 80: rating = '强势上涨'
        elif score >= 60: rating = '稳健上涨'
        elif score >= 40: rating = '震荡偏强'
        elif score >= 20: rating = '震荡偏弱'
        else:              rating = '下行趋势'

        return {
            'score': score,
            'rating': rating,
            'reasons': reasons,
            'ma5':  ma5.iloc[-1],
            'ma10': ma10.iloc[-1],
            'ma20': ma20.iloc[-1],
            'ma60': ma60.iloc[-1],
        }

    # ── OBV ───────────────────────────────────────────────────────────────────
    def calc_obv(self, df: pd.DataFrame) -> pd.Series:
        direction = np.sign(df['close'].diff().fillna(0))
        return (direction * df['volume']).cumsum()

    # ── Volume Ratio ─────────────────────────────────────────────────────────
    def calc_volume_ratio(self, df: pd.DataFrame, period: int = 20) -> pd.Series:
        avg = df['volume'].rolling(period).mean()
        return df['volume'] / avg.replace(0, np.nan)

    # ── ADX / DI ──────────────────────────────────────────────────────────────
    def calc_adx(self, df: pd.DataFrame, period: int = 14) \
            -> Tuple[pd.Series, pd.Series, pd.Series]:
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
        return adx, plus_di, minus_di

    # ── ATR ───────────────────────────────────────────────────────────────────
    def calc_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, prev_close = df['high'], df['low'], df['close'].shift(1)
        tr = pd.concat([high - low,
                        (high - prev_close).abs(),
                        (low  - prev_close).abs()], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    # ── Bias (乖离率) ─────────────────────────────────────────────────────────
    def calc_bias(self, df: pd.DataFrame, period: int = 20) -> pd.Series:
        ma = df['close'].rolling(period).mean()
        return (df['close'] - ma) / ma.replace(0, np.nan) * 100
