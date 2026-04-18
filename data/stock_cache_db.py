#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股数据缓存管理 - SQLite数据库
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# 数据库存放在项目根目录下的 data/ 文件夹
_DATA_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DATA_DIR, 'stock_cache.db')

class StockCache:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.conn = None
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()
        
        # 股票基础信息表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stocks (
                code TEXT PRIMARY KEY,
                name TEXT,
                price REAL,
                change_pct REAL,
                volume REAL,
                amount REAL,
                update_time TIMESTAMP
            )
        ''')
        
        # 主力资金表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fund_flow (
                code TEXT PRIMARY KEY,
                main_in REAL,
                retail_in REAL,
                main_ratio REAL,
                update_time TIMESTAMP
            )
        ''')
        
        # 龙虎榜表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS lhb (
                code TEXT PRIMARY KEY,
                buy_amount REAL,
                sell_amount REAL,
                net_amount REAL,
                update_time TIMESTAMP
            )
        ''')
        
        # 技术指标表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tech_indicators (
                code TEXT PRIMARY KEY,
                ma5 REAL,
                ma10 REAL,
                ma20 REAL,
                rsi REAL,
                macd REAL,
                dif REAL,
                dea REAL,
                update_time TIMESTAMP
            )
        ''')

        # 历史K线缓存表（pickle 序列化 DataFrame）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS history_kline (
                code TEXT NOT NULL,
                days INTEGER NOT NULL,
                trade_date TEXT NOT NULL,
                data BLOB NOT NULL,
                update_time TIMESTAMP,
                PRIMARY KEY (code, days)
            )
        ''')

        # 基本面数据缓存表（季报/年报数据变动慢，缓存24小时）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fundamental (
                code TEXT PRIMARY KEY,
                roe REAL,
                profit_growth REAL,
                dividend_yield REAL,
                revenue_growth REAL,
                pe REAL,
                gross_margin REAL,
                net_margin REAL,
                debt_to_asset REAL,
                pb REAL,
                update_time TIMESTAMP
            )
        ''')

        # 公司画像表（行业、经营范围、市值等）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS company_profile (
                code TEXT PRIMARY KEY,
                industry TEXT,
                business_scope TEXT,
                pb REAL,
                total_market_cap REAL,
                float_market_cap REAL,
                update_time TIMESTAMP
            )
        ''')

        # 股票新闻缓存表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_news (
                code TEXT PRIMARY KEY,
                news_json TEXT,
                update_time TIMESTAMP
            )
        ''')

        self.conn.commit()

        # ── 兼容旧库：为已有表添加新列 ──────────────────────────────────
        _migrations = [
            ("fund_flow", "super_large_net", "REAL"),
            ("fund_flow", "large_net", "REAL"),
            ("fund_flow", "medium_net", "REAL"),
            ("fund_flow", "small_net", "REAL"),
            ("fund_flow", "days_continuous", "INTEGER"),
            ("fundamental", "gross_margin", "REAL"),
            ("fundamental", "net_margin", "REAL"),
            ("fundamental", "debt_to_asset", "REAL"),
            ("fundamental", "pb", "REAL"),
        ]
        for table, col, ctype in _migrations:
            try:
                cursor.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ctype}')
            except Exception:
                pass
        self.conn.commit()
    
    def save_stocks(self, stocks_data: List[Dict]):
        """批量保存股票数据"""
        cursor = self.conn.cursor()
        now = datetime.now()
        
        for stock in stocks_data:
            cursor.execute('''
                INSERT OR REPLACE INTO stocks 
                (code, name, price, change_pct, volume, amount, update_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                stock['code'],
                stock['name'],
                stock['price'],
                stock['change_pct'],
                stock.get('volume', 0),
                stock.get('amount', 0),
                now
            ))
        
        self.conn.commit()
    
    def get_stock(self, code: str, max_age_minutes: int = 30) -> Optional[Dict]:
        """获取单只股票数据，max_age_minutes 内的缓存有效"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        cursor.execute('SELECT * FROM stocks WHERE code = ? AND update_time > ?', (code, cutoff))
        row = cursor.fetchone()
        
        if row:
            return {
                'code': row[0],
                'name': row[1],
                'price': row[2],
                'change_pct': row[3],
                'volume': row[4],
                'amount': row[5],
                'update_time': row[6]
            }

        # 缓存 miss：实时拉取并写入缓存
        data = self._fetch_realtime(code)
        if data:
            self.save_stocks([data])
            return data
        return None

    def _fetch_realtime(self, code: str) -> Optional[Dict]:
        """缓存 miss 时，通过东方财富 HTTPS 接口实时获取股票基础信息"""
        try:
            import urllib.request, urllib.parse, json as _json
            secid = '1.' + code if code.startswith('6') else '0.' + code
            url = ('https://push2.eastmoney.com/api/qt/ulist.np/get?'
                   + urllib.parse.urlencode({
                       'fltt': '2', 'invt': '2',
                       'fields': 'f12,f14,f2,f3,f5,f6',
                       'secids': secid,
                   }))
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                obj = _json.loads(resp.read().decode('utf-8', 'ignore'))
            diff = obj.get('data', {}).get('diff', [])
            if not diff:
                return None
            it = diff[0]
            price = float(it.get('f2') or 0)
            change_pct = float(it.get('f3') or 0)
            return {
                'code': code,
                'name': it.get('f14', ''),
                'price': price,
                'change_pct': change_pct,
                'volume': float(it.get('f5') or 0),
                'amount': float(it.get('f6') or 0),
            }
        except Exception as e:
            print(f'⚠️ 实时获取{code}基础信息失败: {e}', flush=True)
            return None
    
    def get_all_stocks(self, max_age_minutes=30) -> List[Dict]:
        """获取所有股票（过期数据会被过滤）"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        
        cursor.execute('''
            SELECT code, name, price, change_pct, volume, amount, update_time
            FROM stocks
            WHERE update_time > ?
            ORDER BY change_pct DESC
        ''', (cutoff,))
        
        stocks = []
        for row in cursor.fetchall():
            stocks.append({
                'code': row[0],
                'name': row[1],
                'price': row[2],
                'change_pct': row[3],
                'volume': row[4],
                'amount': row[5],
                'update_time': row[6]
            })
        
        return stocks
    
    def save_fund_flow(self, code: str, data: Dict):
        """保存主力资金数据"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO fund_flow
            (code, main_in, retail_in, main_ratio,
             super_large_net, large_net, medium_net, small_net, days_continuous,
             update_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            code,
            data.get('main_in', 0),
            data.get('retail_in', 0),
            data.get('main_ratio', 0),
            data.get('super_large_net', 0),
            data.get('large_net', 0),
            data.get('medium_net', 0),
            data.get('small_net', 0),
            data.get('days_continuous', 0),
            datetime.now()
        ))
        self.conn.commit()

    def get_fund_flow(self, code: str, max_age_hours=24) -> Optional[Dict]:
        """获取主力资金数据"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(hours=max_age_hours)

        cursor.execute('''
            SELECT main_in, retail_in, main_ratio,
                   super_large_net, large_net, medium_net, small_net, days_continuous,
                   update_time
            FROM fund_flow
            WHERE code = ? AND update_time > ?
        ''', (code, cutoff))

        row = cursor.fetchone()
        if row:
            return {
                'main_in': row[0],
                'retail_in': row[1],
                'main_ratio': row[2],
                'super_large_net': row[3],
                'large_net': row[4],
                'medium_net': row[5],
                'small_net': row[6],
                'days_continuous': row[7],
                'update_time': row[8]
            }
        return None
    
    def save_tech_indicators(self, code: str, data: Dict):
        """保存技术指标数据"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO tech_indicators
            (code, ma5, ma10, ma20, rsi, macd, dif, dea, update_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            code,
            data.get('ma5'),
            data.get('ma10'),
            data.get('ma20'),
            data.get('rsi'),
            data.get('macd'),
            data.get('macd_dif'),
            data.get('macd_dea'),
            datetime.now()
        ))
        self.conn.commit()
    
    def get_tech_indicators(self, code: str, max_age_hours=24) -> Optional[Dict]:
        """获取技术指标数据"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        
        cursor.execute('''
            SELECT ma5, ma10, ma20, rsi, macd, dif, dea, update_time
            FROM tech_indicators
            WHERE code = ? AND update_time > ?
        ''', (code, cutoff))
        
        row = cursor.fetchone()
        if row:
            return {
                'ma5': row[0],
                'ma10': row[1],
                'ma20': row[2],
                'rsi': row[3],
                'macd': row[4],
                'dif': row[5],
                'dea': row[6],
                'update_time': row[7]
            }
        return None
    
    def save_lhb(self, code: str, data: Dict):
        """保存龙虎榜数据"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO lhb
            (code, buy_amount, sell_amount, net_amount, update_time)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            code,
            data.get('buy_amount', 0),
            data.get('sell_amount', 0),
            data.get('net_amount', 0),
            datetime.now()
        ))
        self.conn.commit()
    
    def get_lhb(self, code: str, max_age_hours=24) -> Optional[Dict]:
        """获取龙虎榜数据"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        
        cursor.execute('''
            SELECT buy_amount, sell_amount, net_amount, update_time
            FROM lhb
            WHERE code = ? AND update_time > ?
        ''', (code, cutoff))
        
        row = cursor.fetchone()
        if row:
            return {
                'buy_amount': row[0],
                'sell_amount': row[1],
                'net_amount': row[2],
                'update_time': row[3]
            }
        return None
    
    def get_cache_stats(self) -> Dict:
        """获取缓存统计信息"""
        cursor = self.conn.cursor()
        
        # 股票数量
        cursor.execute('SELECT COUNT(*) FROM stocks')
        stock_count = cursor.fetchone()[0]
        
        # 最新更新时间
        cursor.execute('SELECT MAX(update_time) FROM stocks')
        latest_update = cursor.fetchone()[0]
        
        # 资金流数据量
        cursor.execute('SELECT COUNT(*) FROM fund_flow')
        fund_count = cursor.fetchone()[0]
        
        return {
            'stock_count': stock_count,
            'latest_update': latest_update,
            'fund_flow_count': fund_count
        }
    
    def clear_old_data(self, days=7):
        """清理N天前的旧数据"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(days=days)
        
        cursor.execute('DELETE FROM stocks WHERE update_time < ?', (cutoff,))
        cursor.execute('DELETE FROM fund_flow WHERE update_time < ?', (cutoff,))
        cursor.execute('DELETE FROM lhb WHERE update_time < ?', (cutoff,))
        cursor.execute('DELETE FROM company_profile WHERE update_time < ?', (cutoff,))
        cursor.execute('DELETE FROM stock_news WHERE update_time < ?', (cutoff,))
        
        self.conn.commit()
    
    def save_history_kline(self, code: str, days: int, df) -> None:
        """持久化历史K线到SQLite（pickle序列化DataFrame）"""
        import pickle
        trade_date = datetime.now().strftime('%Y-%m-%d')
        data_blob = pickle.dumps(df, protocol=4)
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO history_kline (code, days, trade_date, data, update_time)
            VALUES (?, ?, ?, ?, ?)
        ''', (code, days, trade_date, data_blob, datetime.now()))
        self.conn.commit()

    def get_history_kline(self, code: str, days: int) -> 'Optional[pd.DataFrame]':
        """
        读取历史K线缓存。
        当天写入的数据直接返回；若是昨日收盘后（15:00后）缓存的数据且今天是非交易时间也可复用。
        简单策略：trade_date == 今天 则命中缓存，否则返回 None（让调用方重新抓取）。
        """
        import pickle
        today = datetime.now().strftime('%Y-%m-%d')
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT trade_date, data FROM history_kline WHERE code = ? AND days = ?',
            (code, days)
        )
        row = cursor.fetchone()
        if row and row[0] == today:
            try:
                return pickle.loads(row[1])
            except Exception:
                return None
        return None

    def save_fundamental(self, code: str, data: Dict):
        """保存基本面数据（财务指标，24小时有效）"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO fundamental
            (code, roe, profit_growth, dividend_yield, revenue_growth, pe,
             gross_margin, net_margin, debt_to_asset, pb, update_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            code,
            data.get('roe', 0.0),
            data.get('profit_growth', 0.0),
            data.get('dividend_yield', 0.0),
            data.get('revenue_growth', 0.0),
            data.get('pe', 0.0),
            data.get('gross_margin', 0.0),
            data.get('net_margin', 0.0),
            data.get('debt_to_asset', 0.0),
            data.get('pb', 0.0),
            datetime.now()
        ))
        self.conn.commit()

    def get_fundamental(self, code: str, max_age_hours: int = 24) -> Optional[Dict]:
        """获取基本面数据缓存，超时返回 None"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        cursor.execute('''
            SELECT roe, profit_growth, dividend_yield, revenue_growth, pe,
                   gross_margin, net_margin, debt_to_asset, pb
            FROM fundamental
            WHERE code = ? AND update_time > ?
        ''', (code, cutoff))
        row = cursor.fetchone()
        if row:
            return {
                'roe': row[0],
                'profit_growth': row[1],
                'dividend_yield': row[2],
                'revenue_growth': row[3],
                'pe': row[4],
                'gross_margin': row[5],
                'net_margin': row[6],
                'debt_to_asset': row[7],
                'pb': row[8],
            }
        return None

    # ── 公司画像 ──────────────────────────────────────────────────────

    def save_company_profile(self, code: str, data: Dict):
        """保存公司画像（行业、经营范围等）"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO company_profile
            (code, industry, business_scope, pb, total_market_cap, float_market_cap, update_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            code,
            data.get('industry', ''),
            data.get('business_scope', ''),
            data.get('pb', 0.0),
            data.get('total_market_cap', 0.0),
            data.get('float_market_cap', 0.0),
            datetime.now()
        ))
        self.conn.commit()

    def get_company_profile(self, code: str, max_age_hours: int = 24) -> Optional[Dict]:
        """获取公司画像缓存"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        cursor.execute('''
            SELECT industry, business_scope, pb, total_market_cap, float_market_cap, update_time
            FROM company_profile
            WHERE code = ? AND update_time > ?
        ''', (code, cutoff))
        row = cursor.fetchone()
        if row:
            return {
                'industry': row[0],
                'business_scope': row[1],
                'pb': row[2],
                'total_market_cap': row[3],
                'float_market_cap': row[4],
                'update_time': row[5]
            }
        return None

    # ── 股票新闻 ──────────────────────────────────────────────────────

    def save_stock_news(self, code: str, news_list: list):
        """保存股票新闻（JSON 序列化）"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO stock_news (code, news_json, update_time)
            VALUES (?, ?, ?)
        ''', (code, json.dumps(news_list, ensure_ascii=False), datetime.now()))
        self.conn.commit()

    def get_stock_news(self, code: str, max_age_hours: int = 2) -> Optional[list]:
        """获取股票新闻缓存"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        cursor.execute('''
            SELECT news_json FROM stock_news
            WHERE code = ? AND update_time > ?
        ''', (code, cutoff))
        row = cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()


# ============== 测试代码 ==============

def test_cache():
    print("🔍 测试SQLite缓存...")
    
    cache = StockCache()
    
    # 测试1: 保存数据
    print("\n1️⃣ 测试保存数据...")
    test_stocks = [
        {'code': '601318', 'name': '中国平安', 'price': 45.67, 'change_pct': 2.3, 'volume': 1000000, 'amount': 45670000},
        {'code': '600519', 'name': '贵州茅台', 'price': 1680.0, 'change_pct': -1.2, 'volume': 50000, 'amount': 84000000},
    ]
    cache.save_stocks(test_stocks)
    print("✅ 保存成功")
    
    # 测试2: 读取数据
    print("\n2️⃣ 测试读取数据...")
    stock = cache.get_stock('601318')
    if stock:
        print(f"✅ {stock['name']}: ¥{stock['price']} ({stock['change_pct']:+.2f}%)")
    
    # 测试3: 统计信息
    print("\n3️⃣ 缓存统计:")
    stats = cache.get_cache_stats()
    print(f"   股票数量: {stats['stock_count']}")
    print(f"   最新更新: {stats['latest_update']}")
    
    cache.close()
    print("\n✅ 测试完成!")


if __name__ == '__main__':
    test_cache()
