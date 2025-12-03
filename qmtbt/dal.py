import sqlite3
import json
import datetime
from typing import Optional
import pandas as pd

class DataAccessLayer:
    def __init__(self, db_path: str = "qmtbt.db"):
        self.db_path = db_path
        self._ensure_db()

    def _ensure_db(self):
        conn = sqlite3.connect(self.db_path)
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

    def _parse_time_str(self, s: str, period: str) -> Optional[int]:
        if not s:
            return None
        if period == "1d":
            dt = datetime.datetime.strptime(s, "%Y%m%d")
        else:
            dt = datetime.datetime.strptime(s, "%Y%m%d%H%M%S")
        return int(dt.timestamp() * 1000)

    def _fetch_from_sqlite(self, symbol: str, period: str, start_time: str = "", end_time: str = "", count: int = -1) -> pd.DataFrame:
        start_ms = self._parse_time_str(start_time, period)
        end_ms = self._parse_time_str(end_time, period)
        conn = sqlite3.connect(self.db_path)
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
            extras = df["extras"].fillna("{}").map(json.loads)
            extra_keys = set()
            for d in extras:
                extra_keys.update(d.keys())
            for k in extra_keys:
                df[k] = extras.map(lambda d: d.get(k))
            df = df.drop(columns=["extras"])
            return df
        finally:
            conn.close()

    def _store_to_sqlite(self, symbol: str, period: str, df: pd.DataFrame):
        base_cols = {"time", "open", "high", "low", "close", "volume", "amount", "lastPrice"}
        conn = sqlite3.connect(self.db_path)
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

    def _ensure_xtdata(self):
        try:
            from xtquant import xtdata
            xtdata.connect()
        except Exception:
            pass

    def _fetch_from_xtquant(self, symbol: str, period: str, start_time: str = "", end_time: str = "", count: int = -1, dividend_type: str = "none") -> pd.DataFrame:
        self._ensure_xtdata()
        from xtquant import xtdata
        try:
            xtdata.download_history_data(stock_code=symbol, period=period, start_time=start_time, end_time=end_time)
        except Exception:
            pass
        res = xtdata.get_market_data_ex(stock_list=[symbol], period=period, start_time=start_time, end_time=end_time, count=count, dividend_type=dividend_type)
        return res[symbol]

    def get_history_data(self, symbol: str, period: str, start_time: str = "", end_time: str = "", count: int = -1, dividend_type: str = "none", fallback_to_xtquant: bool = True) -> pd.DataFrame:
        df = self._fetch_from_sqlite(symbol, period, start_time, end_time, count)
        need_fetch = df.empty
        if count and count > 0 and not df.empty and len(df) < count:
            need_fetch = True
        if need_fetch and fallback_to_xtquant:
            fetched = self._fetch_from_xtquant(symbol, period, start_time, end_time, count, dividend_type)
            if not fetched.empty:
                self._store_to_sqlite(symbol, period, fetched)
                df = self._fetch_from_sqlite(symbol, period, start_time, end_time, count)
            else:
                df = fetched
        return df

