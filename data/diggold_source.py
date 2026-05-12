#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
东财掘金 SDK 数据源
通过掘金量化终端专用协议获取数据，不依赖 HTTP，不受东方财富 Web API 封禁影响。
"""

import os
import sys
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# 加载 .env
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    load_dotenv(_env_path)
except Exception:
    pass

_DIGGOLD_TOKEN = os.environ.get('DIGGOLD_TOKEN', '')

_AVAILABLE = False
_init_error = None
_terminal_online = None  # None=未检测, True/False=已检测

try:
    from gm.api import set_token, history, history_n, get_instruments
    if _DIGGOLD_TOKEN:
        set_token(_DIGGOLD_TOKEN)
    _AVAILABLE = True
except ImportError:
    _init_error = 'gm 包未安装'
except Exception as e:
    _init_error = str(e)


class DiggoldSource:
    """掘金数据源"""

    # 股票名缓存 {code: name}
    _name_cache: Dict[str, str] = {}
    # 只加载这些代码的名称（None=未设置，加载全部主板）
    _filter_codes: Optional[set] = None

    def __init__(self):
        if not _AVAILABLE:
            raise RuntimeError(f'掘金 SDK 不可用: {_init_error}')

    @staticmethod
    def is_available() -> bool:
        global _terminal_online
        if not _AVAILABLE:
            return False
        if _terminal_online is None:
            try:
                df = get_instruments(exchanges=['SHSE'], sec_types=1, df=True)
                _terminal_online = df is not None and not df.empty
            except Exception:
                _terminal_online = False
            if not _terminal_online:
                print('⚠️ 掘金终端不可达，跳过掘金数据源', flush=True)
        return _terminal_online

    @staticmethod
    def _code_to_symbol(code: str) -> str:
        """600519 -> SHSE.600519, 000001 -> SZSE.000001"""
        if code.startswith('6') or code.startswith('5'):
            return f'SHSE.{code}'
        return f'SZSE.{code}'

    @staticmethod
    def _symbol_to_code(symbol: str) -> str:
        """SHSE.600519 -> 600519"""
        return symbol.split('.')[-1] if '.' in symbol else symbol

    def _load_name_cache(self):
        """加载主板A股名称映射（排除创业板3xx、科创板688）"""
        if self._name_cache:
            return
        try:
            df = get_instruments(exchanges=['SHSE', 'SZSE'], sec_types=1, df=True)
            if df is not None and not df.empty:
                # 适配不同版本的列名
                sym_col = next((c for c in df.columns if 'symbol' in c.lower()), None)
                name_col = next((c for c in df.columns if 'sec_name' in c.lower() or 'name' in c.lower()), None)
                if not sym_col or not name_col:
                    print(f'⚠️ 掘金 get_instruments 列名不匹配: {list(df.columns)}', flush=True)
                    return
                for _, row in df.iterrows():
                    sym = str(row.get(sym_col, ''))
                    name = str(row.get(name_col, ''))
                    if sym and name:
                        code = self._symbol_to_code(sym)
                        # 只保留主板：排除创业板(3开头)、科创板(688开头)
                        if code.startswith('3') or code.startswith('688'):
                            continue
                        # 如果设置了过滤列表，只保留列表中的代码
                        if self._filter_codes is not None and code not in self._filter_codes:
                            continue
                        self._name_cache[code] = name
                print(f'✅ 掘金加载 {len(self._name_cache)} 只主板股票名称', flush=True)
            else:
                print('⚠️ 掘金 get_instruments 返回空数据', flush=True)
        except Exception as e:
            print(f'⚠️ 掘金加载名称缓存失败: {e}', flush=True)

    @classmethod
    def set_filter_codes(cls, codes: List[str]):
        """设置只加载指定代码的名称（在创建实例前调用）"""
        cls._filter_codes = set(codes)
        cls._name_cache.clear()  # 清除旧缓存，下次 _load_name_cache 会重新加载

    def get_realtime_quote(self, code: str) -> Optional[Dict]:
        """获取单只股票最新行情"""
        if not self._name_cache:
            self._load_name_cache()
        try:
            symbol = self._code_to_symbol(code)
            df = history_n(
                symbol=symbol,
                frequency='1d',
                count=2,
                end_time=datetime.now().strftime('%Y-%m-%d'),
                adjust=1,
                df=True,
            )
            if df is None or df.empty:
                return None

            row = df.iloc[-1]
            price = float(row['close'])
            prev_close = float(row.get('pre_close', 0)) or price
            change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
            return {
                'code': code,
                'name': self._name_cache.get(code, ''),
                'price': price,
                'change_pct': change_pct,
                'volume': float(row.get('volume', 0)),
                'amount': float(row.get('amount', 0)),
                'open': float(row.get('open', 0)),
                'high': float(row.get('high', 0)),
                'low': float(row.get('low', 0)),
                'prev_close': prev_close,
                'source': 'diggold',
            }
        except Exception:
            return None

    def get_realtime_batch(self, codes: List[str]) -> List[Dict]:
        """批量获取实时行情（并行）"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self.get_realtime_quote, code): code for code in codes}
            for future in as_completed(futures):
                try:
                    data = future.result(timeout=10)
                    if data:
                        results.append(data)
                except Exception:
                    pass
        return results

    def get_history_kline(self, code: str, days: int = 120) -> Optional[pd.DataFrame]:
        """获取历史 K 线，返回标准格式 DataFrame"""
        try:
            symbol = self._code_to_symbol(code)
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=int(days * 1.5))).strftime('%Y-%m-%d')

            data = history(
                symbol=symbol,
                frequency='1d',
                start_time=start_date,
                end_time=end_date,
                adjust=1,
                df=True,
            )
            if data is None or data.empty:
                return None

            # 处理日期列
            date_col = None
            if 'eob' in data.columns:
                date_col = 'eob'
            elif 'bob' in data.columns:
                date_col = 'bob'

            if date_col:
                data['date'] = pd.to_datetime(data[date_col])
                data = data.drop(columns=[date_col])

            if 'date' in data.columns:
                data.set_index('date', inplace=True)

            # 标准化列
            keep = []
            for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                if col in data.columns:
                    keep.append(col)
            if keep:
                data = data[keep]
            for col in keep:
                data[col] = pd.to_numeric(data[col], errors='coerce')

            # 截取指定天数
            if len(data) > days:
                data = data.iloc[-days:]

            return data
        except Exception:
            return None
