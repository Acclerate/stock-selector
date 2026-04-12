#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短线技术指标计算库"""

import numpy as np
import pandas as pd
from typing import Tuple, Dict


class ShortTermIndicators:

    # ── RSI ──────────────────────────────────────────────────────────────────
    def calc_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - 100 / (1 + rs)

    # ── KDJ ──────────────────────────────────────────────────────────────────
    def calc_kdj(self, df: pd.DataFrame, n: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        low_min  = df['low'].rolling(n).min()
        high_max = df['high'].rolling(n).max()
        rsv = (df['close'] - low_min) / (high_max - low_min).replace(0, np.nan) * 100
        rsv = rsv.fillna(50)
        k = rsv.ewm(com=2, min_periods=1).mean()
        d = k.ewm(com=2, min_periods=1).mean()
        j = 3 * k - 2 * d
        return k, d, j

    def detect_kdj_cross(self, k: pd.Series, d: pd.Series, j: pd.Series) -> Dict:
        k_prev, k_cur = k.iloc[-2], k.iloc[-1]
        d_prev, d_cur = d.iloc[-2], d.iloc[-1]
        j_cur = j.iloc[-1]
        golden = k_prev < d_prev and k_cur > d_cur
        dead   = k_prev > d_prev and k_cur < d_cur
        oversold  = k_cur < 30 and d_cur < 30
        overbought = k_cur > 80 and d_cur > 80
        score, signals = 0, []
        if golden and oversold:
            score = 20; signals.append('KDJ超卖金叉')
        elif golden:
            score = 15; signals.append('KDJ金叉')
        elif oversold:
            score = 10; signals.append('KDJ超卖')
        elif overbought:
            score = 0;  signals.append('KDJ超买')
        elif not dead:
            score = 8
        return {'score': score, 'signals': signals, 'k': k_cur, 'd': d_cur, 'j': j_cur,
                'golden_cross': golden, 'dead_cross': dead, 'oversold': oversold, 'overbought': overbought}

    # ── MACD ─────────────────────────────────────────────────────────────────
    def calc_macd_short(self, df: pd.DataFrame, fast=12, slow=26, signal=9) \
            -> Tuple[pd.Series, pd.Series, pd.Series]:
        ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
        dif  = ema_fast - ema_slow
        dea  = dif.ewm(span=signal, adjust=False).mean()
        hist = (dif - dea) * 2
        return dif, dea, hist

    def detect_macd_cross(self, dif: pd.Series, dea: pd.Series, hist: pd.Series) -> Dict:
        dif_cur, dif_prev = dif.iloc[-1], dif.iloc[-2]
        dea_cur, dea_prev = dea.iloc[-1], dea.iloc[-2]
        hist_cur, hist_prev = hist.iloc[-1], hist.iloc[-2]
        golden = dif_prev < dea_prev and dif_cur > dea_cur
        red_col = hist_cur > 0
        shrink  = hist_cur > 0 and hist_cur < hist_prev  # 红柱缩短
        score, signals = 0, []
        if golden and dif_cur < 0:
            score = 15; signals.append('MACD零轴下方金叉')
        elif golden:
            score = 12; signals.append('MACD金叉')
        elif red_col and not shrink:
            score = 10; signals.append('MACD红柱扩张')
        elif red_col:
            score = 5;  signals.append('MACD红柱')
        return {'score': score, 'signals': signals,
                'golden_cross': golden, 'histogram': hist_cur,
                'dif': dif_cur, 'dea': dea_cur}

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    def calc_bollinger(self, df: pd.DataFrame, period=20, std_dev=2) \
            -> Tuple[pd.Series, pd.Series, pd.Series]:
        mid   = df['close'].rolling(period).mean()
        std   = df['close'].rolling(period).std()
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        return upper, mid, lower

    def detect_bollinger_signal(self, df: pd.DataFrame,
                                 upper: pd.Series, middle: pd.Series, lower: pd.Series) -> Dict:
        price = df['close'].iloc[-1]
        score, signals = 0, []
        bw = (upper.iloc[-1] - lower.iloc[-1]) / middle.iloc[-1]  # bandwidth
        if price <= lower.iloc[-1]:
            score = 15; signals.append('布林下轨支撑')
        elif price <= middle.iloc[-1]:
            score = 10; signals.append('布林中轨以下，有上升空间')
        elif price >= upper.iloc[-1]:
            score = 0;  signals.append('布林上轨，注意压力')
        else:
            score = 8
        return {'score': score, 'signals': signals,
                'upper': upper.iloc[-1], 'middle': middle.iloc[-1], 'lower': lower.iloc[-1],
                'bandwidth': bw, 'price_position': (price - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1])}

    # ── Volume ────────────────────────────────────────────────────────────────
    def detect_volume_surge(self, df: pd.DataFrame, ratio: float = 1.5) -> Dict:
        avg_vol = df['volume'].rolling(20).mean().iloc[-1]
        cur_vol = df['volume'].iloc[-1]
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0
        price_up  = df['close'].iloc[-1] > df['close'].iloc[-2]
        score, signals = 0, []
        if vol_ratio >= ratio and price_up:
            score = 15; signals.append(f'放量上涨({vol_ratio:.1f}倍)')
        elif vol_ratio >= ratio:
            score = 5;  signals.append(f'放量({vol_ratio:.1f}倍)')
        elif vol_ratio < 0.5:
            score = 3;  signals.append('缩量')
        else:
            score = 8
        return {'score': score, 'signals': signals, 'volume_ratio': vol_ratio, 'price_up': price_up}

    # ── ATR (short-term 10 day) ───────────────────────────────────────────────
    def calc_atr_short(self, df: pd.DataFrame, period: int = 10) -> pd.Series:
        high, low, close_prev = df['high'], df['low'], df['close'].shift(1)
        tr = pd.concat([high - low,
                        (high - close_prev).abs(),
                        (low  - close_prev).abs()], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def calc_trade_points(self, current_price: float, atr_value: float,
                          stop_multiplier: float = 2.0,
                          profit_multiplier: float = 3.0) -> Dict:
        if atr_value > 0 and current_price > 0:
            stop_loss   = current_price - atr_value * stop_multiplier
            take_profit = current_price + atr_value * profit_multiplier
        else:
            stop_loss   = current_price * 0.93
            take_profit = current_price * 1.15
        stop_loss_pct   = (stop_loss   - current_price) / current_price * 100
        take_profit_pct = (take_profit - current_price) / current_price * 100
        risk = current_price - stop_loss
        reward = take_profit - current_price
        rr = reward / risk if risk > 0 else 1.5
        return {
            'buy_price': round(current_price, 2),
            'stop_loss': round(stop_loss, 2),
            'take_profit': round(take_profit, 2),
            'stop_loss_pct': round(stop_loss_pct, 2),
            'take_profit_pct': round(take_profit_pct, 2),
            'risk_reward_ratio': round(rr, 2),
        }
