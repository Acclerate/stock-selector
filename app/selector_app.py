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

from flask import Flask, render_template, jsonify, request, redirect, url_for
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
    return render_template('index.html', username=current_user.username)


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


@app.route('/api/selector/report', methods=['POST'])
@login_required
def api_get_selector_report():
    """
    生成选股文字报告
    请求体: {"type": "short|long|enhanced", "stocks": [...]}
    """
    data = request.json or {}
    selector_type = data.get('type', 'long')
    stocks = data.get('stocks', [])

    if not stocks:
        return jsonify({'status': 'error', 'message': '无数据'})

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

        return jsonify({'status': 'success', 'report': report})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


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
        ff  = det.get('fund_flow') or {}
        trade = det.get('trade_points') or {}
        entry = {
            'code':       s.get('code'),
            'name':       s.get('name'),
            'price':      s.get('price'),
            'change_pct': s.get('change_pct'),
            'tech_indicators': {
                'ma20':        (det.get('trend') or {}).get('ma20'),
                'ma60':        (det.get('trend') or {}).get('ma60'),
                'short_score': s.get('score'),
                'long_score':  s.get('score'),
                'adx':         (det.get('strength') or {}).get('adx'),
                'atr':         trade.get('atr'),
            },
            'fund_flow': {
                'main_net_inflow': ff.get('main_in'),
            },
            # 选股专属字段
            'selector_score':       s.get('score'),
            'selector_rating':      s.get('rating'),
            'buy_signals':          s.get('buy_signals', []),
            'stop_loss':            s.get('stop_loss'),
            'take_profit':          s.get('take_profit'),
            'stop_loss_pct':        s.get('stop_loss_pct'),
            'take_profit_pct':      s.get('take_profit_pct'),
            'risk_reward_ratio':    s.get('risk_reward_ratio'),
        }
        stocks_data.append(entry)

    sentiment = {
        'score': None,
        'level': '未知',
        'description': '本次分析来自选股引擎，无实时市场情绪评分',
    }

    use_stream = bool(data.get('stream', False))

    try:
        from gpt_analyst import run_analysis
        if use_stream:
            def generate():
                for chunk in run_analysis(codes, sentiment, stocks_data, stream=True):
                    yield chunk
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
    """
    7维市场情绪评分。
    尝试调用 data/market_sentiment.py（若存在），否则从东方财富全量
    行情缓存快速估算：涨跌比 / 均涨幅 / 涨停率 / 强势股比 / 成交活跃度。
    """
    try:
        from market_sentiment import MarketSentiment
        ms = MarketSentiment()
        result = ms.calculate()
        return jsonify({'status': 'success', 'data': result})
    except ImportError:
        pass
    except Exception as e:
        pass

    # 快速估算：从 stock_cache_db 取当天全量行情
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
        cache.close()

        if len(rows) < 50:
            return jsonify({
                'status': 'success',
                'data': {
                    'score': None, 'level': '数据不足',
                    'description': f'缓存仅 {len(rows)} 条，建议先运行选股或全市场更新',
                    'dimensions': {}
                }
            })

        changes = [r[0] for r in rows if r[0] is not None]
        up   = sum(1 for c in changes if c > 0)
        down = sum(1 for c in changes if c < 0)
        total = len(changes)
        limit_up = sum(1 for c in changes if c >= 9.9)

        up_ratio       = up / total
        avg_change     = sum(changes) / total
        limit_up_rate  = limit_up / total
        strong_ratio   = sum(1 for c in changes if c >= 3) / total

        score = (
            up_ratio       * 30 +
            min(max(avg_change / 5, 0), 1) * 20 +
            min(limit_up_rate / 0.03, 1)   * 20 +
            min(strong_ratio  / 0.15, 1)   * 15 +
            15  # 成交量维度（缓存无量数据，给中性基础分）
        )
        score = round(min(max(score, 0), 100), 1)

        if score >= 75: level = '极度乐观'
        elif score >= 60: level = '乐观'
        elif score >= 50: level = '中性偏多'
        elif score >= 40: level = '中性偏空'
        elif score >= 25: level = '悲观'
        else: level = '极度悲观'

        return jsonify({'status': 'success', 'data': {
            'score': score,
            'level': level,
            'description': f'涨跌比 {up}:{down}，均涨幅 {avg_change:.2f}%，涨停 {limit_up} 只',
            'dimensions': {
                '涨跌比':     round(up_ratio * 100, 1),
                '均涨幅':     round(avg_change, 2),
                '涨停率%':    round(limit_up_rate * 100, 2),
                '强势股比%':  round(strong_ratio * 100, 1),
                '样本数':     total,
            }
        }})
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
        top = fetcher.get_top_lhb_stocks(limit=10)
        if not top:
            # 尝试实时拉取今日龙虎榜
            fetcher.save_lhb_to_cache()
            top = fetcher.get_top_lhb_stocks(limit=10)
        sentiment = fetcher.analyze_lhb_sentiment()
        return jsonify({'status': 'success', 'data': {'top': top, 'sentiment': sentiment}})
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
