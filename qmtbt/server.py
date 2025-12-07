from flask import Flask, request, jsonify
import logging
import sqlite3
import json
import datetime
import pandas as pd
import threading
import time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')
logger = logging.getLogger('qmtbt.server')

def log_request(name, params):
    try:
        logger.info(f"{name} params={json.dumps(params, ensure_ascii=False)}")
    except Exception:
        logger.info(f"{name} params={params}")

def log_response(name, source=None, records=None, extra=None):
    try:
        preview = (records or [])[:3]
        size = len(records or [])
        payload = {'source': source, 'size': size, 'preview': preview}
        if extra is not None:
            payload['extra'] = extra
        
        # 中文日志输出
        if source == 'sqlite':
            source_cn = 'SQLite数据库'
        elif source == 'xtquant':
            source_cn = 'xtquant实时数据'
        elif source == 'xtquant->sqlite':
            source_cn = 'xtquant下载后存入SQLite'
        elif source == 'client':
            source_cn = '客户端上传'
        else:
            source_cn = source or '未知来源'
        
        logger.info(f"{name} 数据来源: {source_cn}, 记录数量: {size}")
        logger.info(f"{name} result={json.dumps(payload, ensure_ascii=False)}")
    except Exception:
        logger.info(f"{name} result source={source} size={len(records or [])}")
DB_PATH = 'qmtbt.db'

def ensure_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_data (
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                time INTEGER NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                lastPrice REAL,
                extras TEXT,
                PRIMARY KEY(symbol, period, time)
            )
            """
        )
    finally:
        conn.close()

ensure_db()

def parse_time_str(s: str, period: str):
    if not s:
        return None
    if period == '1d':
        dt = datetime.datetime.strptime(s, '%Y%m%d')
    else:
        dt = datetime.datetime.strptime(s, '%Y%m%d%H%M%S')
    return int(dt.timestamp() * 1000)

def fetch_from_sqlite(symbol: str, period: str, start_time: str = '', end_time: str = '', count: int = -1) -> pd.DataFrame:
    start_ms = parse_time_str(start_time, period)
    end_ms = parse_time_str(end_time, period)
    conn = sqlite3.connect(DB_PATH)
    try:
        where = ["symbol = ?", "period = ?"]
        params = [symbol, period]
        if start_ms is not None:
            where.append("time >= ?")
            params.append(start_ms)
        if end_ms is not None:
            where.append("time <= ?")
            params.append(end_ms)
        sql = f"SELECT symbol, period, time, open, high, low, close, volume, amount, lastPrice, extras FROM market_data WHERE {' AND '.join(where)} ORDER BY time ASC"
        if count and count > 0:
            sql += f" LIMIT {count}"
        df = pd.read_sql_query(sql, conn, params=params)
        if df.empty:
            return df
        extras = df['extras'].fillna('{}').map(json.loads)
        extra_keys = set()
        for d in extras:
            extra_keys.update(d.keys())
        for k in extra_keys:
            df[k] = extras.map(lambda d: d.get(k))
        df = df.drop(columns=['extras'])
        return df
    finally:
        conn.close()

def store_to_sqlite(symbol: str, period: str, df: pd.DataFrame):
    base_cols = {"time", "open", "high", "low", "close", "volume", "amount", "lastPrice"}
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        rows = []
        for _, row in df.iterrows():
            extras = {}
            for col in df.columns:
                if col not in base_cols and col != "symbol" and col != "period":
                    val = row[col]
                    if pd.notna(val):
                        extras[col] = val
            rows.append((
                symbol,
                period,
                int(row.get("time")),
                row.get("open"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("volume"),
                row.get("amount"),
                row.get("lastPrice"),
                json.dumps(extras) if extras else None,
            ))
        cur.executemany(
            """
            INSERT INTO market_data (symbol, period, time, open, high, low, close, volume, amount, lastPrice, extras)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, period, time) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                amount=excluded.amount,
                lastPrice=excluded.lastPrice,
                extras=excluded.extras
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()

def ensure_xtdata():
    try:
        from xtquant import xtdata
        xtdata.connect()
    except Exception:
        pass

def _normalize_stock_codes(symbol: str) -> list:
    s = (symbol or '').upper().strip()
    if not s:
        return []
    codes = [s]
    if '.' in s:
        parts = s.split('.')
        if len(parts) == 2 and len(parts[0]) == 6 and parts[1] in ('SZ', 'SH'):
            code, exch = parts[0], parts[1]
            codes.append(f"{exch}.{code}")
            codes.append(f"{code}.{exch}")
            codes.append(code)
            # 常见别名
            if exch == 'SZ':
                codes.append(f"SZSE.{code}")
                codes.append(f"XSHE.{code}")
                codes.append(f"{code}.XSHE")
                codes.append(f"SZ{code}")
            elif exch == 'SH':
                codes.append(f"SSE.{code}")
                codes.append(f"XSHG.{code}")
                codes.append(f"{code}.XSHG")
                codes.append(f"SH{code}")
    else:
        if len(s) == 6:
            codes.append(f"SZ.{s}")
            codes.append(f"SH.{s}")
            codes.append(f"{s}.SZ")
            codes.append(f"{s}.SH")
            codes.append(f"SZSE.{s}")
            codes.append(f"XSHE.{s}")
            codes.append(f"{s}.XSHE")
            codes.append(f"SSE.{s}")
            codes.append(f"XSHG.{s}")
            codes.append(f"{s}.XSHG")
            codes.append(f"SZ{s}")
            codes.append(f"SH{s}")
    # 去重并保持顺序
    seen = set()
    normalized = []
    for c in codes:
        if c and c not in seen:
            seen.add(c)
            normalized.append(c)
    return normalized

def fetch_from_xtquant(symbol: str, period: str, start_time: str = '', end_time: str = '', count: int = -1, dividend_type: str = 'none') -> pd.DataFrame:
    from xtquant import xtdata
    ensure_xtdata()
    codes = _normalize_stock_codes(symbol)
    logger.info(f"xtquant查询: 请求标的={symbol}, 尝试代码={codes}")
    for code in codes:
        try:
            xtdata.download_history_data(stock_code=code, period=period, start_time=start_time, end_time=end_time, incrementally=True)
        except Exception:
            continue
        try:
            res = xtdata.get_market_data(stock_list=[code], period=period, start_time=start_time, end_time=end_time, count=count, dividend_type=dividend_type)
            if isinstance(res, dict) and code in res:
                df = res[code]
                if isinstance(df, pd.DataFrame) and not df.empty:
                    if 'symbol' not in df.columns:
                        df['symbol'] = symbol
                    logger.info(f"xtquant命中代码: {code}, 返回记录数={len(df)}")
                    return df
        except Exception:
            continue
        try:
            res2 = xtdata.get_market_data_ex(stock_list=[code], period=period, count=count)
            if isinstance(res2, dict) and code in res2:
                df2 = res2[code]
                if isinstance(df2, pd.DataFrame) and not df2.empty:
                    if 'symbol' not in df2.columns:
                        df2['symbol'] = symbol
                    logger.info(f"xtquant(ex)命中代码: {code}, 返回记录数={len(df2)}")
                    return df2
        except Exception:
            continue
    logger.warning(f"xtquant未获取到数据: 标的={symbol}, 周期={period}")
    return pd.DataFrame()

def get_history_data(symbol: str, period: str, start_time: str = '', end_time: str = '', count: int = -1, dividend_type: str = 'none', fallback: bool = True):
    logger.info(f"请求历史数据: 标的={symbol}, 周期={period}, 开始时间={start_time}, 结束时间={end_time}, 数量={count}, 复权={dividend_type}, 自动下载={fallback}")
    df = fetch_from_sqlite(symbol, period, start_time, end_time, count)
    source = 'sqlite'
    sqlite_count = len(df)
    logger.info(f"SQLite检查: 标的={symbol}, 周期={period}, 找到记录数={sqlite_count}")
    
    need_fetch = df.empty or (count and count > 0 and len(df) < count)
    logger.info(f"数据检查: 标的={symbol}, 周期={period}, 需要下载={need_fetch} (df.empty={df.empty}, 请求数量={count}, 实际数量={sqlite_count})")

    if need_fetch and fallback:
        logger.info(f"正在从xtquant下载: 标的={symbol}, 周期={period}...")
        fetched = fetch_from_xtquant(symbol, period, start_time, end_time, count, dividend_type)
        fetch_count = len(fetched)
        logger.info(f"xtquant下载完成: 标的={symbol}, 周期={period}, 记录数={fetch_count}")

        if not fetched.empty:
            store_to_sqlite(symbol, period, fetched)
            df = fetch_from_sqlite(symbol, period, start_time, end_time, count)
            source = 'xtquant->sqlite'
            logger.info(f"已存入SQLite并重新读取: 标的={symbol}, 周期={period}, 新记录数={len(df)}")
        else:
            df = fetched
            source = 'xtquant'
            logger.info(f"xtquant返回为空或失败，直接返回结果。")
            
    logger.info(f"最终结果: 标的={symbol}, 周期={period}, 来源={source}, 记录数={len(df)}")
    return df, source

SUB_STATE = {
    'running': False,
    'thread': None,
    'codes': set(),
    'period': None,
}

def _run_loop():
    from xtquant import xtdata
    xtdata.run()

def start_run_loop():
    if not SUB_STATE['running']:
        SUB_STATE['running'] = True
        SUB_STATE['thread'] = threading.Thread(target=_run_loop, daemon=True)
        SUB_STATE['thread'].start()

def callback_handler(data, period):
    try:
        codes = list(data.keys())
    except Exception:
        return
    try:
        from xtquant import xtdata
        for code in codes:
            res = xtdata.get_market_data_ex(stock_list=[code], period=period, count=-1)
            df = res[code]
            if isinstance(df, pd.DataFrame) and not df.empty:
                store_to_sqlite(code, period, df)
    except Exception:
        pass

def subscribe_codes(code_list, period, count=-1):
    ensure_xtdata()
    try:
        from xtquant import xtdata
        for code in code_list:
            xtdata.subscribe_quote(code, period=period, count=count, callback=lambda d, p=period: callback_handler(d, p))
            SUB_STATE['codes'].add(code)
        SUB_STATE['period'] = period
        start_run_loop()
        return True
    except Exception:
        return False

def unsubscribe_codes(code_list, period=None):
    try:
        from xtquant import xtdata
        for code in code_list:
            try:
                xtdata.unsubscribe_quote(code, period=period or SUB_STATE['period'])
            except Exception:
                pass
            SUB_STATE['codes'].discard(code)
        return True
    except Exception:
        return False

@app.get('/history')
def history():
    symbol = request.args.get('symbol')
    period = request.args.get('period', '1d')
    start_time = request.args.get('start_time', '')
    end_time = request.args.get('end_time', '')
    count = int(request.args.get('count', '-1'))
    dividend_type = request.args.get('dividend_type', 'none')
    fallback = request.args.get('fallback', 'true').lower() != 'false'
    log_request('/history', {'symbol': symbol, 'period': period, 'start_time': start_time, 'end_time': end_time, 'count': count, 'dividend_type': dividend_type, 'fallback': fallback})
    df, source = get_history_data(symbol=symbol, period=period, start_time=start_time, end_time=end_time, count=count, dividend_type=dividend_type, fallback=fallback)
    records = df.to_dict('records')
    log_response('/history', source=source, records=records)
    return jsonify({
        'symbol': symbol,
        'period': period,
        'records': records
    })

@app.post('/put')
def put():
    payload = request.get_json(force=True)
    log_request('/put', payload)
    symbol = payload.get('symbol')
    period = payload.get('period')
    records = payload.get('records', [])
    df = pd.DataFrame(records)
    if df.empty:
        log_response('/put', source='client', records=[])
        return jsonify({'ok': False, 'message': 'empty records'}), 400
    if 'time' not in df.columns:
        log_response('/put', source='client', records=records, extra={'error': 'missing_time'})
        return jsonify({'ok': False, 'message': 'records must include time(ms)'}), 400
    store_to_sqlite(symbol, period, df)
    log_response('/put', source='sqlite', records=records, extra={'stored': len(df)})
    return jsonify({'ok': True, 'stored': len(df)})

@app.post('/download_history')
def download_history():
    payload = request.get_json(force=True)
    log_request('/download_history', payload)
    code_list = payload.get('code_list', [])
    if not isinstance(code_list, list) or len(code_list) == 0:
        log_response('/download_history', source='client', records=[], extra={'error': 'code_list is required'})
        return jsonify({'ok': False, 'error': 'code_list is required'}), 400
    period = payload.get('period', '1d')
    start_time = payload.get('start_time', '')
    end_time = payload.get('end_time', '')
    incrementally = payload.get('incrementally', True)
    do_financial = payload.get('financial', False)
    do_sector = payload.get('sector', False)
    try:
        from xtquant import xtdata
        ensure_xtdata()
        for code in code_list:
            xtdata.download_history_data(code, period=period, start_time=start_time, end_time=end_time, incrementally=incrementally)
        if do_financial:
            xtdata.download_financial_data(code_list)
        if do_sector:
            xtdata.download_sector_data()
        response = {'ok': True, 'downloaded': len(code_list)}
        log_response('/download_history', source='xtquant', records=[], extra=response)
        return jsonify(response)
    except Exception as e:
        logger.error(f"/download_history error={str(e)}")
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/subscribe')
def subscribe():
    payload = request.get_json(force=True)
    log_request('/subscribe', payload)
    code_list = payload.get('code_list', [])
    period = payload.get('period', '1d')
    count = int(payload.get('count', '-1'))
    ok = subscribe_codes(code_list, period, count=count)
    result = {'ok': ok, 'codes': list(SUB_STATE['codes']), 'period': SUB_STATE['period']}
    log_response('/subscribe', source='xtquant/live', records=[], extra=result)
    return jsonify(result)

@app.post('/unsubscribe')
def unsubscribe():
    payload = request.get_json(force=True)
    log_request('/unsubscribe', payload)
    code_list = payload.get('code_list', [])
    period = payload.get('period', None)
    ok = unsubscribe_codes(code_list, period)
    result = {'ok': ok, 'codes': list(SUB_STATE['codes'])}
    log_response('/unsubscribe', source='xtquant/live', records=[], extra=result)
    return jsonify(result)

@app.get('/status')
def status():
    result = {'running': SUB_STATE['running'], 'codes': list(SUB_STATE['codes']), 'period': SUB_STATE['period']}
    log_request('/status', {})
    log_response('/status', source='internal', records=[], extra=result)
    return jsonify(result)

@app.get('/index_weight')
def index_weight():
    index_code = request.args.get('index_code')
    log_request('/index_weight', {'index_code': index_code})
    if not index_code:
        return jsonify({'ok': False, 'error': 'index_code is required'}), 400
    
    try:
        from xtquant import xtdata
        ensure_xtdata()
        weights = xtdata.get_index_weight(index_code)
        if not weights:
            logger.info(f"index_weight locally missing, downloading... index_code={index_code}")
            xtdata.download_index_weight()
            weights = xtdata.get_index_weight(index_code)
            
        if weights is None:
            weights = {}
        log_response('/index_weight', source='xtquant', records=list(weights.keys()), extra={'count': len(weights)})
        return jsonify({'ok': True, 'index_code': index_code, 'weights': weights})
    except Exception as e:
        logger.error(f"/index_weight error={str(e)}")
        return jsonify({'ok': False, 'error': str(e)}), 500

def run(host: str = '0.0.0.0', port: int = 8000):
    app.run(host=host, port=port)

if __name__ == '__main__':
    run()
