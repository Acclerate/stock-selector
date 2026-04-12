#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
混合数据源管理器
整合 Tushare Pro + 东方财富 + 腾讯 + 新浪财经 + akshare
实时行情优先级: 东方财富 > 腾讯 > 新浪 > akshare
历史K线优先级:  Tushare > 东方财富 > akshare
"""

import akshare as ak
import requests
import os
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stock_cache_db import StockCache

# 注意：不清除代理设置，保留系统代理以确保外网可达


class HybridDataSource:
    """混合数据源管理器"""
    
    def __init__(self, tushare_token: Optional[str] = None):
        """
        初始化数据源
        
        Args:
            tushare_token: Tushare Pro token (可选)
                          注册地址: https://tushare.pro/register
        """
        self.tushare_token = tushare_token
        self.tushare_available = False

        # 当天历史K线内存缓存: {(code, days): (date_str, DataFrame)}
        self._history_cache: dict = {}

        # SQLite 持久化缓存（跨进程复用当天K线）
        self._db_cache = StockCache()

        # 初始化东方财富 API（用于实时行情，HTTP已不通，仅作对象占位）
        try:
            import sys, os as _os
            _data_dir = _os.path.dirname(_os.path.abspath(__file__))
            if _data_dir not in sys.path:
                sys.path.insert(0, _data_dir)
            from eastmoney_api import EastMoneyAPI
            self._em = EastMoneyAPI(timeout=5)
        except Exception as _e:
            self._em = None

        # 尝试初始化Tushare
        if tushare_token:
            try:
                import tushare as ts
                ts.set_token(tushare_token)
                self.pro = ts.pro_api()
                self.pro.trade_cal(exchange='SSE', start_date='20260101', end_date='20260101')
                self.tushare_available = True
                print("数据源: 实时行情=东方财富→腾讯→新浪  历史K线=Tushare(主)→腾讯→akshare", flush=True)
            except Exception as e:
                print(f"⚠️ Tushare 连接失败({e})，历史K线将使用 腾讯→akshare", flush=True)
                print("数据源: 实时行情=东方财富→腾讯→新浪  历史K线=腾讯→akshare", flush=True)
        else:
            print("数据源: 实时行情=东方财富→腾讯→新浪  历史K线=腾讯→akshare", flush=True)
    
    def get_realtime_price(self, code: str) -> Optional[Dict]:
        """
        获取实时价格（智能策略：交易时间用新浪，盘后用akshare）
        
        Args:
            code: 股票代码 (如 '600900')
            
        Returns:
            {'code': str, 'name': str, 'price': float, 'change_pct': float, ...}
        """
        from datetime import datetime, time as dt_time
        
        # 判断是否交易时间
        now = datetime.now()
        current_time = now.time()
        weekday = now.weekday()
        
        is_trading = False
        if weekday < 5:  # 工作日
            morning_start = dt_time(9, 15)
            morning_end = dt_time(11, 30)
            afternoon_start = dt_time(13, 0)
            afternoon_end = dt_time(15, 0)
            is_trading = (morning_start <= current_time <= morning_end) or \
                        (afternoon_start <= current_time <= afternoon_end)
        
        # 优先级：东方财富 > 腾讯 > 新浪 > akshare（全时段均适用）
        if self._em:
            result = self._get_eastmoney_realtime(code)
            if result:
                return result

        result = self._get_tencent_realtime(code)
        if result:
            return result

        if is_trading:
            result = self._get_sina_realtime(code)
            if result:
                return result

        # 最后兜底：akshare
        result = self._get_akshare_realtime(code)
        return result
    
    def get_realtime_batch(self, codes: List[str]) -> List[Dict]:
        """
        批量获取实时价格（交易时间尝试新浪批量，盘后用akshare逐个）
        
        Args:
            codes: 股票代码列表
            
        Returns:
            [{'code': str, 'name': str, 'price': float, ...}, ...]
        """
        from datetime import datetime, time as dt_time
        
        # 判断是否交易时间
        now = datetime.now()
        current_time = now.time()
        weekday = now.weekday()
        
        is_trading = False
        if weekday < 5:
            morning_start = dt_time(9, 15)
            morning_end = dt_time(11, 30)
            afternoon_start = dt_time(13, 0)
            afternoon_end = dt_time(15, 0)
            is_trading = (morning_start <= current_time <= morning_end) or \
                        (afternoon_start <= current_time <= afternoon_end)
        
        # 优先东方财富批量（速度快）
        if self._em:
            try:
                em_results = self._get_eastmoney_batch(codes)
                if em_results and len(em_results) > 0:
                    return em_results
            except Exception:
                pass

        if is_trading:
            result = self._get_sina_batch(codes)
            if result and len(result) > 0:
                return result

        # 兜底：逐个查询
        results = []
        for code in codes:
            data = self.get_realtime_price(code)
            if data:
                results.append(data)

        return results
    
    def get_history_data(self, code: str, days: int = 120) -> Optional[pd.DataFrame]:
        """
        获取历史数据（优先级：Tushare > 东方财富 > akshare）
        同一天内相同 (code, days) 的请求直接返回内存缓存，无需重复抓取。

        Args:
            code: 股票代码
            days: 天数

        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount
        """
        today = datetime.now().strftime('%Y-%m-%d')
        cache_key = (code, days)
        cached = self._history_cache.get(cache_key)
        if cached and cached[0] == today:
            return cached[1].copy()

        # 查 SQLite 持久化缓存（同一天内跨进程复用）
        db_result = self._db_cache.get_history_kline(code, days)
        if db_result is not None and not db_result.empty:
            self._history_cache[cache_key] = (today, db_result)
            return db_result.copy()

        # 方案1: Tushare（推荐，数据质量高）
        if self.tushare_available:
            result = self._get_tushare_history(code, days)
            if result is not None and not result.empty:
                self._history_cache[cache_key] = (today, result)
                self._db_cache.save_history_kline(code, days, result)
                return result

        # 方案2: 东方财富（免费、快）
        result = self._get_eastmoney_history(code, days)
        if result is not None and not result.empty:
            self._history_cache[cache_key] = (today, result)
            self._db_cache.save_history_kline(code, days, result)
            return result

        # 方案3: akshare（兜底）
        result = self._get_akshare_history(code, days)
        if result is not None and not result.empty:
            self._history_cache[cache_key] = (today, result)
            self._db_cache.save_history_kline(code, days, result)
        return result
    
    def _get_eastmoney_realtime(self, code: str) -> Optional[Dict]:
        """东方财富单股实时行情 (HTTPS ulist.np, urllib)"""
        try:
            import urllib.request, urllib.parse, json as _json
            secid = '1.' + code if code.startswith('6') else '0.' + code
            fields = 'f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18'
            url = 'https://push2.eastmoney.com/api/qt/ulist.np/get?' + urllib.parse.urlencode({
                'fltt': '2', 'invt': '2', 'fields': fields, 'secids': secid,
            })
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                obj = _json.loads(resp.read().decode('utf-8', 'ignore'))
            diff = obj.get('data', {}).get('diff', [])
            if not diff:
                return None
            it = diff[0]
            price = it.get('f2') or 0
            prev_close = it.get('f18') or 0
            change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else (it.get('f3') or 0)
            return {
                'code': code,
                'name': it.get('f14', ''),
                'price': float(price),
                'change_pct': float(change_pct),
                'volume': float(it.get('f5') or 0),
                'amount': float(it.get('f6') or 0),
                'open': float(it.get('f17') or 0),
                'high': float(it.get('f15') or 0),
                'low': float(it.get('f16') or 0),
                'prev_close': float(prev_close),
                'source': 'eastmoney',
            }
        except Exception as e:
            print(f'⚠️ 东方财富实时行情失败 {code}: {e}', flush=True)
            return None

    def _get_eastmoney_batch(self, codes: List[str]) -> List[Dict]:
        """东方财富批量实时行情 (HTTPS ulist.np, urllib)"""
        try:
            import urllib.request, urllib.parse, json as _json
            results = []
            for i in range(0, len(codes), 50):
                batch = codes[i:i+50]
                secids = ','.join('1.' + c if c.startswith('6') else '0.' + c for c in batch)
                fields = 'f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18'
                url = 'https://push2.eastmoney.com/api/qt/ulist.np/get?' + urllib.parse.urlencode({
                    'fltt': '2', 'invt': '2', 'fields': fields, 'secids': secids,
                })
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    obj = _json.loads(resp.read().decode('utf-8', 'ignore'))
                diff = obj.get('data', {}).get('diff', [])
                for it in diff:
                    price = it.get('f2') or 0
                    prev_close = it.get('f18') or 0
                    change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else (it.get('f3') or 0)
                    results.append({
                        'code': str(it.get('f12', '')),
                        'name': it.get('f14', ''),
                        'price': float(price),
                        'change_pct': float(change_pct),
                        'volume': float(it.get('f5') or 0),
                        'amount': float(it.get('f6') or 0),
                        'source': 'eastmoney_batch',
                    })
            return results
        except Exception as e:
            print(f'⚠️ 东方财富批量行情失败: {e}', flush=True)
            return []

    def _get_tencent_realtime(self, code: str) -> Optional[Dict]:
        """腾讯实时行情 (HTTPS fqkline, urllib)"""
        try:
            import urllib.request, urllib.parse, json as _json
            symbol = 'sh' + code if code.startswith('6') else 'sz' + code
            # 取最近2条日K即可得到昨收+今价
            url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?' + urllib.parse.urlencode({
                'param': f'{symbol},day,,,2,qfq'
            })
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                obj = _json.loads(resp.read().decode('utf-8', 'ignore'))
            data = obj.get('data', {}).get(symbol, {})
            rows = data.get('qfqday') or data.get('day') or []
            if not rows:
                return None
            r = rows[-1]  # [date, open, close, high, low, vol]
            prev = rows[-2] if len(rows) >= 2 else r
            price = float(r[2])
            prev_close = float(prev[2])
            change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
            return {
                'code': code,
                'name': '',
                'price': price,
                'change_pct': change_pct,
                'volume': float(r[5]),
                'amount': 0,
                'open': float(r[1]),
                'high': float(r[3]),
                'low': float(r[4]),
                'prev_close': prev_close,
                'source': 'tencent',
            }
        except Exception as e:
            print(f'⚠️ 腾讯行情失败 {code}: {e}', flush=True)
            return None

    def _get_eastmoney_history(self, code: str, days: int) -> Optional[pd.DataFrame]:
        """东方财富历史K线（HTTPS fqkline via Tencent, urllib）"""
        try:
            import urllib.request, urllib.parse, json as _json
            symbol = 'sh' + code if code.startswith('6') else 'sz' + code
            fetch_days = days * 2  # 多取以覆盖非交易日
            url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?' + urllib.parse.urlencode({
                'param': f'{symbol},day,,,{fetch_days},qfq'
            })
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                obj = _json.loads(resp.read().decode('utf-8', 'ignore'))
            data = obj.get('data', {}).get(symbol, {})
            rows = data.get('qfqday') or data.get('day') or []
            if not rows:
                return None
            records = []
            for r in rows:
                try:
                    records.append({
                        'date': r[0],
                        'open': float(r[1]),
                        'close': float(r[2]),
                        'high': float(r[3]),
                        'low': float(r[4]),
                        'volume': float(r[5]),
                        'amount': 0.0,
                    })
                except Exception:
                    continue
            if not records:
                return None
            df = pd.DataFrame(records)
            return df[['date', 'open', 'high', 'low', 'close', 'volume', 'amount']].tail(days)
        except Exception as e:
            print(f'⚠️ 东方财富历史K线失败 {code}: {e}', flush=True)
            return None

    def _get_sina_realtime(self, code: str) -> Optional[Dict]:
        """新浪财经实时行情"""
        try:
            # 判断市场
            if code.startswith('6'):
                symbol = f'sh{code}'
            else:
                symbol = f'sz{code}'
            
            url = f'http://hq.sinajs.cn/list={symbol}'
            response = requests.get(url, timeout=3)
            
            if response.status_code != 200:
                return None
            
            # 解析数据
            content = response.text
            if 'var hq_str_' not in content:
                return None
            
            data_str = content.split('"')[1]
            fields = data_str.split(',')
            
            if len(fields) < 32:
                return None
            
            name = fields[0]
            price = float(fields[3])
            prev_close = float(fields[2])
            change_pct = ((price - prev_close) / prev_close) * 100
            volume = float(fields[8])
            amount = float(fields[9])
            
            return {
                'code': code,
                'name': name,
                'price': price,
                'change_pct': change_pct,
                'volume': volume,
                'amount': amount,
                'open': float(fields[1]),
                'high': float(fields[4]),
                'low': float(fields[5]),
                'prev_close': prev_close,
                'source': 'sina'
            }
        except Exception as e:
            print(f"⚠️ 新浪财经获取失败 {code}: {e}")
            return None
    
    def _get_sina_batch(self, codes: List[str]) -> Optional[List[Dict]]:
        """新浪财经批量查询"""
        try:
            # 转换为新浪格式
            symbols = []
            for code in codes:
                if code.startswith('6'):
                    symbols.append(f'sh{code}')
                else:
                    symbols.append(f'sz{code}')
            
            # 新浪限制：一次最多50只
            if len(symbols) > 50:
                symbols = symbols[:50]
            
            symbol_str = ','.join(symbols)
            url = f'http://hq.sinajs.cn/list={symbol_str}'
            response = requests.get(url, timeout=5)
            
            if response.status_code != 200:
                return None
            
            # 解析批量数据
            results = []
            lines = response.text.strip().split('\n')
            
            for i, line in enumerate(lines):
                if 'var hq_str_' not in line:
                    continue
                
                code = codes[i] if i < len(codes) else None
                if not code:
                    continue
                
                data_str = line.split('"')[1]
                fields = data_str.split(',')
                
                if len(fields) < 32:
                    continue
                
                name = fields[0]
                price = float(fields[3])
                prev_close = float(fields[2])
                change_pct = ((price - prev_close) / prev_close) * 100
                
                results.append({
                    'code': code,
                    'name': name,
                    'price': price,
                    'change_pct': change_pct,
                    'volume': float(fields[8]),
                    'amount': float(fields[9]),
                    'source': 'sina_batch'
                })
            
            return results if results else None
        except Exception as e:
            print(f"⚠️ 新浪批量查询失败: {e}")
            return None
    
    def _get_tushare_realtime(self, code: str) -> Optional[Dict]:
        """Tushare实时行情"""
        try:
            # 转换为Tushare格式
            if code.startswith('6'):
                ts_code = f'{code}.SH'
            else:
                ts_code = f'{code}.SZ'
            
            # 获取今日行情
            today = datetime.now().strftime('%Y%m%d')
            df = self.pro.daily(ts_code=ts_code, trade_date=today)
            
            if df.empty:
                return None
            
            row = df.iloc[0]
            change_pct = row['pct_chg']
            
            return {
                'code': code,
                'name': '',  # Tushare不返回名称，需要额外查询
                'price': row['close'],
                'change_pct': change_pct,
                'volume': row['vol'] * 100,  # 手转股
                'amount': row['amount'] * 1000,  # 千元转元
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'prev_close': row['pre_close'],
                'source': 'tushare'
            }
        except Exception as e:
            print(f"⚠️ Tushare获取失败 {code}: {e}")
            return None
    
    def _get_akshare_realtime(self, code: str) -> Optional[Dict]:
        """akshare实时行情"""
        try:
            df = ak.stock_zh_a_spot_em()
            
            if df is None or df.empty:
                return None
            
            stock = df[df['代码'] == code]
            
            if stock.empty:
                return None
            
            row = stock.iloc[0]
            
            return {
                'code': code,
                'name': row['名称'],
                'price': row['最新价'],
                'change_pct': row['涨跌幅'],
                'volume': row['成交量'],
                'amount': row['成交额'],
                'source': 'akshare'
            }
        except Exception as e:
            print(f"⚠️ akshare获取失败 {code}: {e}")
            return None
    
    def _get_tushare_history(self, code: str, days: int) -> Optional[pd.DataFrame]:
        """Tushare历史数据"""
        try:
            # 转换为Tushare格式
            if code.startswith('6'):
                ts_code = f'{code}.SH'
            else:
                ts_code = f'{code}.SZ'
            
            # 计算日期范围
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days*2)).strftime('%Y%m%d')
            
            df = self.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            
            if df.empty:
                return None
            
            # 转换为标准格式
            df = df.rename(columns={
                'trade_date': 'date',
                'vol': 'volume',
            })
            
            # 成交量单位转换（手 -> 股）
            df['volume'] = df['volume'] * 100
            df['amount'] = df['amount'] * 1000
            
            # 按日期排序
            df = df.sort_values('date')
            
            return df[['date', 'open', 'high', 'low', 'close', 'volume', 'amount']].tail(days)
        except Exception as e:
            print(f"⚠️ Tushare历史数据获取失败 {code}: {e}")
            return None
    
    def _get_akshare_history(self, code: str, days: int) -> Optional[pd.DataFrame]:
        """akshare历史数据"""
        try:
            # 计算日期范围
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days*2)).strftime('%Y%m%d')
            
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="")
            
            if df is None or df.empty:
                return None
            
            # 重命名列
            df = df.rename(columns={
                '日期': 'date',
                '开盘': 'open',
                '最高': 'high',
                '最低': 'low',
                '收盘': 'close',
                '成交量': 'volume',
                '成交额': 'amount',
            })
            
            return df[['date', 'open', 'high', 'low', 'close', 'volume', 'amount']].tail(days)
        except Exception as e:
            print(f"⚠️ akshare历史数据获取失败 {code}: {e}")
            return None


# 单例模式
_instance = None

def get_hybrid_source(tushare_token: Optional[str] = None) -> HybridDataSource:
    """获取混合数据源单例"""
    global _instance
    if _instance is None:
        _instance = HybridDataSource(tushare_token)
    return _instance


if __name__ == '__main__':
    # 测试代码
    print("=" * 60)
    print("混合数据源测试")
    print("=" * 60)
    
    # 初始化（不配置token，使用免费数据源）
    ds = HybridDataSource()
    
    # 测试1: 单个股票实时行情
    print("\n测试1: 获取长江电力实时行情")
    data = ds.get_realtime_price('600900')
    if data:
        print(f"✅ {data['name']} ({data['code']})")
        print(f"   价格: ¥{data['price']:.2f}")
        print(f"   涨跌: {data['change_pct']:+.2f}%")
        print(f"   来源: {data['source']}")
    
    # 测试2: 批量查询
    print("\n测试2: 批量查询")
    codes = ['600900', '601985', '600905']
    results = ds.get_realtime_batch(codes)
    print(f"✅ 获取到 {len(results)} 只股票")
    for stock in results:
        print(f"   {stock['name']} ({stock['code']}): ¥{stock['price']:.2f} ({stock['change_pct']:+.2f}%)")
    
    # 测试3: 历史数据
    print("\n测试3: 获取历史数据")
    df = ds.get_history_data('600900', days=30)
    if df is not None:
        print(f"✅ 获取到 {len(df)} 天历史数据")
        print(df.tail())
