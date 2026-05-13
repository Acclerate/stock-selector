#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版A中长线选股引擎
基于增强版，新增6个评分维度：
  P0: 财务质量(毛利率+净利率+负债率)、短线择时(RSI+KDJ+MACD+布林带)
  P1: PB估值、资金流精细化(超大单占比+连续流入天数)
  P2: 龙虎榜加分、行业相对强弱

评分体系 (原始150分 → 归一化100分):
  技术趋势(20) + 基本面(20) + 财务质量(15) + 估值(15)
  + 动量(10) + 量能(10) + DMI(10) + 短线择时(15)
  + 资金流(10) + 龙虎榜(5) + 行业强弱(10) = 150

推荐阈值: 归一化 ≥ 60
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
from typing import List, Dict
from smart_data_source import SmartDataSource
from stock_cache_db import StockCache
from advanced_indicators import AdvancedIndicators
from advanced_long_term_indicators import AdvancedLongTermIndicators
from fundamental_data import FundamentalData
from short_term_indicators import ShortTermIndicators


class EnhancedLongTermSelectorA:
    """增强版A中长线选股引擎 — 全维度升级"""

    MAX_RAW_SCORE = 150

    def __init__(self):
        self.ds = SmartDataSource()
        self.cache = StockCache()
        self.indicators = AdvancedIndicators()
        self.advanced_indicators = AdvancedLongTermIndicators()
        self.fundamental = FundamentalData(cache=self.cache)
        self._sti = ShortTermIndicators()
        self._fund_fetcher = None
        # 行业指数数据缓存（行业名→近20日涨幅%），多线程安全
        self._industry_index_cache: Dict[str, float] = {}

    # ── 股票池 ─────────────────────────────────────────────────────────────────
    def get_index_stocks(self) -> List[str]:
        """从四大指数成分股中获取扫描范围，过滤创业板和科创板"""
        import akshare as ak
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'data'))
        try:
            from config import SCAN_INDICES
        except Exception:
            SCAN_INDICES = ["000300", "000905", "000852", "000016"]

        def _filter(codes):
            seen = set()
            result = []
            for c in codes:
                if c in seen or c.startswith('3') or c.startswith('688'):
                    continue
                seen.add(c)
                result.append(c)
            return result

        all_codes = []
        for idx in SCAN_INDICES:
            idx_name = {"000300": "沪深300", "000905": "中证500", "000852": "中证1000", "000016": "上证50"}.get(idx, idx)
            try:
                print(f"获取{idx_name}({idx})成分股（东方财富）...", flush=True)
                df = ak.index_stock_cons(symbol=idx)
                codes = df['品种代码'].astype(str).str.zfill(6).tolist()
                all_codes.extend(codes)
                print(f"  ✅ {idx_name}: {len(codes)} 只", flush=True)
                continue
            except Exception as e:
                print(f"  ⚠️ 东方财富失败: {e}", flush=True)
            try:
                print(f"获取{idx_name}({idx})成分股（中证官网）...", flush=True)
                df = ak.index_stock_cons_weight_csindex(symbol=idx)
                codes = df['成分券代码'].astype(str).str.zfill(6).tolist()
                all_codes.extend(codes)
                print(f"  ✅ {idx_name}: {len(codes)} 只", flush=True)
            except Exception as e:
                print(f"  ⚠️ 中证官网失败: {e}", flush=True)

        result = _filter(all_codes)
        if result:
            print(f"✅ 合计获取到 {len(result)} 只主板成分股", flush=True)
        else:
            print("❌ 所有成分股数据源均失败", flush=True)
        return result

    # ── 主分析 ─────────────────────────────────────────────────────────────────
    def analyze_single_stock(self, code: str, pre_filter: bool = True) -> Dict:
        """增强版A单股分析 — 11维评分
        pre_filter=True 时启用两阶段预筛: 技术面太弱则跳过API调用 (批量扫描用)
        pre_filter=False 时始终执行全部分析 (单股查询用)
        """
        import time
        t0 = time.time()
        def _log(step: str):
            print(f"  [{code}] {step} ({time.time()-t0:.1f}s)", flush=True)

        try:
            # 获取历史数据（日线120天）
            _log("获取历史数据...")
            df = self.ds.get_history_data(code, days=120)
            if df is None or df.empty or len(df) < 60:
                _log("历史数据不足，跳过")
                return None

            _log("获取基础信息(cache)...")
            stock_info = self.cache.get_stock(code)
            if not stock_info:
                _log("无缓存信息，跳过")
                return None

            current_price = float(stock_info.get('price', df['close'].iloc[-1]))
            score = 0.0
            details = {}

            # ════════════════════════════════════════════════════════════════════
            # Phase 1: 技术面评分 (仅需K线数据，无需API调用)
            # 趋势(20) + 动量(10) + 量能(10) + DMI(10) + 短线择时(15) = 65
            # ════════════════════════════════════════════════════════════════════

            # 1. 技术趋势 (20分) — 细粒度MA排列评分
            _log("Phase1: 技术面评分...")
            trend_score, trend_signals, trend = self._calc_trend_score(df)
            score += trend_score
            details['trend'] = {
                'score': round(trend_score, 1),
                'rating': trend['rating'],
                'reasons': trend['reasons'],
                'ma20': trend.get('ma20', 0),
                'ma60': trend.get('ma60', 0),
            }

            # 2. 动量 (10分)
            returns_20d = (df['close'].iloc[-1] - df['close'].iloc[-21]) / df['close'].iloc[-21] * 100
            momentum_score = float(np.clip(returns_20d / 10, -1, 1) * 10)
            score += momentum_score
            details['momentum'] = {
                'score': round(momentum_score, 1),
                'returns_20d': round(returns_20d, 2),
            }

            # 3. 量能 OBV (10分)
            obv = self.indicators.calc_obv(df)
            obv_now = obv.iloc[-1]
            obv_20d = obv.iloc[-20]
            obv_change = (obv_now - obv_20d) / (abs(obv_20d) + 1)
            volume_score = float(np.clip(3 + obv_change * 70, -3, 10))
            score += volume_score
            details['volume'] = {
                'score': round(volume_score, 1),
                'obv_trend': 'up' if obv_now > obv_20d else 'down',
            }

            # 4. DMI (10分)
            plus_di, minus_di, adx = self.advanced_indicators.calc_dmi(df)
            dmi_analysis = self.advanced_indicators.analyze_dmi_signal(
                plus_di.iloc[-1], minus_di.iloc[-1], adx.iloc[-1]
            )
            di_diff = (plus_di.iloc[-1] - minus_di.iloc[-1]) / 30
            adx_strength = min(adx.iloc[-1] / 30, 1.0)
            dmi_score = float(np.clip(di_diff * 7 * adx_strength, -5, 10))
            score += dmi_score
            details['dmi'] = {
                'score': round(dmi_score, 1),
                **dmi_analysis,
            }

            # 5. 短线择时 (15分): RSI(4) + KDJ(4) + MACD(4) + 布林(3)
            timing_score = self._calc_short_term_timing_score(df)
            score += timing_score['score']
            details['short_term_timing'] = timing_score

            # ════════════════════════════════════════════════════════════════════
            # Phase 1 预筛: 技术面太弱则跳过 Phase 2 (节省 API 调用)
            # Phase 2 满分 75 = 基本面(20)+财务质量(15)+估值(15)+资金流(10)+龙虎榜(5)+行业强弱(10)
            # 归一化60分对应原始90分, 若 phase1 + 75 < 90 → 不可能达标 → 跳过
            # ════════════════════════════════════════════════════════════════════
            phase2_max = 75
            min_raw_for_recommend = 60 / 100 * self.MAX_RAW_SCORE
            if pre_filter and (score + phase2_max < min_raw_for_recommend):
                return None

            # ════════════════════════════════════════════════════════════════════
            # Phase 2: 深度评分 (需要 API 调用或缓存查询)
            # 基本面(20)+财务质量(15)+估值(15)+资金流(10)+龙虎榜(5)+行业强弱(10) = 75
            # ════════════════════════════════════════════════════════════════════

            # 6. 基本面 (20分): ROE(7) + 利润增长(7) + 股息率(6)
            _log("Phase2: 基本面+估值评分...")
            fundamental_data = self.fundamental.get_stock_fundamental(code)
            fundamental_score = self._calc_fundamental_score(fundamental_data)
            score += fundamental_score['score']
            details['fundamental'] = fundamental_score

            # 7. 财务质量 (15分): 毛利率(5) + 净利率(5) + 负债率(5)
            quality_score = self._calc_financial_quality_score(fundamental_data)
            score += quality_score['score']
            details['financial_quality'] = quality_score

            # 8. 估值 (15分): PEG(8) + PB(7)
            valuation_score = self._calc_valuation_score(
                fundamental_data.get('pe', 0),
                fundamental_data.get('profit_growth', 0),
                fundamental_data.get('pb', 0),
            )
            score += valuation_score['score']
            details['valuation'] = valuation_score

            # 9. 资金流精细化 (10分)
            _log("资金流评分...")
            fund_flow = self._get_fund_flow(code)
            fund_flow_score = self._calc_fund_flow_score(fund_flow)
            score += fund_flow_score['score']
            details['fund_flow'] = fund_flow_score

            # 10. 龙虎榜 (5分)
            _log("龙虎榜评分...")
            lhb_score = self._calc_lhb_score(code)
            score += lhb_score['score']
            details['lhb'] = lhb_score

            # 11. 行业相对强弱 (10分)
            _log("行业强弱评分...")
            industry_score = self._calc_industry_strength_score(code, returns_20d)
            score += industry_score['score']
            details['industry_strength'] = industry_score

            # ════════════════════════════════════════════════════════════════════
            # 补充技术指标（供 AI 分析）
            # ════════════════════════════════════════════════════════════════════
            ma5 = df['close'].rolling(5).mean()
            ma10 = df['close'].rolling(10).mean()
            details['ma'] = {
                'ma5': float(ma5.iloc[-1]),
                'ma10': float(ma10.iloc[-1]),
                'ma20': float(trend.get('ma20', 0)),
                'ma60': float(trend.get('ma60', 0)),
            }
            rsi_s = self._sti.calc_rsi(df)
            details['rsi'] = {'value': float(rsi_s.iloc[-1])}
            kdj_k, kdj_d, kdj_j = self._sti.calc_kdj(df)
            details['kdj'] = {'k': float(kdj_k.iloc[-1]), 'd': float(kdj_d.iloc[-1]), 'j': float(kdj_j.iloc[-1])}
            dif, dea, macd_hist = self._sti.calc_macd_short(df)
            details['macd'] = {'dif': float(dif.iloc[-1]), 'dea': float(dea.iloc[-1]), 'macd_hist': float(macd_hist.iloc[-1])}
            boll_upper, boll_mid, boll_lower = self._sti.calc_bollinger(df)
            details['bollinger'] = {
                'upper': float(boll_upper.iloc[-1]),
                'middle': float(boll_mid.iloc[-1]),
                'lower': float(boll_lower.iloc[-1]),
            }

            # ════════════════════════════════════════════════════════════════════
            # 综合信号
            # ════════════════════════════════════════════════════════════════════
            _log("综合信号评分...")
            signals = {
                'trend': trend,
                'momentum': {'signal': 'buy' if returns_20d > 0 else 'sell'},
                'volume': {'signal': 'buy' if obv.iloc[-1] > obv.iloc[-20] else 'sell'},
                'dmi': dmi_analysis,
                'valuation': {'signal': 'buy' if valuation_score.get('peg_level', '') in ['低估', '合理'] or valuation_score.get('pb_level', '') in ['破净/低估值', '合理'] else 'sell'},
            }
            optimized_signal = self.advanced_indicators.optimize_signal_trigger(signals)

            # ════════════════════════════════════════════════════════════════════
            # 汇总
            # ════════════════════════════════════════════════════════════════════
            final_score = (score / self.MAX_RAW_SCORE) * 100

            buy_signals = []
            if optimized_signal['decision'] in ['强烈买入', '买入']:
                for reason in optimized_signal['reasons']:
                    buy_signals.append(reason)
            if fundamental_score['score'] >= 16:
                buy_signals.append(f"基本面优秀(ROE {fundamental_data.get('roe', 0):.1f}%)")
            if valuation_score.get('peg_level') == '低估' or valuation_score.get('pb_level') == '破净/低估值':
                peg_info = f"PEG {valuation_score.get('peg', 'N/A')}" if valuation_score.get('peg') else f"PB {valuation_score.get('pb', 0):.2f}"
                buy_signals.append(f"估值低估({peg_info})")
            if fundamental_data.get('dividend_yield', 0) >= 3:
                buy_signals.append(f"高股息({fundamental_data.get('dividend_yield', 0):.1f}%)")
            if quality_score.get('level') == 'A':
                buy_signals.append(f"财务质量优(毛利率{fundamental_data.get('gross_margin', 0):.1f}%)")
            if dmi_analysis['signal'] in ['buy', 'strong_buy']:
                buy_signals.append(f"DMI多头({dmi_analysis['strength']})")
            if lhb_score['score'] >= 3:
                buy_signals.append("龙虎榜机构净买入")
            if fund_flow_score.get('days_continuous', 0) >= 3:
                buy_signals.append(f"主力连续{fund_flow_score['days_continuous']}日净流入")
            if industry_score.get('signal') == 'outperform':
                buy_signals.append(f"行业领涨({industry_score.get('relative', 0):+.1f}%)")

            stop_loss = current_price * 0.92
            take_profit = current_price * 1.20

            result = {
                'code': code,
                'name': stock_info.get('name', 'Unknown'),
                'price': float(stock_info.get('price', 0)),
                'change_pct': float(stock_info.get('change_pct', 0)),
                'score': round(final_score, 2),
                'rating': self._get_rating(final_score),
                'details': self._convert_to_json_safe(details),
                'signal': optimized_signal,
                'recommend': final_score >= 60,
                'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'buy_signals': buy_signals,
                'buy_signal_count': len(buy_signals),
                'buy_price': current_price,
                'stop_loss': round(stop_loss, 2),
                'take_profit': round(take_profit, 2),
                'stop_loss_pct': -8.0,
                'take_profit_pct': 20.0,
                'risk_reward_ratio': 2.5,
            }
            return result

        except Exception as e:
            print(f"分析{code}失败({time.time()-t0:.1f}s): {e}", flush=True)
            import traceback
            traceback.print_exc()
            return None

    # ── 评分子方法 ─────────────────────────────────────────────────────────────

    def _calc_trend_score(self, df):
        """细粒度趋势评分 (20分): MA排列(40/30/20) + 价格vs MA(各20) + MA20方向(10)"""
        close = df['close'].astype(float)
        ma5 = close.rolling(5, min_periods=5).mean()
        ma10 = close.rolling(10, min_periods=10).mean()
        ma20 = close.rolling(20, min_periods=20).mean()
        ma60 = close.rolling(60, min_periods=60).mean()

        latest_close = float(close.iloc[-1])
        latest_ma5 = float(ma5.iloc[-1])
        latest_ma10 = float(ma10.iloc[-1])
        latest_ma20 = float(ma20.iloc[-1])
        latest_ma60 = float(ma60.iloc[-1]) if not np.isnan(ma60.iloc[-1]) else 0.0
        ma20_rising = float(ma20.iloc[-1]) > float(ma20.iloc[-2]) if len(ma20) > 1 else False

        raw = 0.0
        signals = []
        reasons = []

        # MA alignment (分档: 完美40 / 三线30 / 短中20)
        if latest_ma5 > latest_ma10 > latest_ma20 > latest_ma60:
            raw += 40
            signals.append('buy')
            reasons.append('均线完美多头排列')
        elif latest_ma5 > latest_ma20 > latest_ma60:
            raw += 30
            signals.append('buy')
            reasons.append('均线多头排列')
        elif latest_ma5 > latest_ma20:
            raw += 20
            signals.append('buy')
            reasons.append('短期均线在中期之上')

        # Price vs MAs (各20分)
        if latest_close > latest_ma5:
            raw += 20
        if latest_close > latest_ma20:
            raw += 20
            signals.append('buy')
        else:
            signals.append('sell')
        if latest_close > latest_ma60:
            raw += 20
            signals.append('buy')
        else:
            signals.append('sell')

        # MA20 direction (10分)
        if ma20_rising:
            raw += 10

        if raw >= 80: rating = '强势上涨'
        elif raw >= 60: rating = '稳健上涨'
        elif raw >= 40: rating = '震荡偏强'
        elif raw >= 20: rating = '震荡偏弱'
        else: rating = '下行趋势'

        weighted = raw / 100.0 * 20
        return weighted, signals, {
            'score': raw,
            'rating': rating,
            'reasons': reasons,
            'ma5': latest_ma5,
            'ma10': latest_ma10,
            'ma20': latest_ma20,
            'ma60': latest_ma60,
        }

    def _calc_fundamental_score(self, data: Dict) -> Dict:
        """基本面评分 (20分): ROE(7) + 利润增长(7) + 股息率(6)"""
        roe = data.get('roe', 0) or 0
        roe_score = float(np.clip(roe / 20, 0, 1) * 7)

        profit_growth = data.get('profit_growth', 0) or 0
        growth_score = float(np.clip(profit_growth / 25, 0, 1) * 7)

        dividend = data.get('dividend_yield', 0) or 0
        dividend_score = float(np.clip(dividend / 4, 0, 1) * 6)

        total = round(roe_score + growth_score + dividend_score, 1)
        level = 'A' if total >= 16 else 'B' if total >= 12 else 'C' if total >= 8 else 'D'

        return {
            'score': total,
            'level': level,
            'roe': roe,
            'profit_growth': profit_growth,
            'dividend_yield': dividend,
            'revenue_growth': data.get('revenue_growth', 0),
        }

    def _calc_financial_quality_score(self, data: Dict) -> Dict:
        """财务质量评分 (15分): 毛利率(5) + 净利率(5) + 负债率(5)  [P0]"""
        gross_margin = data.get('gross_margin', 0) or 0
        net_margin = data.get('net_margin', 0) or 0
        debt_ratio = data.get('debt_to_asset', 0) or 0

        # 全部为0时视为无数据
        if gross_margin == 0 and net_margin == 0 and debt_ratio == 0:
            return {'score': 0, 'level': '无数据', 'gross_margin': 0, 'net_margin': 0, 'debt_to_asset': 0}

        # 毛利率: 60%→5分
        gm_score = float(np.clip(gross_margin / 60, 0, 1) * 5)
        # 净利率: 30%→5分（允许负值产生负分，下限0）
        nm_score = float(np.clip(net_margin / 30, 0, 1) * 5)
        # 负债率: 20%→5分(低负债好), 80%→0分
        dr_score = float(np.clip((80 - debt_ratio) / 60, 0, 1) * 5)

        total = round(gm_score + nm_score + dr_score, 1)
        level = 'A' if total >= 12 else 'B' if total >= 9 else 'C' if total >= 6 else 'D'

        return {
            'score': total,
            'level': level,
            'gross_margin': gross_margin,
            'net_margin': net_margin,
            'debt_to_asset': debt_ratio,
        }

    def _calc_valuation_score(self, pe: float, growth: float, pb: float) -> Dict:
        """估值评分 (15分): PEG(8) + PB(7)  [P1 PB新增]"""
        # PEG (8分)
        peg_score = 0.0
        peg = None
        peg_level = '无效'
        if pe > 0 and growth > 0:
            peg_data = self.advanced_indicators.calc_peg_ratio(pe, growth)
            peg = peg_data.get('peg')
            if peg is not None:
                peg_score = float(np.clip((2.0 - peg) / 1.5, 0, 1) * 8)
                if peg < 0.8: peg_level = '低估'
                elif peg < 1.2: peg_level = '合理'
                elif peg < 2.0: peg_level = '偏高'
                else: peg_level = '高估'

        # PB (7分): PB 0.5→满分, PB 3.0→0分, 负值→0分
        pb_score = 0.0
        pb_level = '无效'
        if pb and pb > 0:
            pb_score = float(np.clip((3.0 - pb) / 2.5, 0, 1) * 7)
            if pb < 1.0: pb_level = '破净/低估值'
            elif pb < 2.0: pb_level = '合理'
            elif pb < 3.0: pb_level = '偏高'
            else: pb_level = '高估'

        total = round(peg_score + pb_score, 1)
        return {
            'score': total,
            'peg': peg,
            'peg_level': peg_level,
            'peg_score': round(peg_score, 1),
            'pb': pb,
            'pb_level': pb_level,
            'pb_score': round(pb_score, 1),
            'pe': pe,
        }

    def _calc_short_term_timing_score(self, df: pd.DataFrame) -> Dict:
        """短线择时评分 (15分): RSI(4)+KDJ(4)+MACD(4)+布林(3)  [P0]"""
        sub_scores = {}

        # RSI (4分): RSI 30→4分, RSI 65→0分, RSI>75→负分
        rsi = self._sti.calc_rsi(df)
        rsi_now = rsi.iloc[-1]
        rsi_score = float(np.clip((65 - rsi_now) / 35, -0.3, 1.0) * 4)
        sub_scores['rsi'] = {'score': round(rsi_score, 1), 'value': round(rsi_now, 1)}

        # KDJ (4分): J值+金叉/死叉
        k, d, j = self._sti.calc_kdj(df)
        kdj_result = self._sti.detect_kdj_cross(k, d, j)
        j_cur = kdj_result['j']
        j_base = float(np.clip((80 - j_cur) / 60, -0.3, 1.0) * 3)
        cross_adj = 1 if kdj_result['golden_cross'] else (-1 if kdj_result['dead_cross'] else 0)
        kdj_score = float(np.clip(j_base + cross_adj, -1, 4))
        sub_scores['kdj'] = {
            'score': round(kdj_score, 1), 'j': round(j_cur, 1),
            'golden_cross': kdj_result['golden_cross'],
        }

        # MACD (4分): 柱状图方向 + 金叉
        dif, dea, hist = self._sti.calc_macd_short(df)
        macd_result = self._sti.detect_macd_cross(dif, dea, hist)
        hist_cur = macd_result['histogram']
        hist_prev = float(hist.iloc[-2]) if len(hist) > 1 else hist_cur
        if df['close'].iloc[-1] > 0:
            scale = df['close'].iloc[-1] * 0.005
            hist_norm = hist_cur / scale if scale else 0
            hist_delta = (hist_cur - hist_prev) / scale if scale else 0
            macd_base = float(np.clip(hist_norm + hist_delta * 2, -2, 3))
        else:
            macd_base = 0.0
        if macd_result['golden_cross']:
            macd_base = max(macd_base, 3.0)
        macd_score = float(np.clip(macd_base, -1, 4))
        sub_scores['macd'] = {
            'score': round(macd_score, 1), 'hist': round(hist_cur, 4),
            'golden_cross': macd_result['golden_cross'],
        }

        # 布林带 (3分): 位置越低越好
        upper, middle, lower = self._sti.calc_bollinger(df)
        boll_result = self._sti.detect_bollinger_signal(df, upper, middle, lower)
        pos = boll_result['price_position']
        boll_score = float(np.clip((1 - pos) * 3, -0.5, 3))
        sub_scores['bollinger'] = {
            'score': round(boll_score, 1), 'position_pct': round(pos * 100, 0),
        }

        total = round(rsi_score + kdj_score + macd_score + boll_score, 1)
        return {'score': total, 'sub': sub_scores}

    def _get_fund_flow(self, code: str) -> Dict:
        """获取资金流数据"""
        try:
            from fund_flow_fetcher import FundFlowFetcher
            if not hasattr(self, '_fund_fetcher') or self._fund_fetcher is None:
                self._fund_fetcher = FundFlowFetcher(cache=self.cache)
            return self._fund_fetcher.fetch_and_save(code) or {}
        except Exception:
            return self.cache.get_fund_flow(code) or {}

    def _calc_fund_flow_score(self, fund_flow: Dict) -> Dict:
        """资金流精细化评分 (10分): 主力净流入(4)+超大单占比(3)+连续天数(3)  [P1 升级]"""
        if not fund_flow:
            return {'score': 0, 'main_in': 0, 'super_large_ratio': 0, 'days_continuous': 0}

        main_in = fund_flow.get('main_in', 0)
        main_in_wan = main_in / 10000

        # 主力净流入 (4分): ±1亿为满分/负分边界
        main_score = float(np.clip(main_in / 100000000 * 4, -2, 4))

        # 超大单占比 (3分): 超大单占主力流入的比例
        super_large = fund_flow.get('super_large_net', 0)
        large = fund_flow.get('large_net', 0)
        main_total = abs(super_large) + abs(large) + 1
        super_ratio = super_large / main_total  # -1 ~ 1
        sl_score = float(np.clip(super_ratio * 3, -1.5, 3))

        # 连续流入天数 (3分): 5天以上满分
        days = fund_flow.get('days_continuous', 0)
        if days > 0:
            days_score = min(days, 5) / 5 * 3
        else:
            days_score = max(days, -5) / 5 * 1.5  # 流出扣分少一些
            days_score = max(days_score, -1.5)

        total = round(main_score + sl_score + days_score, 1)
        return {
            'score': total,
            'main_in': round(main_in_wan, 0),
            'super_large_ratio': round(super_ratio, 2),
            'days_continuous': days,
        }

    def _calc_lhb_score(self, code: str) -> Dict:
        """龙虎榜评分 (5分)  [P2]"""
        try:
            lhb = self.cache.get_lhb(code)
            if not lhb:
                return {'score': 0, 'on_list': False, 'signal': '未上榜'}
            net_amount = lhb.get('net_amount', 0)
            on_list = True
            if net_amount > 0:
                # 净买入，按金额给分（5000万→满分）
                s = float(np.clip(net_amount / 50000000 * 5, 0, 5))
                return {'score': s, 'on_list': on_list, 'signal': f'净买入{net_amount/10000:.0f}万', 'net_amount': net_amount}
            else:
                s = float(np.clip(net_amount / 50000000 * 3, -3, 0))
                return {'score': s, 'on_list': on_list, 'signal': f'净卖出{net_amount/10000:.0f}万', 'net_amount': net_amount}
        except Exception:
            return {'score': 0, 'on_list': False, 'signal': '查询失败'}

    def _calc_industry_strength_score(self, code: str, stock_return_20d: float) -> Dict:
        """行业相对强弱评分 (10分)  [P2]"""
        try:
            industry = self._get_stock_industry(code)
            if not industry:
                return {'score': 5.0, 'signal': 'neutral', 'industry': '', 'relative': 0}

            industry_return = self._get_industry_return(industry)
            if industry_return is None:
                return {'score': 5.0, 'signal': 'neutral', 'industry': industry, 'relative': 0}

            relative = stock_return_20d - industry_return
            # 相对涨幅 +5%→10分, 持平→5分, -5%→0分
            s = float(np.clip(5 + relative / 2, 0, 10))
            signal = 'outperform' if relative > 3 else ('underperform' if relative < -3 else 'neutral')

            return {
                'score': round(s, 1),
                'signal': signal,
                'industry': industry,
                'industry_return': round(industry_return, 2),
                'relative': round(relative, 2),
            }
        except Exception:
            return {'score': 5.0, 'signal': 'neutral', 'industry': '', 'relative': 0}

    # ── 辅助方法 ───────────────────────────────────────────────────────────────

    def _get_stock_industry(self, code: str) -> str:
        """获取股票所属行业，优先缓存"""
        try:
            profile = self.cache.get_company_profile(code)
            if profile and profile.get('industry'):
                return profile['industry']
        except Exception:
            pass
        # 实时获取并缓存
        try:
            profile = self.fundamental.get_company_profile(code)
            return profile.get('industry', '')
        except Exception:
            return ''

    def _get_industry_return(self, industry_name: str):
        """获取行业近20日涨幅%，带缓存"""
        if not industry_name:
            return None
        if industry_name in self._industry_index_cache:
            return self._industry_index_cache[industry_name]

        # 占位，防止重复请求
        self._industry_index_cache[industry_name] = None

        try:
            import akshare as ak
            end = datetime.now()
            start = end - timedelta(days=40)
            df = ak.stock_board_industry_hist_em(
                symbol=industry_name, period="日k",
                start_date=start.strftime('%Y%m%d'),
                end_date=end.strftime('%Y%m%d'),
            )
            if df is not None and not df.empty and len(df) >= 2:
                close_col = '收盘' if '收盘' in df.columns else 'close'
                prices = df[close_col].astype(float)
                ret = (prices.iloc[-1] - prices.iloc[0]) / prices.iloc[0] * 100
                self._industry_index_cache[industry_name] = ret
                return ret
        except Exception:
            pass
        return None

    def _get_rating(self, score: float) -> str:
        if score >= 80: return 'A+'
        elif score >= 70: return 'A'
        elif score >= 60: return 'B+'
        elif score >= 50: return 'B'
        elif score >= 40: return 'C'
        else: return 'D'

    def _convert_to_json_safe(self, obj):
        import numpy as np
        import math
        if isinstance(obj, dict):
            return {k: self._convert_to_json_safe(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_to_json_safe(item) for item in obj]
        elif isinstance(obj, (np.integer, np.int64, np.int32)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64, np.float32)):
            val = float(obj)
            if math.isnan(val) or math.isinf(val):
                return None
            return val
        elif isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        elif isinstance(obj, np.bool_):
            return bool(obj)
        elif obj is None:
            return None
        else:
            return obj

    # ── 选股入口 ───────────────────────────────────────────────────────────────
    def select_top_stocks(self, top_n: int = 5) -> List[Dict]:
        """选择TOP N股票"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        print("=" * 60)
        print(f"🎯 增强版A中长线选股 - TOP {top_n}")
        print(f"   11维评分: 趋势+基本面+财务质量+估值+动量+量能+DMI+短线择时+资金流+龙虎榜+行业强弱")
        print("=" * 60)
        print()

        stocks = self.get_index_stocks()
        if not stocks:
            print("❌ 获取指数成分股失败")
            return []

        try:
            from diggold_source import DiggoldSource
            DiggoldSource.set_filter_codes(stocks)
        except Exception:
            pass

        print(f"📊 并行分析 {len(stocks)} 只股票（11维评分）...", flush=True)
        print()

        self.cache.preload_stocks(stocks)

        if not hasattr(self, '_fund_fetcher') or self._fund_fetcher is None:
            try:
                from fund_flow_fetcher import FundFlowFetcher
                self._fund_fetcher = FundFlowFetcher(cache=self.cache)
            except Exception:
                pass

        results = []
        done = 0
        t0 = __import__('time').time()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self.analyze_single_stock, code): code for code in stocks}
            for future in as_completed(futures):
                done += 1
                code = futures[future]
                try:
                    result = future.result(timeout=60)
                    if result:
                        results.append(result)
                        if done % 50 == 0 or done == len(stocks):
                            print(f"  [{done}/{len(stocks)}] 已分析，累计 {len(results)} 只有效 ({__import__('time').time()-t0:.0f}s)", flush=True)
                except Exception:
                    pass

        print(f"\n  分析完成: {len(results)}/{len(stocks)} 只有效 ({__import__('time').time()-t0:.0f}s)", flush=True)

        results.sort(key=lambda x: x['score'], reverse=True)
        top_stocks = results[:top_n]

        print()
        print("=" * 60)
        print(f"📈 增强版A推荐结果 (TOP {len(top_stocks)})")
        print("=" * 60)
        print()

        for i, stock in enumerate(top_stocks, 1):
            print(f"{i}. {stock['name']} ({stock['code']})")
            print(f"   评分: {stock['score']:.1f} ({stock['rating']})")
            print(f"   价格: ¥{stock['price']:.2f} ({stock['change_pct']:+.2f}%)")
            d = stock['details']
            fund = d.get('fundamental', {})
            print(f"   基本面: ROE={fund.get('roe',0):.1f}% | 利润增长={fund.get('profit_growth',0):+.1f}% | 股息率={fund.get('dividend_yield',0):.2f}%")
            qual = d.get('financial_quality', {})
            print(f"   财务质量: 毛利率={qual.get('gross_margin',0):.1f}% | 净利率={qual.get('net_margin',0):.1f}% | 负债率={qual.get('debt_to_asset',0):.1f}%")
            val = d.get('valuation', {})
            peg_str = f"PEG={val.get('peg','N/A')}" if val.get('peg') else 'PEG=N/A'
            print(f"   估值: {peg_str} | PB={val.get('pb',0):.2f} ({val.get('pb_level','')})")
            ff = d.get('fund_flow', {})
            print(f"   资金: 主力{ff.get('main_in',0):+.0f}万 | 超大单占比{ff.get('super_large_ratio',0):.2f} | 连续{ff.get('days_continuous',0)}日")
            lhb = d.get('lhb', {})
            if lhb.get('on_list'):
                print(f"   龙虎榜: {lhb.get('signal', '')}")
            ind = d.get('industry_strength', {})
            if ind.get('industry'):
                print(f"   行业: {ind['industry']} | 相对强弱 {ind.get('relative',0):+.1f}%")
            if stock.get('buy_signals'):
                print(f"   买入信号: {' | '.join(stock['buy_signals'][:5])}")
            sig = stock.get('signal', {})
            print(f"   综合信号: {sig.get('decision', 'N/A')} ({sig.get('buy_count', 0)}个买点)")
            print()

        return top_stocks

    def generate_report(self, stocks: List[Dict]) -> str:
        """生成增强版A推荐报告"""
        report = []
        report.append("=" * 60)
        report.append(f"📊 增强版A中长线选股报告")
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"评分体系(原始150→归一化100):")
        report.append(f"  趋势(20)+基本面(20)+财务质量(15)+估值(15)+动量(10)")
        report.append(f"  +量能(10)+DMI(10)+短线择时(15)+资金流(10)+龙虎榜(5)+行业强弱(10)")
        report.append("=" * 60)
        report.append("")

        for i, stock in enumerate(stocks, 1):
            report.append(f"{i}. {stock['name']} ({stock['code']})")
            report.append(f"   评级: {stock['rating']} | 评分: {stock['score']:.1f}/100")
            report.append(f"   价格: ¥{stock['price']:.2f} ({stock['change_pct']:+.2f}%)")
            report.append("")

            details = stock['details']

            # 技术面
            report.append("   📈 技术面:")
            trend = details.get('trend', {})
            report.append(f"      趋势: {trend.get('rating', 'N/A')} ({trend.get('score', 0):.1f}/20)")
            momentum = details.get('momentum', {})
            report.append(f"      动量: 20日{momentum.get('returns_20d', 0):+.2f}%")
            volume = details.get('volume', {})
            report.append(f"      量能: OBV {volume.get('obv_trend', 'N/A')}")
            dmi = details.get('dmi', {})
            report.append(f"      DMI: {dmi.get('strength', 'N/A')} (ADX={dmi.get('adx', 0):.1f})")
            timing = details.get('short_term_timing', {})
            report.append(f"      短线择时: {timing.get('score', 0):.1f}/15")
            report.append("")

            # 基本面+财务质量
            fund = details.get('fundamental', {})
            report.append("   📋 基本面:")
            report.append(f"      ROE: {fund.get('roe', 0):.1f}% | 利润增长: {fund.get('profit_growth', 0):+.1f}% | 股息率: {fund.get('dividend_yield', 0):.2f}%")
            qual = details.get('financial_quality', {})
            report.append(f"      财务质量({qual.get('level', 'N/A')}): 毛利率={qual.get('gross_margin', 0):.1f}% | 净利率={qual.get('net_margin', 0):.1f}% | 负债率={qual.get('debt_to_asset', 0):.1f}%")
            report.append("")

            # 估值
            val = details.get('valuation', {})
            report.append("   💎 估值:")
            peg_info = f"PEG={val.get('peg', 'N/A')}" if val.get('peg') else 'PEG=不适用'
            report.append(f"      {peg_info}({val.get('peg_level', '')}) | PB={val.get('pb', 0):.2f}({val.get('pb_level', '')})")
            report.append("")

            # 资金+龙虎榜+行业
            ff = details.get('fund_flow', {})
            report.append("   💰 资金面:")
            report.append(f"      主力: {ff.get('main_in', 0):+.0f}万 | 超大单占比: {ff.get('super_large_ratio', 0):.2f} | 连续{ff.get('days_continuous', 0)}日")
            lhb = details.get('lhb', {})
            if lhb.get('on_list'):
                report.append(f"      龙虎榜: {lhb.get('signal', '')}")
            ind = details.get('industry_strength', {})
            if ind.get('industry'):
                report.append(f"      行业({ind['industry']}): 个股{stock.get('change_pct',0):+.1f}% vs 行业{ind.get('industry_return',0):+.1f}% = 相对{ind.get('relative',0):+.1f}%")
            report.append("")

            # 买入信号
            if stock.get('buy_signals'):
                report.append("   ✅ 买入信号:")
                for sig in stock['buy_signals'][:6]:
                    report.append(f"      • {sig}")
                report.append("")

            # 综合信号
            sig = stock.get('signal', {})
            if sig:
                report.append(f"   📌 综合信号: {sig.get('decision', 'N/A')} ({sig.get('buy_count', 0)}个买点)")
            report.append("")
            report.append("-" * 60)
            report.append("")

        return "\n".join(report)

    def close(self):
        self.ds.close()
        self.cache.close()
        self.fundamental.close()


if __name__ == '__main__':
    selector = EnhancedLongTermSelectorA()
    top_stocks = selector.select_top_stocks(top_n=5)
    if top_stocks:
        report = selector.generate_report(top_stocks)
        print(report)
        with open('enhanced_a_recommendation.txt', 'w', encoding='utf-8') as f:
            f.write(report)
        print("✅ 报告已保存到 enhanced_a_recommendation.txt")
    selector.close()
