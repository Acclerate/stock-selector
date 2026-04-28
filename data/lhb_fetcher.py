#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
龙虎榜数据获取模块
数据源：akshare + 东方财富
功能：获取每日龙虎榜详情、机构席位、游资动向
"""

import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import sys
import os

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stock_cache_db import StockCache


class LHBFetcher:
    """龙虎榜数据获取器"""
    
    def __init__(self):
        self.cache = StockCache()
    
    def get_daily_lhb(self, date: str = None) -> pd.DataFrame:
        """
        获取指定日期的龙虎榜详情
        
        Args:
            date: 日期字符串，格式 YYYYMMDD，默认今天
        
        Returns:
            DataFrame 包含龙虎榜详细数据
        """
        if date is None:
            date = datetime.now().strftime('%Y%m%d')
        
        try:
            # 获取龙虎榜详情
            df = ak.stock_lhb_detail_em(start_date=date, end_date=date)
            
            if df is not None and not df.empty:
                print(f"✅ 获取到 {len(df)} 条龙虎榜数据 ({date})")
                return df
            else:
                print(f"⚠️  {date} 无龙虎榜数据 (可能非交易日)")
                return pd.DataFrame()
                
        except Exception as e:
            print(f"❌ 获取龙虎榜数据失败：{e}")
            return pd.DataFrame()
    
    def get_lhb_by_stock(self, code: str, days: int = 30) -> pd.DataFrame:
        """
        获取单只股票的历史龙虎榜数据
        
        Args:
            code: 股票代码
            days: 回溯天数
        
        Returns:
            DataFrame 包含该股票的历史龙虎榜记录
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        try:
            df = ak.stock_lhb_detail_em(
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d')
            )
            
            if df is not None and not df.empty:
                # 筛选特定股票
                stock_lhb = df[df['代码'] == code]
                return stock_lhb
            else:
                return pd.DataFrame()
                
        except Exception as e:
            print(f"❌ 获取股票 {code} 龙虎榜失败：{e}")
            return pd.DataFrame()
    
    def parse_lhb_for_stock(self, df: pd.DataFrame, code: str) -> Optional[Dict]:
        """
        解析龙虎榜数据，提取单只股票的关键信息
        
        Args:
            df: 龙虎榜 DataFrame
            code: 股票代码
        
        Returns:
            包含买卖金额的字典
        """
        if df is None or df.empty:
            return None
        
        stock_data = df[df['代码'] == code]
        if stock_data.empty:
            return None
        
        # 计算买卖总额
        buy_total = stock_data['买入总额'].sum() if '买入总额' in stock_data.columns else 0
        sell_total = stock_data['卖出总额'].sum() if '卖出总额' in stock_data.columns else 0
        net_amount = buy_total - sell_total
        
        return {
            'code': code,
            'buy_amount': buy_total,
            'sell_amount': sell_total,
            'net_amount': net_amount,
            'update_time': datetime.now()
        }
    
    def save_lhb_to_cache(self, date: str = None):
        """
        获取并保存龙虎榜数据到缓存，优先当天，无数据则回溯最近5天。
        同时回填已有条目的空名称。
        """
        # 拉取最近几天的龙虎榜数据，构建 code->name 映射
        all_names = {}
        all_dfs = []
        for days_back in range(0, 6):
            d = (datetime.now() - timedelta(days=days_back)).strftime('%Y%m%d')
            df = self.get_daily_lhb(d)
            if not df.empty:
                all_dfs.append(df)
                if '代码' in df.columns and '名称' in df.columns:
                    for _, row in df.iterrows():
                        code = row['代码']
                        name = row['名称']
                        if code and name and code not in all_names:
                            all_names[code] = name
        if not all_dfs:
            return

        # 用最新一天的数据更新 lhb 缓存
        df = all_dfs[0]
        if '代码' in df.columns:
            buy_col = '龙虎榜买入额' if '龙虎榜买入额' in df.columns else '买入总额'
            sell_col = '龙虎榜卖出额' if '龙虎榜卖出额' in df.columns else '卖出总额'
            net_col = '龙虎榜净买额' if '龙虎榜净买额' in df.columns else '净买额'
            name_col = '名称' if '名称' in df.columns else None
            agg_dict = {buy_col: 'sum', sell_col: 'sum', net_col: 'sum'}
            if name_col:
                agg_dict[name_col] = 'first'
            grouped = df.groupby('代码').agg(agg_dict).reset_index()

            for _, row in grouped.iterrows():
                code = row['代码']
                data = {
                    'buy_amount': row[buy_col],
                    'sell_amount': row[sell_col],
                    'net_amount': row[net_col],
                    'name': row.get(name_col, '') if name_col else '',
                }
                self.cache.save_lhb(code, data)

            print(f"已保存 {len(grouped)} 只股票的龙虎榜数据到缓存")

        # 回填所有 lhb 表中 name 为空的条目
        cursor = self.cache.conn.cursor()
        cursor.execute("SELECT code FROM lhb WHERE name IS NULL OR name=''")
        empty_codes = [r[0] for r in cursor.fetchall()]
        if not empty_codes:
            return
        # 用已收集的龙虎榜名称回填
        filled = 0
        for code in empty_codes:
            name = all_names.get(code, '')
            if name:
                cursor.execute("UPDATE lhb SET name=? WHERE code=?", (name, code))
                filled += 1
        if filled:
            self.cache.conn.commit()
            print(f"已回填 {filled} 只股票的名称")
        # 剩余空名称：从 stocks 表或实时接口补充
        cursor.execute("SELECT code FROM lhb WHERE name IS NULL OR name=''")
        still_empty = [r[0] for r in cursor.fetchall()]
        if still_empty:
            name_map = self._batch_lookup_names(still_empty)
            if name_map:
                for code, name in name_map.items():
                    cursor.execute("UPDATE lhb SET name=? WHERE code=?", (name, code))
                self.cache.conn.commit()
                print(f"实时补充 {len(name_map)} 只股票的名称")

    def _batch_lookup_names(self, codes: List[str]) -> Dict[str, str]:
        """批量查询股票名称，优先 stocks 表，其次东方财富实时接口"""
        result = {}
        cursor = self.cache.conn.cursor()
        remaining = []
        for code in codes:
            cursor.execute("SELECT name FROM stocks WHERE code=? AND name IS NOT NULL AND name!='' LIMIT 1", (code,))
            r = cursor.fetchone()
            if r and r[0]:
                result[code] = r[0]
            else:
                remaining.append(code)
        # 实时接口补充
        for code in remaining:
            try:
                import urllib.request, urllib.parse, json as _json
                secid = '1.' + code if code.startswith('6') else '0.' + code
                url = ('https://push2.eastmoney.com/api/qt/ulist.np/get?'
                       + urllib.parse.urlencode({
                           'fltt': '2', 'invt': '2',
                           'fields': 'f12,f14',
                           'secids': secid,
                       }))
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = _json.loads(resp.read().decode())
                items = data.get('data', {}).get('diff', [])
                if items:
                    name = items[0].get('f14', '')
                    if name:
                        result[code] = name
            except Exception:
                pass
        return result
    
    def get_top_lhb_stocks(self, limit: int = 10) -> List[Dict]:
        """
        获取龙虎榜净买入最多的股票
        
        Args:
            limit: 返回数量
        
        Returns:
            股票列表，按净买入金额排序
        """
        # 直接从龙虎榜表获取所有有数据的股票
        cursor = self.cache.conn.cursor()
        # 确保 name 列存在
        cursor.execute("PRAGMA table_info(lhb)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'name' not in columns:
            cursor.execute("ALTER TABLE lhb ADD COLUMN name TEXT DEFAULT ''")
            self.cache.conn.commit()

        cutoff = datetime.now() - timedelta(hours=48)

        cursor.execute('''
            SELECT code, name, buy_amount, sell_amount, net_amount, update_time
            FROM lhb
            WHERE update_time > ? AND net_amount > 0
            ORDER BY net_amount DESC
            LIMIT ?
        ''', (cutoff, limit))

        lhb_stocks = []
        for row in cursor.fetchall():
            code = row[0]
            lhb_name = row[1] or ''
            # 名称优先级：lhb表自带 > stocks表(不限时间) > 实时拉取 > 代码兜底
            if not lhb_name:
                # 从 stocks 表直接查名称（不限时间，只查 name 列）
                try:
                    c2 = self.cache.conn.cursor()
                    c2.execute("SELECT name FROM stocks WHERE code=? AND name IS NOT NULL AND name!='' LIMIT 1", (code,))
                    r2 = c2.fetchone()
                    if r2 and r2[0]:
                        lhb_name = r2[0]
                except Exception:
                    pass
            if not lhb_name:
                # 尝试实时获取
                try:
                    stock_data = self.cache.get_stock(code)
                    if stock_data and stock_data.get('name'):
                        lhb_name = stock_data['name']
                except Exception:
                    pass
            name = lhb_name or code
            # 回写名称到 lhb 表
            if lhb_name and not row[1]:
                try:
                    cursor.execute("UPDATE lhb SET name=? WHERE code=?", (lhb_name, code))
                    self.cache.conn.commit()
                except Exception:
                    pass
            # 获取涨跌幅
            try:
                stock_data = self.cache.get_stock(code)
                change_pct = stock_data.get('change_pct', 0) if stock_data else 0
            except Exception:
                change_pct = 0
            lhb_stocks.append({
                'code': code,
                'name': name,
                'buy_amount': row[2],
                'sell_amount': row[3],
                'net_amount': row[4],
                'change_pct': change_pct
            })
        
        return lhb_stocks
    
    def analyze_lhb_sentiment(self) -> Dict:
        """
        分析龙虎榜整体情绪
        
        Returns:
            情绪分析结果
        """
        top_stocks = self.get_top_lhb_stocks(limit=50)
        
        if not top_stocks:
            return {
                'sentiment': '无数据',
                'emoji': '⚪',
                'score': 50,
                'description': '今日无龙虎榜数据',
                'top_stocks': []
            }
        
        # 计算净买入总额
        total_net = sum(s['net_amount'] for s in top_stocks)
        avg_net = total_net / len(top_stocks) if top_stocks else 0
        
        # 计算上涨股票占比
        gainers = [s for s in top_stocks if s.get('change_pct', 0) > 0]
        gainer_ratio = len(gainers) / len(top_stocks) if top_stocks else 0
        
        # 情绪评分 (0-100)
        score = 50 + (gainer_ratio - 0.5) * 40 + min(avg_net / 10000000, 10)
        score = max(0, min(100, score))
        
        if score >= 70:
            sentiment = '极度乐观'
            emoji = '🔴'
        elif score >= 60:
            sentiment = '乐观'
            emoji = '🟠'
        elif score >= 50:
            sentiment = '偏乐观'
            emoji = '🟢'
        elif score >= 40:
            sentiment = '偏悲观'
            emoji = '🟡'
        elif score >= 30:
            sentiment = '悲观'
            emoji = '🔵'
        else:
            sentiment = '极度悲观'
            emoji = '⚫'
        
        return {
            'sentiment': sentiment,
            'emoji': emoji,
            'score': round(score, 1),
            'description': f'龙虎榜净买入总额：{total_net/10000:.1f}万，上涨占比：{gainer_ratio*100:.1f}%',
            'top_stocks': top_stocks[:5]
        }


def main():
    """主函数：获取并保存今日龙虎榜数据"""
    print("=" * 70)
    print("龙虎榜数据获取")
    print("=" * 70)
    
    fetcher = LHBFetcher()
    
    # 获取今日龙虎榜
    today = datetime.now().strftime('%Y%m%d')
    print(f"\n📊 获取 {today} 龙虎榜数据...")
    fetcher.save_lhb_to_cache(today)
    
    # 分析龙虎榜情绪
    print("\n📈 龙虎榜情绪分析:")
    sentiment = fetcher.analyze_lhb_sentiment()
    print(f"   情绪：{sentiment['emoji']} {sentiment['sentiment']} ({sentiment['score']}分)")
    print(f"   {sentiment['description']}")
    
    if sentiment.get('top_stocks'):
        print("\n🏆 净买入 TOP5:")
        for i, stock in enumerate(sentiment['top_stocks'], 1):
            print(f"   {i}. {stock['code']} {stock['name']}: "
                  f"净买入 {stock['net_amount']/10000:.1f}万 "
                  f"({stock['change_pct']:+.2f}%)")
    
    print("\n" + "=" * 70)
    print(f"完成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


if __name__ == '__main__':
    main()
