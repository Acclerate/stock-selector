#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版中长线选股引擎
集成基本面分析+高级指标
"""

import pandas as pd
import numpy as np
from datetime import datetime
import json
from typing import List, Dict
from smart_data_source import SmartDataSource
from stock_cache_db import StockCache
from advanced_indicators import AdvancedIndicators
from advanced_long_term_indicators import AdvancedLongTermIndicators
from fundamental_data import FundamentalData
from short_term_indicators import ShortTermIndicators


class EnhancedLongTermSelector:
    """增强版中长线选股引擎"""
    
    def __init__(self):
        self.ds = SmartDataSource()
        self.cache = StockCache()
        self.indicators = AdvancedIndicators()
        self.advanced_indicators = AdvancedLongTermIndicators()
        self.fundamental = FundamentalData(cache=self.cache)
        self._sti = ShortTermIndicators()
        
    def get_index_stocks(self) -> List[str]:
        """从沪深300+中证500成分股中获取扫描范围，过滤创业板和科创板"""
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
    
    def analyze_single_stock(self, code: str) -> Dict:
        """
        增强版单股分析
        包含技术面+基本面+高级指标
        """
        import time
        t0 = time.time()
        def _log(step: str):
            print(f"  [{code}] {step} ({time.time()-t0:.1f}s)", flush=True)

        try:
            # 获取历史数据
            _log("获取历史数据...")
            df = self.ds.get_history_data(code, days=120)
            if df is None or df.empty or len(df) < 60:
                _log("历史数据不足，跳过")
                return None
            
            # 获取基础信息
            _log("获取基础信息(cache)...")
            stock_info = self.cache.get_stock(code)
            if not stock_info:
                _log("无缓存信息，跳过")
                return None
            
            score = 0
            max_score = 130  # 扩展总分
            details = {}
            
            # ====== 1. 技术面评分 (30分) ======
            _log("技术面评分...")
            trend = self.indicators.score_trend(df)
            trend_score = trend['score'] * 0.30
            score += trend_score
            
            details['trend'] = {
                'score': trend_score,
                'rating': trend['rating'],
                'reasons': trend['reasons']
            }
            
            # ====== 2. 基本面评分 (30分) ✨新增 ======
            _log("基本面数据(akshare)...")
            fundamental_data = self.fundamental.get_stock_fundamental(code)
            fundamental_score = self._calc_fundamental_score(fundamental_data)
            score += fundamental_score['score']
            
            details['fundamental'] = fundamental_score
            
            # ====== 3. 估值评分 (15分) ✨新增 ======
            _log("估值评分...")
            valuation_score = self._calc_valuation_score(
                fundamental_data.get('pe', 0),
                fundamental_data.get('profit_growth', 0)
            )
            score += valuation_score['score']
            
            details['valuation'] = valuation_score
            
            # ====== 4. 动量评分 (15分, 连续评分) ======
            _log("动量+量价+DMI评分...")
            returns_20d = (df['close'].iloc[-1] - df['close'].iloc[-21]) / df['close'].iloc[-21] * 100
            # 连续评分: ±10%→±15分
            momentum_score = float(np.clip(returns_20d / 10, -1, 1) * 15)
            score += momentum_score

            details['momentum'] = {
                'score': round(momentum_score, 1),
                'returns_20d': returns_20d
            }
            
            # ====== 5. 量价评分 (15分, 连续评分) ======
            obv = self.indicators.calc_obv(df)
            obv_now = obv.iloc[-1]
            obv_20d = obv.iloc[-20]
            obv_change = (obv_now - obv_20d) / (abs(obv_20d) + 1)
            volume_score = float(np.clip(5 + obv_change * 100, -5, 15))
            score += volume_score

            details['volume'] = {
                'score': round(volume_score, 1),
                'obv_trend': 'up' if obv_now > obv_20d else 'down'
            }
            
            # ====== 6. DMI评分 (15分, 连续评分) ======
            plus_di, minus_di, adx = self.advanced_indicators.calc_dmi(df)
            dmi_analysis = self.advanced_indicators.analyze_dmi_signal(
                plus_di.iloc[-1], minus_di.iloc[-1], adx.iloc[-1]
            )
            # DI差值 + ADX强度 → 连续评分
            di_diff = (plus_di.iloc[-1] - minus_di.iloc[-1]) / 30
            adx_strength = min(adx.iloc[-1] / 30, 1.0)
            dmi_score = float(np.clip(di_diff * 10 * adx_strength, -8, 15))
            score += dmi_score

            details['dmi'] = {
                'score': round(dmi_score, 1),
                **dmi_analysis
            }
            
            # ====== 7. 资金流评分 (10分, 连续评分) ======
            _log("资金流评分...")
            try:
                from fund_flow_fetcher import FundFlowFetcher
                if not hasattr(self, '_fund_fetcher'):
                    self._fund_fetcher = FundFlowFetcher(cache=self.cache)
                fund_flow = self._fund_fetcher.fetch_and_save(code)
            except Exception:
                fund_flow = self.cache.get_fund_flow(code)

            if fund_flow:
                main_in = fund_flow.get('main_in', 0)
                fund_score = float(np.clip(main_in / 100000000 * 10, -5, 10))
            else:
                fund_score = 0.0
            score += fund_score

            details['fund_flow'] = {
                'score': round(fund_score, 1),
                'main_in': fund_flow.get('main_in', 0) / 10000 if fund_flow else 0
            }

            # ====== 补充技术指标（供 AI 分析使用） ======
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

            # ====== 综合信号评分 ✨新增 ======
            _log("综合信号评分...")
            signals = {
                'trend': trend,
                'momentum': {'signal': 'buy' if returns_20d > 0 else 'sell'},
                'volume': {'signal': 'buy' if obv.iloc[-1] > obv.iloc[-20] else 'sell'},
                'dmi': dmi_analysis,
                'valuation': {'signal': 'buy' if valuation_score['level'] in ['低估', '合理'] else 'sell'}
            }
            
            optimized_signal = self.advanced_indicators.optimize_signal_trigger(signals)
            
            # ====== 汇总结果 ======
            final_score = (score / max_score) * 100  # 归一化到100分
            
            # 生成买入信号列表
            buy_signals = []
            if optimized_signal['decision'] in ['强烈买入', '买入']:
                for reason in optimized_signal['reasons']:
                    buy_signals.append(reason)
            
            # 添加基本面信号
            if fundamental_score['score'] >= 24:
                buy_signals.append(f"基本面优秀(ROE {fundamental_data.get('roe', 0):.1f}%)")
            if valuation_score['level'] == '低估':
                buy_signals.append(f"PEG低估({valuation_score['peg']:.2f})")
            if fundamental_data.get('dividend_yield', 0) >= 3:
                buy_signals.append(f"高股息({fundamental_data.get('dividend_yield', 0):.1f}%)")
            
            # DMI信号
            if dmi_analysis['signal'] in ['buy', 'strong_buy']:
                buy_signals.append(f"DMI多头({dmi_analysis['strength']})")
            
            # 计算买卖点（中长线：-8%止损，+20%止盈）
            current_price = float(stock_info.get('price', 0))
            buy_price = current_price
            stop_loss = current_price * 0.92  # -8%
            take_profit = current_price * 1.20  # +20%
            stop_loss_pct = -8.0
            take_profit_pct = 20.0
            risk_reward_ratio = 20.0 / 8.0  # 2.5:1
            
            result = {
                'code': code,
                'name': stock_info.get('name', 'Unknown'),
                'price': float(stock_info.get('price', 0)),
                'change_pct': float(stock_info.get('change_pct', 0)),
                'score': round(final_score, 2),
                'rating': self._get_rating(final_score),
                'details': self._convert_to_json_safe(details),
                'signal': optimized_signal,
                'recommend': final_score >= 55,
                'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                # 新增字段
                'buy_signals': buy_signals,
                'buy_signal_count': len(buy_signals),
                'buy_price': buy_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'stop_loss_pct': stop_loss_pct,
                'take_profit_pct': take_profit_pct,
                'risk_reward_ratio': risk_reward_ratio
            }
            
            return result
            
        except Exception as e:
            print(f"分析{code}失败({time.time()-t0:.1f}s): {e}", flush=True)
            import traceback
            traceback.print_exc()
            return None
    
    def _calc_fundamental_score(self, data: Dict) -> Dict:
        """计算基本面评分 (30分, 连续评分)"""
        # ROE (10分): 连续, ROE=20%→10分
        roe = data.get('roe', 0) or 0
        roe_score = float(np.clip(roe / 20, 0, 1) * 10)

        # 利润增长 (10分): 连续, 增长25%→10分
        profit_growth = data.get('profit_growth', 0) or 0
        growth_score = float(np.clip(profit_growth / 25, 0, 1) * 10)

        # 股息率 (10分): 连续, 股息4%→10分
        dividend = data.get('dividend_yield', 0) or 0
        dividend_score = float(np.clip(dividend / 4, 0, 1) * 10)

        score = roe_score + growth_score + dividend_score
        level = 'A' if score >= 24 else 'B' if score >= 18 else 'C' if score >= 12 else 'D'

        return {
            'score': round(score, 1),
            'level': level,
            'roe': roe,
            'profit_growth': profit_growth,
            'dividend_yield': dividend,
            'revenue_growth': data.get('revenue_growth', 0)
        }
    
    def _calc_valuation_score(self, pe: float, growth: float) -> Dict:
        """计算估值评分 (15分, 连续评分)"""
        if pe <= 0 or growth <= 0:
            return {
                'score': 0,
                'level': '无效',
                'pe': pe,
                'peg': None
            }

        peg_data = self.advanced_indicators.calc_peg_ratio(pe, growth)
        peg = peg_data['peg']

        if peg:
            # 连续评分: PEG=0.5→15分, PEG=2.0→0分
            score = float(np.clip((2.0 - peg) / 1.5, 0, 1) * 15)
            if peg < 0.8: level = '低估'
            elif peg < 1.2: level = '合理'
            elif peg < 2.0: level = '偏高'
            else: level = '高估'
        else:
            score = 0.0
            level = '无效'

        return {
            'score': round(score, 1),
            'level': level,
            'pe': pe,
            'peg': peg,
            'growth': growth
        }
    
    def _get_rating(self, score: float) -> str:
        """评级"""
        if score >= 80:
            return 'A+'
        elif score >= 70:
            return 'A'
        elif score >= 60:
            return 'B+'
        elif score >= 50:
            return 'B'
        elif score >= 40:
            return 'C'
        else:
            return 'D'
    
    def _convert_to_json_safe(self, obj):
        """转换为JSON安全的数据类型"""
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
        elif isinstance(obj, bool):
            return bool(obj)
        elif obj is None:
            return None
        else:
            return obj
    
    def select_top_stocks(self, top_n: int = 5) -> List[Dict]:
        """选择TOP N股票"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        print("=" * 60)
        print(f"🎯 增强版中长线选股 - TOP {top_n}")
        print("=" * 60)
        print()

        stocks = self.get_index_stocks()
        if not stocks:
            print("❌ 获取指数成分股失败")
            return []

        # 限制掘金名称缓存只加载当前扫描范围的股票
        try:
            from diggold_source import DiggoldSource
            DiggoldSource.set_filter_codes(stocks)
        except Exception:
            pass

        print(f"📊 并行分析 {len(stocks)} 只股票（含基本面分析）...", flush=True)
        print()

        self.cache.preload_stocks(stocks)

        if not hasattr(self, '_fund_fetcher'):
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
                    result = future.result(timeout=30)
                    if result:
                        results.append(result)
                        if done % 50 == 0 or done == len(stocks):
                            print(f"  [{done}/{len(stocks)}] 已分析，累计 {len(results)} 只有效 ({__import__('time').time()-t0:.0f}s)", flush=True)
                except Exception:
                    pass

        print(f"\n  分析完成: {len(results)}/{len(stocks)} 只有效 ({__import__('time').time()-t0:.0f}s)", flush=True)
        
        # 按评分排序
        results.sort(key=lambda x: x['score'], reverse=True)
        
        # 取TOP N
        top_stocks = results[:top_n]
        
        print()
        print("=" * 60)
        print(f"📈 推荐结果 (TOP {len(top_stocks)})")
        print("=" * 60)
        print()
        
        for i, stock in enumerate(top_stocks, 1):
            print(f"{i}. {stock['name']} ({stock['code']})")
            print(f"   评分: {stock['score']:.1f} ({stock['rating']})")
            print(f"   价格: ¥{stock['price']:.2f} ({stock['change_pct']:+.2f}%)")
            
            # 基本面
            fund = stock['details']['fundamental']
            print(f"   基本面: ROE={fund['roe']:.1f}% | 利润增长={fund['profit_growth']:+.1f}% | 股息率={fund['dividend_yield']:.2f}%")
            
            # 估值
            val = stock['details']['valuation']
            if val['peg']:
                print(f"   估值: PE={val['pe']:.1f} | PEG={val['peg']:.2f} ({val['level']})")
            
            # 信号
            sig = stock['signal']
            print(f"   信号: {sig['decision']} ({sig['buy_count']}个买点)")
            print()
        
        return top_stocks
    
    def generate_report(self, stocks: List[Dict]) -> str:
        """生成增强版推荐报告"""
        report = []
        report.append("=" * 60)
        report.append(f"📊 增强版中长线选股报告")
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"评分体系: 趋势(30)+动量(15)+量能(15)+ADX(10)+波动(10)+乖离(10)+资金(10)+基本面(30)+估值(15)+DMI(15)")
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
            report.append(f"      趋势: {trend.get('rating', 'N/A')} ({trend.get('score', 0):.1f}/30)")
            momentum = details.get('momentum', {})
            report.append(f"      动量: 5日{momentum.get('returns_5d', 0):+.2f}% | 20日{momentum.get('returns_20d', 0):+.2f}%")
            volume = details.get('volume', {})
            report.append(f"      量能: {volume.get('obv_trend', 'N/A')} | 量比{volume.get('volume_ratio', 0):.2f}")
            strength = details.get('strength', {})
            report.append(f"      强度: ADX={strength.get('adx', 0):.1f}")
            report.append("")

            # 基本面
            fund = details.get('fundamental', {})
            report.append("   📋 基本面:")
            report.append(f"      ROE: {fund.get('roe', 0):.1f}% | 利润增长: {fund.get('profit_growth', 0):+.1f}% | 股息率: {fund.get('dividend_yield', 0):.2f}%")
            report.append("")

            # 估值
            val = details.get('valuation', {})
            report.append("   💎 估值:")
            if val.get('peg'):
                report.append(f"      PE={val.get('pe', 0):.1f} | PEG={val.get('peg', 0):.2f} ({val.get('level', 'N/A')})")
            else:
                report.append(f"      PE={val.get('pe', 0):.1f} | PEG=不适用")
            report.append("")

            # 资金面
            fund_flow = details.get('fund_flow', {})
            report.append("   💰 资金面:")
            report.append(f"      主力: {fund_flow.get('main_in', 0):+.0f}万")
            report.append("")

            # 推荐理由
            report.append("   ✅ 推荐理由:")
            for reason in trend.get('reasons', [])[:3]:
                report.append(f"      • {reason}")

            # 信号
            sig = stock.get('signal', {})
            if sig:
                report.append(f"   📌 信号: {sig.get('decision', 'N/A')} ({sig.get('buy_count', 0)}个买点)")
            report.append("")
            report.append("-" * 60)
            report.append("")

        return "\n".join(report)

    def close(self):
        self.ds.close()
        self.cache.close()
        self.fundamental.close()


if __name__ == '__main__':
    selector = EnhancedLongTermSelector()
    
    # 选择TOP 5
    top_stocks = selector.select_top_stocks(top_n=5)
    
    selector.close()
