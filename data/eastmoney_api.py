#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
东方财富实时行情 API（参照 02 项目实现）
数据源：https://push2.eastmoney.com
特点：免费、实时、无需 API Key
"""

import re
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests


class EastMoneyAPI:
    """东方财富实时行情 API"""

    # 02 项目使用的 HTTPS 批量行情接口（更稳定）
    ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"

    DEFAULT_FIELDS = (
        "f12,f14,f2,f3,f4,f5,f6,f7,f8,f9,f10,"
        "f15,f16,f17,f18,f51,f52,f55,f57,f58,f59,f60,f61,f62,f63"
    )

    def __init__(self, timeout: int = 5, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/",
            }
        )

    def _normalize_code(self, code: str) -> str:
        c = str(code or "").strip().upper().replace(" ", "")
        if c.startswith(("SH", "SZ", "BJ")) and len(c) == 8 and c[2:].isdigit():
            c = c[2:]
        if "." in c:
            left, right = c.split(".", 1)
            if left.isdigit() and right in {"SH", "SS", "SZ", "BJ"}:
                c = left
        if not re.fullmatch(r"\d{6}", c):
            raise ValueError(f"不支持的股票代码格式: {code}")
        return c

    def _get_secid(self, code: str) -> str:
        """转换为东方财富 secid（市场.代码）"""
        c = self._normalize_code(code)
        if c.startswith("6"):  # 沪市（含科创板 688）
            return f"1.{c}"
        # 深市 + 北交所（沿用 02 项目默认映射）
        return f"0.{c}"

    def _request_json(self, url: str, params: Dict) -> Optional[Dict]:
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json()
            except Exception as exc:
                if attempt >= self.max_retries - 1:
                    print(f"请求失败: {exc}")
            time.sleep(0.4 * (attempt + 1))
        return None

    def _parse_realtime_item(self, item: Dict) -> Dict:
        code = str(item.get("f12", ""))
        latest = item.get("f2") or 0
        prev_close = item.get("f18") or item.get("f59") or 0
        change_pct = item.get("f3")
        if change_pct is None and prev_close:
            change_pct = round((latest - prev_close) / prev_close * 100, 2)

        return {
            "code": code,
            "name": item.get("f14", ""),
            "latest_price": latest,
            "price": latest,  # 兼容项目内常见字段名
            "change_pct": change_pct or 0,
            "change_amount": item.get("f4", 0),
            "open": item.get("f17", 0),
            "high": item.get("f15", 0),
            "low": item.get("f16", 0),
            "close": prev_close,
            "volume": item.get("f5", 0),
            "turnover": item.get("f6", 0),
            "amplitude": item.get("f7", 0),
            "turnover_rate": item.get("f8", 0),
            "pe_ratio": item.get("f9", 0),
            "volume_ratio": item.get("f10", item.get("f55", 0)),
            "limit_up": item.get("f51", 0),
            "limit_down": item.get("f52", 0),
            "market_cap": item.get("f20"),
            "float_market_cap": item.get("f21"),
            "change_rate": item.get("f61"),
        }

    def get_realtime(self, code: str, fields: str = None) -> Optional[Dict]:
        """获取单只股票实时行情，失败返回 None"""
        try:
            c = self._normalize_code(code)
        except ValueError:
            return None
        rows = self.get_batch([c], fields=fields)
        return rows[0] if rows else None

    def get_batch(self, codes: List[str], fields: str = None) -> List[Dict]:
        """批量获取股票实时行情（按 50 只分批请求）"""
        if not codes:
            return []

        fields = fields or self.DEFAULT_FIELDS
        normalized = []
        for c in codes:
            try:
                normalized.append(self._normalize_code(c))
            except ValueError:
                continue
        if not normalized:
            return []

        results: List[Dict] = []
        order = {c: i for i, c in enumerate(normalized)}

        for i in range(0, len(normalized), 50):
            batch = normalized[i : i + 50]
            secids = ",".join(self._get_secid(c) for c in batch)
            payload = self._request_json(
                self.ULIST_URL,
                params={"fltt": "2", "invt": "2", "fields": fields, "secids": secids},
            )
            if not payload:
                continue
            diff = payload.get("data", {}).get("diff", []) or []
            for item in diff:
                parsed = self._parse_realtime_item(item)
                if parsed.get("code"):
                    results.append(parsed)

        results.sort(key=lambda x: order.get(x.get("code", ""), 9999))
        return results

    def get_market_all(self, market: str = "all") -> List[Dict]:
        """
        获取全市场股票行情（单页）
        market: all / sh / sz / bj
        """
        if market not in {"all", "sh", "sz", "bj"}:
            market = "all"

        fs_map = {
            "sh": "m:1 t:2,m:1 t:23",
            "sz": "m:0 t:6,m:0 t:80",
            "bj": "m:0 t:81 s:2048",
            "all": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        }

        payload = self._request_json(
            self.CLIST_URL,
            params={
                "pn": 1,
                "pz": 5000,
                "po": 1,
                "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": fs_map[market],
                "fields": (
                    "f12,f14,f2,f3,f4,f5,f6,f7,f8,f9,f10,"
                    "f15,f16,f17,f18,f20,f21,f23"
                ),
            },
        )
        if not payload:
            return []
        stocks = payload.get("data", {}).get("diff", []) or []
        return [self._parse_market_data(s) for s in stocks]

    def _parse_market_data(self, data: Dict) -> Dict:
        """解析全市场列表数据"""
        return {
            "code": data.get("f12", ""),
            "name": data.get("f14", ""),
            "latest_price": data.get("f2", 0),
            "change_pct": data.get("f3", 0),
            "change_amount": data.get("f4", 0),
            "volume": data.get("f5", 0),
            "turnover": data.get("f6", 0),
            "amplitude": data.get("f7", 0),
            "turnover_rate": data.get("f8", 0),
            "pe_ratio": data.get("f9", 0),
            "volume_ratio": data.get("f10", 0),
            "high": data.get("f15", 0),
            "low": data.get("f16", 0),
            "open": data.get("f17", 0),
            "close": data.get("f18", 0),
            "total_market_cap": data.get("f20", 0),
            "float_market_cap": data.get("f21", 0),
            "pb_ratio": data.get("f23", 0),
        }


def test_api():
    """测试 API 连接"""
    api = EastMoneyAPI()
    print("=" * 70)
    print("东方财富实时行情 API 测试")
    print("=" * 70)

    test_codes = ["000001", "600519", "300750", "688981"]
    for code in test_codes:
        data = api.get_realtime(code)
        if data:
            print(f"\n{data['code']} - {data['name']}:")
            print(f"  最新价：¥{data['latest_price']:.2f}")
            print(f"  涨跌幅：{data['change_pct']:.2f}%")
            print(f"  成交量：{int(data['volume']):,}")
            print(f"  成交额：¥{(data['turnover'] or 0)/10000:.2f}万")
        else:
            print(f"\n{code}: 获取失败")

    print("\n" + "=" * 70)
    print(f"测试完成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


if __name__ == "__main__":
    test_api()
