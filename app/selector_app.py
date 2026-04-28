#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
选股引擎 — Web服务入口
提供三种策略的选股 API
"""

import os
import sys
import hashlib
from functools import wraps
from datetime import datetime

# ── 路径注入（使 data/ selectors/ utils/ 均可直接 import）─────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 04-stock-selector/
for _sub in ('data', 'selectors', 'utils'):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['SECRET_KEY'] = 'selector-secret-key-change-in-production'

# ── 登录 ────────────────────────────────────────────────────────────────────
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

USERS = {
    'admin': {'password': hashlib.sha256('admin123'.encode()).hexdigest(), 'id': 1},
}

class User(UserMixin):
    def __init__(self, uid, username):
        self.id = uid
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    for username, data in USERS.items():
        if data['id'] == int(user_id):
            return User(user_id, username)
    return None

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username in USERS:
            if hashlib.sha256(password.encode()).hexdigest() == USERS[username]['password']:
                login_user(User(USERS[username]['id'], username), remember=True)
                return redirect(request.args.get('next') or url_for('index'))
        return render_template('login.html', error='用户名或密码错误')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

from config import WEB_HOST, WEB_PORT

# ── 页面路由 ────────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def index():
    from gpt_analyst import _resolve_model_and_backend
    _, model_name = _resolve_model_and_backend()
    display_name = model_name.upper() if model_name else "AI"
    return render_template('index.html', username=current_user.username, llm_display_name=display_name)


# ── 选股 API ─────────────────────────────────────────────────────────────────
@app.route('/api/selector/run', methods=['POST'])
@login_required
def api_run_selector():
    """
    运行选股器
    请求体: {"type": "short|long|enhanced", "top_n": 5}
    """
    data = request.json or {}
    selector_type = data.get('type', 'long')
    top_n = int(data.get('top_n', 5))

    try:
        if selector_type == 'short':
            from short_term_selector import ShortTermSelector
            selector = ShortTermSelector()
        elif selector_type == 'enhanced':
            from enhanced_long_term_selector import EnhancedLongTermSelector
            selector = EnhancedLongTermSelector()
        else:
            from long_term_selector import LongTermSelector
            selector = LongTermSelector()

        stocks = selector.select_top_stocks(top_n=top_n)
        selector.close()

        return jsonify({
            'status': 'success',
            'type': selector_type,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data': stocks,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)})


def _report_to_markdown(report_text: str, stocks: list, type_label: str) -> str:
    """将纯文本报告转换为 Markdown 格式"""
    lines = [
        f"# {type_label}选股报告",
        f"",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 推荐数量：{len(stocks)} 只",
        f"",
        "---",
        "",
    ]
    for i, stock in enumerate(stocks, 1):
        name = stock.get('name', '')
        code = stock.get('code', '')
        score = stock.get('score', 0)
        rating = stock.get('rating', '')
        price = stock.get('price', 0)
        change = stock.get('change_pct', 0)
        signal = stock.get('signal', {})
        details = stock.get('details', {})

        lines.append(f"## {i}. {name}（{code}）")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 评级 | **{rating}** |")
        lines.append(f"| 评分 | {score:.1f} / 100 |")
        lines.append(f"| 价格 | ¥{price:.2f}（{change:+.2f}%）|")
        if signal:
            lines.append(f"| 信号 | {signal.get('decision', '-')}（{signal.get('buy_count', 0)} 个买点）|")
        lines.append("")

        # 技术面
        trend = details.get('trend', {})
        momentum = details.get('momentum', {})
        volume = details.get('volume', {})
        if trend or momentum:
            lines.append("### 技术面")
            lines.append("")
            if trend:
                lines.append(f"- 趋势：{trend.get('rating', '-')}（{trend.get('score', 0):.1f}/30）")
            if momentum:
                lines.append(f"- 动量：5日 {momentum.get('returns_5d', 0):+.2f}% / 20日 {momentum.get('returns_20d', 0):+.2f}%")
            if volume:
                lines.append(f"- 量能：{volume.get('obv_trend', '-')}，量比 {volume.get('volume_ratio', 0):.2f}")
            lines.append("")

        # 基本面（增强版）
        fund = details.get('fundamental', {})
        if fund:
            lines.append("### 基本面")
            lines.append("")
            lines.append(f"- ROE：{fund.get('roe', 0):.1f}%")
            lines.append(f"- 利润增长：{fund.get('profit_growth', 0):+.1f}%")
            lines.append(f"- 股息率：{fund.get('dividend_yield', 0):.2f}%")
            lines.append("")

        # 估值（增强版）
        val = details.get('valuation', {})
        if val:
            lines.append("### 估值")
            lines.append("")
            peg_str = f"{val['peg']:.2f}" if val.get('peg') else "不适用"
            lines.append(f"- PE：{val.get('pe', 0):.1f}")
            lines.append(f"- PEG：{peg_str}")
            lines.append("")

        # 资金面
        fund_flow = details.get('fund_flow', {})
        if fund_flow:
            lines.append("### 资金面")
            lines.append("")
            lines.append(f"- 主力净流入：{fund_flow.get('main_in', 0):+.0f} 万")
            lines.append("")

        # 推荐理由
        reasons = trend.get('reasons', []) if trend else []
        if reasons:
            lines.append("### 推荐理由")
            lines.append("")
            for r in reasons[:3]:
                lines.append(f"- {r}")
            lines.append("")

        lines.append("---")
        lines.append("")

    lines.append("")
    lines.append("*> 本报告由量化选股系统自动生成，仅供参考，不构成投资建议。<")
    return "\n".join(lines)


@app.route('/api/selector/report', methods=['POST'])
@login_required
def api_get_selector_report():
    """
    生成选股报告并下载 Markdown 文件
    请求体: {"type": "short|long|enhanced", "stocks": [...]}
    """
    data = request.json or {}
    selector_type = data.get('type', 'long')
    stocks = data.get('stocks', [])

    if not stocks:
        return jsonify({'status': 'error', 'message': '无数据'})

    type_names = {'short': '短线', 'long': '中长线', 'enhanced': '增强版'}
    type_label = type_names.get(selector_type, '选股')

    try:
        if selector_type == 'short':
            from short_term_selector import ShortTermSelector
            selector = ShortTermSelector()
        elif selector_type == 'enhanced':
            from enhanced_long_term_selector import EnhancedLongTermSelector
            selector = EnhancedLongTermSelector()
        else:
            from long_term_selector import LongTermSelector
            selector = LongTermSelector()

        report = selector.generate_report(stocks)
        selector.close()

        md_content = _report_to_markdown(report, stocks, type_label)

        from urllib.parse import quote
        filename = f"{type_label}选股报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        return Response(
            md_content,
            mimetype='text/markdown; charset=utf-8',
            headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename)}"}
        )

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


def _calc_market_sentiment() -> dict:
    """获取市场情绪数据，优先用 MarketSentiment 模块，否则从缓存快速估算。"""
    try:
        from market_sentiment import MarketSentiment
        ms = MarketSentiment()
        return ms.calculate()
    except ImportError:
        pass
    except Exception:
        pass

    # 备用：从 SQLite 缓存快速估算
    try:
        from stock_cache_db import StockCache
        cache = StockCache()
        conn = cache.conn
        cursor = conn.cursor()
        from datetime import date as _date
        today = _date.today().isoformat()
        cursor.execute(
            "SELECT change_pct, price FROM stocks WHERE date(update_time)=? AND price>0",
            (today,)
        )
        rows = cursor.fetchall()
        # 判断数据日期
        data_date = today
        if len(rows) < 50:
            # 今天数据不足，尝试取最近有数据的日期
            cursor.execute(
                "SELECT date(update_time) as d FROM stocks WHERE price>0 GROUP BY d ORDER BY d DESC LIMIT 1"
            )
            d_row = cursor.fetchone()
            if d_row:
                data_date = d_row[0]
                cursor.execute(
                    "SELECT change_pct, price FROM stocks WHERE date(update_time)=? AND price>0",
                    (data_date,)
                )
                rows = cursor.fetchall()
        cache.close()

        if len(rows) < 50:
            return {'score': None, 'level': '数据不足',
                    'description': f'缓存仅 {len(rows)} 条，建议先运行选股',
                    'data_date': data_date, 'dimensions': {}}

        changes = [r[0] for r in rows if r[0] is not None]
        up = sum(1 for c in changes if c > 0)
        down = sum(1 for c in changes if c < 0)
        total = len(changes)
        limit_up = sum(1 for c in changes if c >= 9.9)

        up_ratio = up / total
        avg_change = sum(changes) / total
        limit_up_rate = limit_up / total
        strong_ratio = sum(1 for c in changes if c >= 3) / total

        score = (
            up_ratio * 30 +
            min(max(avg_change / 5, 0), 1) * 20 +
            min(limit_up_rate / 0.03, 1) * 20 +
            min(strong_ratio / 0.15, 1) * 15 +
            15
        )
        score = round(min(max(score, 0), 100), 1)

        if score >= 75: level = '极度乐观'
        elif score >= 60: level = '乐观'
        elif score >= 50: level = '中性偏多'
        elif score >= 40: level = '中性偏空'
        elif score >= 25: level = '悲观'
        else: level = '极度悲观'

        return {
            'score': score, 'level': level,
            'description': f'涨跌比 {up}:{down}，均涨幅 {avg_change:.2f}%，涨停 {limit_up} 只',
            'data_date': data_date,
            'dimensions': {
                '涨跌比': round(up_ratio * 100, 1),
                '均涨幅': round(avg_change, 2),
                '涨停率%': round(limit_up_rate * 100, 2),
                '强势股比%': round(strong_ratio * 100, 1),
                '样本数': total,
            }
        }
    except Exception:
        return {'score': None, 'level': '未知', 'description': '', 'dimensions': {}}


# ── GPT 深度分析 API ──────────────────────────────────────────────────────────
@app.route('/api/selector/gpt-analyze', methods=['POST'])
@login_required
def api_gpt_analyze():
    """
    对选股结果调用 GPT-4.1 生成深度研报
    请求体: {"type": "short|long|enhanced", "top_n": 5}
      或传入已计算好的股票列表: {"stocks": [...]}
    可选: {"stream": true}  — 流式返回
    """
    from flask import Response, stream_with_context
    data = request.json or {}
    stocks = data.get('stocks')

    # 若未传 stocks，先运行选股器
    if not stocks:
        selector_type = data.get('type', 'long')
        top_n = int(data.get('top_n', 5))
        try:
            if selector_type == 'short':
                from short_term_selector import ShortTermSelector
                selector = ShortTermSelector()
            elif selector_type == 'enhanced':
                from enhanced_long_term_selector import EnhancedLongTermSelector
                selector = EnhancedLongTermSelector()
            else:
                from long_term_selector import LongTermSelector
                selector = LongTermSelector()
            stocks = selector.select_top_stocks(top_n=top_n)
            selector.close()
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'选股失败: {e}'})

    if not stocks:
        return jsonify({'status': 'error', 'message': '选股结果为空，无法进行 GPT 分析'})

    # 将选股器输出适配为 gpt_analyst 所需格式
    codes = [s['code'] for s in stocks]
    stocks_data = []
    for s in stocks:
        det = s.get('details') or {}
        trend = det.get('trend') or {}
        momentum = det.get('momentum') or {}
        volume = det.get('volume') or {}
        strength = det.get('strength') or {}
        volatility = det.get('volatility') or {}
        bias = det.get('bias') or {}
        ff  = det.get('fund_flow') or {}
        trade = det.get('trade_points') or {}
        rsi_det = det.get('rsi') or {}
        kdj_det = det.get('kdj') or {}
        macd_det = det.get('macd') or {}
        boll_det = det.get('bollinger') or {}

        entry = {
            'code':       s.get('code'),
            'name':       s.get('name'),
            'price':      s.get('price'),
            'change_pct': s.get('change_pct'),
            # 完整技术指标
            'tech_indicators': {
                'ma5':          trend.get('ma5') or (det.get('ma') or {}).get('ma5'),
                'ma10':         trend.get('ma10') or (det.get('ma') or {}).get('ma10'),
                'ma20':         trend.get('ma20'),
                'ma60':         trend.get('ma60'),
                'rsi':          rsi_det.get('value') or (det.get('rsi') or {}).get('value'),
                'macd':         macd_det.get('macd_hist'),
                'dif':          macd_det.get('dif'),
                'dea':          macd_det.get('dea'),
                'kdj_k':        kdj_det.get('k'),
                'kdj_d':        kdj_det.get('d'),
                'kdj_j':        kdj_det.get('j'),
                'boll_upper':   boll_det.get('upper'),
                'boll_mid':     boll_det.get('middle'),
                'boll_lower':   boll_det.get('lower'),
                'atr':          trade.get('atr'),
                'adx':          strength.get('adx'),
                'short_score':  s.get('score'),
                'long_score':   s.get('score'),
            },
            # 趋势详情
            'trend': {
                'rating':     trend.get('rating'),
                'reasons':    trend.get('reasons', []),
            },
            # 动量
            'momentum': {
                'returns_5d':  momentum.get('returns_5d'),
                'returns_20d': momentum.get('returns_20d'),
            },
            # 量能
            'volume': {
                'obv_trend':    volume.get('obv_trend'),
                'volume_ratio': volume.get('volume_ratio'),
            },
            # 波动率
            'volatility': {
                'value':       volatility.get('value'),
                'rating':      volatility.get('rating'),
            },
            # 乖离率
            'bias': {
                'bias_20':     bias.get('bias_20'),
                'bias_60':     bias.get('bias_60'),
            },
            # 资金流
            'fund_flow': {
                'main_net_inflow':     ff.get('main_in'),
                'main_net_inflow_pct': ff.get('main_ratio'),
                'signal':              ff.get('signal'),
            },
            # 选股专属字段
            'selector_score':       s.get('score'),
            'selector_rating':      s.get('rating'),
            'buy_signals':          s.get('buy_signals', []),
            'sell_signals':         s.get('sell_signals', []),
            'stop_loss':            s.get('stop_loss'),
            'take_profit':          s.get('take_profit'),
            'stop_loss_pct':        s.get('stop_loss_pct'),
            'take_profit_pct':      s.get('take_profit_pct'),
            'risk_reward_ratio':    s.get('risk_reward_ratio'),
        }
        stocks_data.append(entry)

    # ── 丰富数据：公司画像、详细资金流、新闻、扩展基本面 ──────────
    from stock_cache_db import StockCache
    _enrich_cache = StockCache()
    try:
        from fundamental_data import FundamentalData
        from fund_flow_fetcher import FundFlowFetcher
        _fd = FundamentalData(cache=_enrich_cache)
        _ff = FundFlowFetcher(cache=_enrich_cache)

        for entry in stocks_data:
            code = entry.get('code', '')
            # 1. 公司画像（行业、经营范围）
            try:
                profile = _fd.get_company_profile(code)
                if profile and profile.get('industry'):
                    entry['company_profile'] = {
                        'industry': profile.get('industry', ''),
                        'business_scope': profile.get('business_scope', ''),
                        'pb_ratio': profile.get('pb', 0),
                        'total_market_cap': profile.get('total_market_cap', 0),
                    }
            except Exception:
                pass
            # 2. 详细资金流（超大单/大单/中单/小单）
            try:
                flow = _ff.fetch_and_save(code)
                if flow and flow.get('main_in'):
                    entry['fund_flow'].update({
                        'super_large_net': flow.get('super_large_net', 0),
                        'large_net': flow.get('large_net', 0),
                        'medium_net': flow.get('medium_net', 0),
                        'small_net': flow.get('small_net', 0),
                        'days_continuous': flow.get('days_continuous', 0),
                    })
            except Exception:
                pass
            # 3. 最近新闻
            try:
                news = _fd.get_stock_news(code)
                if news:
                    entry['recent_news'] = news
            except Exception:
                pass
            # 4. 扩展基本面（毛利率、净利率、负债率）
            try:
                fund = _fd.get_stock_fundamental(code)
                if fund:
                    entry['fundamental'] = {
                        'roe': fund.get('roe', 0),
                        'pe': fund.get('pe', 0),
                        'pb': fund.get('pb', 0),
                        'gross_margin': fund.get('gross_margin', 0),
                        'net_margin': fund.get('net_margin', 0),
                        'debt_to_asset': fund.get('debt_to_asset', 0),
                        'profit_growth': fund.get('profit_growth', 0),
                        'dividend_yield': fund.get('dividend_yield', 0),
                    }
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _enrich_cache.close()

    # 获取市场情绪数据
    sentiment = _calc_market_sentiment()

    use_stream = bool(data.get('stream', False))

    try:
        from gpt_analyst import run_analysis
        if use_stream:
            def generate():
                try:
                    for chunk in run_analysis(codes, sentiment, stocks_data, stream=True):
                        yield chunk
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    yield f'\n\n❌ 分析出错: {e}'
            return Response(stream_with_context(generate()), content_type='text/plain; charset=utf-8')
        else:
            report = run_analysis(codes, sentiment, stocks_data, stream=False)
            return jsonify({'status': 'success', 'report': report})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)})


# ── 市场情绪 API ────────────────────────────────────────────────────────────
@app.route('/api/market/sentiment', methods=['GET'])
@login_required
def api_market_sentiment():
    """7维市场情绪评分"""
    try:
        result = _calc_market_sentiment()
        return jsonify({'status': 'success', 'data': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


# ── 龙虎榜 API ──────────────────────────────────────────────────────────────
@app.route('/api/lhb/top', methods=['GET'])
@login_required
def api_lhb_top():
    """返回龙虎榜净买入 top-10 及情绪分析，先读缓存；无缓存则实时拉取并写入。"""
    try:
        from lhb_fetcher import LHBFetcher
        fetcher = LHBFetcher()
        # 先尝试刷新（写入带 name 的数据），失败不影响旧缓存
        try:
            fetcher.save_lhb_to_cache()
        except Exception:
            pass
        top = fetcher.get_top_lhb_stocks(limit=10)
        sentiment = fetcher.analyze_lhb_sentiment()
        return jsonify({'status': 'success', 'data': {'top': top, 'sentiment': sentiment}})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)})


# ── 个股搜索与分析 API ───────────────────────────────────────────────────
@app.route('/api/stock/search', methods=['GET'])
@login_required
def api_stock_search():
    """按代码或名称模糊搜索股票，返回候选列表"""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'status': 'success', 'data': []})

    try:
        cache = StockCache()
        cursor = cache.conn.cursor()
        cursor.execute(
            "SELECT code, name, price FROM stocks "
            "WHERE code LIKE ? OR name LIKE ? "
            "ORDER BY update_time DESC LIMIT 20",
            (q + '%', '%' + q + '%')
        )
        results = [{'code': r[0], 'name': r[1], 'price': r[2]} for r in cursor.fetchall()]
        cache.close()

        # SQLite 不足3条时，尝试掘金全量名称缓存补填
        if len(results) < 3:
            try:
                from diggold_source import DiggoldSource
                if DiggoldSource.is_available():
                    dg = DiggoldSource()
                    existing_codes = {r['code'] for r in results}
                    q_lower = q.lower()
                    for code, name in dg._name_cache.items():
                        if code in existing_codes:
                            continue
                        if code.startswith(q_lower) or q_lower in name:
                            results.append({'code': code, 'name': name, 'price': None})
                            existing_codes.add(code)
                            if len(results) >= 20:
                                break
            except Exception:
                pass

        return jsonify({'status': 'success', 'data': results})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/api/stock/analyze', methods=['POST'])
@login_required
def api_stock_analyze():
    """分析单只股票，请求体: {code, type}"""
    data = request.json or {}
    code = str(data.get('code', '')).strip().zfill(6)
    selector_type = data.get('type', 'long')

    if not code.isdigit() or len(code) != 6:
        return jsonify({'status': 'error', 'message': '请输入6位股票代码'})

    try:
        if selector_type == 'short':
            from short_term_selector import ShortTermSelector
            selector = ShortTermSelector()
        elif selector_type == 'enhanced':
            from enhanced_long_term_selector import EnhancedLongTermSelector
            selector = EnhancedLongTermSelector()
        else:
            from long_term_selector import LongTermSelector
            selector = LongTermSelector()

        result = selector.analyze_single_stock(code)
        selector.close()

        if not result:
            return jsonify({'status': 'error', 'message': f'无法分析 {code}，可能无历史数据'})

        return jsonify({
            'status': 'success',
            'type': selector_type,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data': [result],
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)})


# ── 启动 ───────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import socket
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        _local_ip = _s.getsockname()[0]
        _s.close()
    except Exception:
        _local_ip = "127.0.0.1"

    print(f"""
╔══════════════════════════════════════════════════════╗
║              📈 选股引擎 Web服务                      ║
║                                                      ║
║   本机访问:   http://localhost:{WEB_PORT}                ║
║   远程访问:   http://{_local_ip}:{WEB_PORT}          ║
║                                                      ║
║   默认账号:   admin / admin123                        ║
╚══════════════════════════════════════════════════════╝
""")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=True)
