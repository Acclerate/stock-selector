#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
短线选股引擎 (优化版)
每日推荐3-5只短线机会股
排除创业板(3开头)和科创板(688开头)

优化内容：
1. 新增MACD、布林带指标评分
2. 动态止损止盈（基于ATR）
3. 精确买卖点输出
4. 多指标共振确认

评分体系 (满分100分):
- RSI信号: 20分
- KDJ信号: 20分
- MACD信号: 15分
- 布林带信号: 15分
- 量价异动: 15分
- 资金流向: 15分
"""

import pandas as pd
import numpy as np
from datetime import datetime
import json
from typing import List, Dict
from smart_data_source import SmartDataSource
from stock_cache_db import StockCache
from short_term_indicators import ShortTermIndicators


class ShortTermSelector:
    """短线选股引擎"""
    
    def __init__(self):
        self.ds = SmartDataSource()
        self.cache = StockCache()
        self.indicators = ShortTermIndicators()
        
    def get_index_stocks(self) -> List[str]:
        """从沪深300+中证500成分股中获取扫描范围，过滤创业板和科创板"""
        import akshare as ak
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'data'))
        try:
            from config import SCAN_INDICES
        except Exception:
            SCAN_INDICES = ["000300", "000905"]

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
            idx_name = {"000300": "沪深300", "000905": "中证500"}.get(idx, idx)
            # 方案1: 东方财富
            try:
                print(f"获取{idx_name}({idx})成分股（东方财富）...", flush=True)
                df = ak.index_stock_cons(symbol=idx)
                codes = df['品种代码'].astype(str).str.zfill(6).tolist()
                all_codes.extend(codes)
                print(f"  ✅ {idx_name}: {len(codes)} 只", flush=True)
                continue
            except Exception as e:
                print(f"  ⚠️ 东方财富失败: {e}", flush=True)
            # 方案2: 中证官网
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
        短线分析单只股票 (优化版)
        评分满分100分，新增MACD/布林带/动态止损止盈

        评分体系:
        - RSI信号: 20分
        - KDJ信号: 20分
        - MACD信号: 15分
        - 布林带信号: 15分
        - 量价异动: 15分
        - 资金流向: 15分
        """
        import time
        t0 = time.time()
        def _log(step: str):
            print(f"  [{code}] {step} ({time.time()-t0:.1f}s)", flush=True)

        try:
            # 获取历史数据（短线只需30天）
            _log("获取历史数据...")
            df = self.ds.get_history_data(code, days=30)
            if df is None or df.empty or len(df) < 10:
                _log("历史数据不足，跳过")
                return None

            # 获取基础信息
            _log("获取基础信息(cache)...")
            stock_info = self.cache.get_stock(code)
            if not stock_info:
                _log("无缓存信息，跳过")
                return None

            score = 0
            details = {}
            signals = []
            buy_signals = []  # 买入信号列表
            sell_signals = []  # 卖出信号列表

            current_price = float(stock_info.get('price', df['close'].iloc[-1]))

            # ====== 1. RSI超卖反弹 (20分, 连续评分) ======
            _log("RSI+KDJ+MACD+布林带评分...")
            rsi = self.indicators.calc_rsi(df)
            rsi_now = rsi.iloc[-1]

            # 连续评分: RSI=30→20分, RSI=50→10分, RSI=70→0分, RSI>80→负分
            rsi_score = float(np.clip((70 - rsi_now) / 40, -0.25, 1.0) * 20)
            rsi_signal = None
            if rsi_now < 30:
                rsi_signal = f'RSI超卖 ({rsi_now:.0f})'
                buy_signals.append(rsi_signal)
                signals.append('RSI超卖')
            elif rsi_now > 70:
                rsi_signal = f'RSI超买 ({rsi_now:.0f})'
                sell_signals.append(rsi_signal)

            score += rsi_score
            details['rsi'] = {
                'score': round(rsi_score, 1),
                'value': rsi_now,
                'signal': rsi_signal
            }

            # ====== 2. KDJ金叉 (20分, 连续评分) ======
            k, d, j = self.indicators.calc_kdj(df)
            kdj_result = self.indicators.detect_kdj_cross(k, d, j)
            j_cur = kdj_result['j']

            # 基础分: J=20→15分, J=80→-5分
            j_score = float(np.clip((80 - j_cur) / 60, -0.33, 1.0) * 15)
            # 金叉/死叉调整
            cross_adj = 5 if kdj_result['golden_cross'] else (-5 if kdj_result['dead_cross'] else 0)
            kdj_score = float(np.clip(j_score + cross_adj, -10, 20))

            if kdj_result['golden_cross']:
                buy_signals.append(f"KDJ金叉 (K={kdj_result['k']:.0f}, J={j_cur:.0f})")
            elif kdj_result['oversold']:
                buy_signals.append(f"KDJ超卖 (J={j_cur:.0f})")
            if kdj_result['dead_cross'] and j_cur > 70:
                sell_signals.append(f"KDJ死叉 (K={kdj_result['k']:.0f}, J={j_cur:.0f})")
            elif kdj_result['overbought']:
                sell_signals.append(f"KDJ超买 (J={j_cur:.0f})")
            kdj_signal = kdj_result['signals'][0] if kdj_result['signals'] else ''
            if kdj_signal:
                signals.append(kdj_signal)

            score += kdj_score
            details['kdj'] = {
                'score': round(kdj_score, 1),
                'k': kdj_result['k'],
                'd': kdj_result['d'],
                'j': j_cur,
                'signal': kdj_signal,
                'golden_cross': kdj_result['golden_cross'],
                'death_cross': kdj_result['dead_cross']
            }

            # ====== 3. MACD信号 (15分, 连续评分) ======
            dif, dea, macd_hist = self.indicators.calc_macd_short(df)
            macd_result = self.indicators.detect_macd_cross(dif, dea, macd_hist)
            hist_cur = macd_result['histogram']
            hist_prev = float(macd_hist.iloc[-2]) if len(macd_hist) > 1 else hist_cur

            # 连续评分: 柱状图方向和动量
            if current_price > 0:
                scale = current_price * 0.005
                hist_norm = hist_cur / scale if scale else 0
                hist_delta = (hist_cur - hist_prev) / scale if scale else 0
                macd_score = float(np.clip(hist_norm * 2 + hist_delta * 3, -10, 15))
            else:
                macd_score = 0.0

            macd_signal = macd_result['signals'][0] if macd_result['signals'] else ''
            if macd_result['golden_cross']:
                macd_score = max(macd_score, 12.0)
                buy_signals.append(f"MACD金叉 (DIF={macd_result['dif']:.3f})")
            elif hist_cur < 0 and macd_result['dif'] < macd_result['dea']:
                macd_score = min(macd_score, -3.0)
                sell_signals.append(f"MACD空头 (DIF={macd_result['dif']:.3f})")
            elif hist_cur > 0 and hist_cur > hist_prev:
                buy_signals.append("MACD红柱扩张")

            score += macd_score
            if macd_signal:
                signals.append(macd_signal)

            details['macd'] = {
                'score': round(macd_score, 1),
                'dif': macd_result['dif'],
                'dea': macd_result['dea'],
                'macd_hist': hist_cur,
                'signal': macd_signal,
                'golden_cross': macd_result['golden_cross'],
                'death_cross': hist_cur < 0 and macd_result['dif'] < macd_result['dea']
            }

            # ====== 4. 布林带信号 (15分, 连续评分) ======
            upper, middle, lower = self.indicators.calc_bollinger(df)
            boll_result = self.indicators.detect_bollinger_signal(df, upper, middle, lower)
            boll_position = boll_result['price_position']  # 0~1 (lower~upper)

            # 连续评分: 下轨(0%)→15分, 中轨(50%)→5分, 上轨(100%)→-5分
            boll_score = float(np.clip((1 - boll_position) * 15 - boll_position * 5, -5, 15))
            boll_position_pct = boll_position * 100

            boll_signal = boll_result['signals'][0] if boll_result['signals'] else ''
            if boll_position < 0.1:
                buy_signals.append(f"布林下轨支撑 (位置{boll_position_pct:.0f}%)")
            elif boll_position < 0.4:
                buy_signals.append("布林中轨支撑")
            elif boll_position > 0.9:
                sell_signals.append("布林触及上轨")

            score += boll_score
            if boll_signal:
                signals.append(boll_signal)

            details['bollinger'] = {
                'score': round(boll_score, 1),
                'upper': boll_result['upper'],
                'middle': boll_result['middle'],
                'lower': boll_result['lower'],
                'bandwidth': boll_result['bandwidth'],
                'position_pct': boll_position_pct,
                'signal': boll_signal
            }

            # ====== 5. 量价异动 (15分, 连续评分) ======
            volume_surge = self.indicators.detect_volume_surge(df, ratio=1.5)
            vol_ratio = volume_surge['volume_ratio']
            price_up = volume_surge['price_up']

            # 连续评分: 量比与价格方向组合
            if price_up:
                volume_score = float(np.clip((vol_ratio - 0.5) / 1.5 * 15, 0, 15))
            else:
                volume_score = float(np.clip(-(vol_ratio - 1.0) / 1.0 * 10, -10, 5))

            vol_signal = volume_surge['signals'][0] if volume_surge['signals'] else ''
            if vol_ratio > 1.5 and price_up:
                buy_signals.append(f"放量突破 (量比{vol_ratio:.1f})")
            elif vol_ratio > 1.5 and not price_up:
                sell_signals.append(f"放量下跌 (量比{vol_ratio:.1f})")

            score += volume_score
            if vol_signal:
                signals.append(vol_signal)

            details['volume'] = {
                'score': round(volume_score, 1),
                'volume_ratio': vol_ratio,
                'price_change': float(price_up),
                'surge_type': vol_signal
            }

            # ====== 6. 资金流向 (15分, 连续评分) ======
            _log("资金流评分...")
            try:
                from fund_flow_fetcher import FundFlowFetcher
                if not hasattr(self, '_fund_fetcher'):
                    self._fund_fetcher = FundFlowFetcher(cache=self.cache)
                fund_flow = self._fund_fetcher.fetch_and_save(code)
            except Exception:
                fund_flow = self.cache.get_fund_flow(code)

            fund_score = 0.0
            fund_signal = None
            main_in_wan = 0

            if fund_flow:
                main_in = fund_flow.get('main_in', 0)
                main_in_wan = main_in / 10000

                # 连续评分: ±500万为满分/负分边界
                fund_score = float(np.clip(main_in / 5000000 * 15, -5, 15))
                if main_in > 5000000:
                    fund_signal = f'主力流入 (+{main_in_wan:.0f}万)'
                    buy_signals.append(fund_signal)
                elif main_in < -5000000:
                    fund_signal = f'主力流出 ({main_in_wan:.0f}万)'
                    sell_signals.append(fund_signal)

                if fund_signal and fund_signal not in signals:
                    signals.append(fund_signal.split(' ')[0])

            score += fund_score
            details['fund_flow'] = {
                'score': round(fund_score, 1),
                'main_in': main_in_wan,
                'signal': fund_signal
            }

            # ====== 7. ATR动态止损止盈 ======
            _log("ATR止损止盈计算...")
            atr = self.indicators.calc_atr_short(df)
            atr_now = atr.iloc[-1]

            trade_points = self.indicators.calc_trade_points(
                current_price, atr_now,
                stop_multiplier=2.0,
                profit_multiplier=3.0
            )
            trade_points['atr'] = round(float(atr_now), 4)
            trade_points['atr_pct'] = round(float(atr_now / current_price * 100) if current_price else 0, 2)

            details['trade_points'] = trade_points

            # ====== 8. 计算共振信号数 ======
            buy_signal_count = len(buy_signals)
            sell_signal_count = len(sell_signals)

            # ====== 汇总结果 ======
            result = {
                'code': code,
                'name': stock_info.get('name', 'Unknown'),
                'price': current_price,
                'change_pct': float(stock_info.get('change_pct', 0)),
                'score': round(float(score), 2),
                'rating': self._get_rating(score),
                'signals': signals,
                'buy_signals': buy_signals,
                'sell_signals': sell_signals,
                'buy_signal_count': buy_signal_count,
                'sell_signal_count': sell_signal_count,
                'details': self._convert_to_json_safe(details),
                # 买卖点
                'buy_price': trade_points['buy_price'],
                'stop_loss': trade_points['stop_loss'],
                'take_profit': trade_points['take_profit'],
                'stop_loss_pct': trade_points['stop_loss_pct'],
                'take_profit_pct': trade_points['take_profit_pct'],
                'atr': trade_points['atr'],
                'atr_pct': trade_points['atr_pct'],
                'risk_reward_ratio': trade_points['risk_reward_ratio'],
                'recommend': bool(score >= 50 and buy_signal_count >= 2),
                'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

            return result

        except Exception as e:
            import traceback
            print(f"分析{code}失败({time.time()-t0:.1f}s): {e}", flush=True)
            traceback.print_exc()
            return None
    
    def _get_rating(self, score: float) -> str:
        """
        评级
        A+/A: 强烈推荐 (≥70分)
        B+/B: 可操作 (≥50分)
        C: 观望 (<50分)
        """
        if score >= 85:
            return 'A+'
        elif score >= 70:
            return 'A'
        elif score >= 60:
            return 'B+'
        elif score >= 50:
            return 'B'
        else:
            return 'C'
    
    def _convert_to_json_safe(self, obj):
        """
        转换为JSON安全的数据类型
        处理numpy/pandas类型、布尔值和NaN
        """
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
            # 处理NaN和Infinity
            if math.isnan(val) or math.isinf(val):
                return None
            return val
        elif isinstance(obj, float):
            # 处理原生float的NaN
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
        """
        短线选股TOP N
        返回推荐列表
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        print("=" * 60)
        print(f"⚡ 短线选股 - TOP {top_n}")
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

        print(f"📊 并行分析 {len(stocks)} 只股票...", flush=True)
        print()

        self.cache.preload_stocks(stocks)

        # 预初始化懒加载资源，避免并发竞态
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
        print(f"⚡ 短线推荐 (TOP {len(top_stocks)})")
        print("=" * 60)
        print()

        for i, stock in enumerate(top_stocks, 1):
            print(f"【{stock['code']} {stock['name']}】评分: {stock['score']:.0f}分 ({stock['rating']})")
            print(f"现价: ¥{stock['price']:.2f} ({stock['change_pct']:+.2f}%)")
            print()

            # 买入信号
            if stock['buy_signals']:
                print("📈 买入信号:")
                for sig in stock['buy_signals'][:4]:
                    print(f"  ✓ {sig}")
                print()

            # 卖出信号（如果有）
            if stock['sell_signals']:
                print("📉 卖出信号:")
                for sig in stock['sell_signals'][:2]:
                    print(f"  ✗ {sig}")
                print()

            # 操作建议
            print("💰 操作建议:")
            print(f"  买点: ¥{stock['buy_price']:.2f} (当前价即可)")
            print(f"  止损: ¥{stock['stop_loss']:.2f} ({stock['stop_loss_pct']:.1f}%, 基于ATR)")
            print(f"  止盈: ¥{stock['take_profit']:.2f} (+{stock['take_profit_pct']:.1f}%, 基于ATR)")
            print(f"  盈亏比: {stock['risk_reward_ratio']:.1f}:1")
            print(f"  预期持仓: 1-3天")
            print()
            print("-" * 60)
            print()

        return top_stocks
    
    def generate_report(self, stocks: List[Dict]) -> str:
        """生成短线推荐报告 (优化版)"""
        report = []
        report.append("=" * 60)
        report.append(f"⚡ 短线选股报告 (优化版)")
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"持仓建议: 1-3天")
        report.append(f"评分体系: RSI(20)+KDJ(20)+MACD(15)+布林(15)+量价(15)+资金(15)")
        report.append("=" * 60)
        report.append("")

        for i, stock in enumerate(stocks, 1):
            report.append(f"【{stock['code']} {stock['name']}】评分: {stock['score']:.0f}分 ({stock['rating']})")
            report.append(f"现价: ¥{stock['price']:.2f} ({stock['change_pct']:+.2f}%)")
            report.append("")

            # 买入信号
            if stock.get('buy_signals'):
                report.append("📈 买入信号:")
                for sig in stock['buy_signals'][:5]:
                    report.append(f"   ✓ {sig}")
                report.append("")

            # 卖出信号
            if stock.get('sell_signals'):
                report.append("📉 卖出信号:")
                for sig in stock['sell_signals'][:3]:
                    report.append(f"   ✗ {sig}")
                report.append("")

            # 技术指标详情
            details = stock['details']
            report.append("📊 技术指标:")
            rsi_val = details.get('rsi', {}).get('value', 0)
            report.append(f"   RSI: {rsi_val:.1f}")

            kdj = details.get('kdj', {})
            report.append(f"   KDJ: K={kdj.get('k', 0):.1f}, D={kdj.get('d', 0):.1f}, J={kdj.get('j', 0):.1f}")

            macd = details.get('macd', {})
            if macd:
                report.append(f"   MACD: DIF={macd.get('dif', 0):.4f}, DEA={macd.get('dea', 0):.4f}")

            boll = details.get('bollinger', {})
            if boll:
                report.append(f"   布林: 位置{boll.get('position_pct', 50):.0f}%, 带宽{boll.get('bandwidth', 0):.1f}%")

            volume = details.get('volume', {})
            report.append(f"   量比: {volume.get('volume_ratio', 0):.2f}")
            report.append("")

            # 资金流向
            fund = details.get('fund_flow', {})
            if fund.get('signal'):
                report.append("💵 资金面:")
                report.append(f"   {fund.get('signal')}")
                report.append("")

            # 操作建议（核心）
            report.append("💰 操作建议:")
            report.append(f"   买点: ¥{stock.get('buy_price', stock['price']):.2f} (当前价即可)")
            report.append(f"   止损: ¥{stock.get('stop_loss', 0):.2f} ({stock.get('stop_loss_pct', -3):.1f}%, 基于ATR)")
            report.append(f"   止盈: ¥{stock.get('take_profit', 0):.2f} (+{stock.get('take_profit_pct', 5):.1f}%, 基于ATR)")
            report.append(f"   盈亏比: {stock.get('risk_reward_ratio', 1.5):.1f}:1")
            report.append(f"   预期持仓: 1-3天")
            report.append("")

            # 评级建议
            if stock['score'] >= 85:
                report.append("   ★★★ 强烈推荐: 多指标共振，机会较好")
            elif stock['score'] >= 70:
                report.append("   ★★☆ 推荐: 有一定机会，可适量参与")
            elif stock['score'] >= 60:
                report.append("   ★☆☆ 关注: 信号一般，轻仓试探")
            else:
                report.append("   ☆☆☆ 观望: 暂不建议操作")

            report.append("")
            report.append("-" * 60)
            report.append("")

        report.append("⚠️ 风险提示:")
        report.append("   • 短线交易风险较高，建议控制仓位")
        report.append("   • 严格执行动态止损止盈")
        report.append("   • 多指标共振确认，减少假信号")
        report.append("   • 不追涨杀跌，理性交易")
        report.append("")

        return "\n".join(report)
    
    def close(self):
        self.ds.close()
        self.cache.close()


if __name__ == '__main__':
    selector = ShortTermSelector()
    
    # 选择TOP 5
    top_stocks = selector.select_top_stocks(top_n=5)
    
    # 生成报告
    if top_stocks:
        report = selector.generate_report(top_stocks)
        print(report)
        
        # 保存到文件
        with open('short_term_recommendation.txt', 'w', encoding='utf-8') as f:
            f.write(report)
        
        print("✅ 报告已保存到 short_term_recommendation.txt")
    
    selector.close()
