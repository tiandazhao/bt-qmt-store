import os
import requests
from backtrader.metabase import MetaParams
import backtrader as bt
import pandas as pd


class MetaSingleton(MetaParams):
    '''Metaclass to make a metaclassed class a singleton'''

    def __init__(cls, name, bases, dct):
        super(MetaSingleton, cls).__init__(name, bases, dct)
        cls._singleton = None

    def __call__(cls, *args, **kwargs):
        if cls._singleton is None:
            cls._singleton = (
                super(MetaSingleton, cls).__call__(*args, **kwargs))

        return cls._singleton


class QMTStore(object, metaclass=MetaSingleton):
    
    def getdata(self, *args, **kwargs): 
        '''Returns ``DataCls`` with args, kwargs'''
        kwargs['store'] = self
        if not hasattr(self.__class__, 'DataCls') or self.__class__.DataCls is None:
            try:
                from .qmtfeed import QMTFeed
                self.__class__.DataCls = QMTFeed
            except Exception:
                raise AttributeError('DataCls is not registered')
        qmtFeed = self.__class__.DataCls(*args, **kwargs)
        return qmtFeed
    
    def getdatas(self, *args, **kwargs):
        '''Returns ``DataCls`` with *args, **kwargs (multiple entries)'''
        return [self.getdata(*args, **{**kwargs, 'dataname': stock}) for stock in kwargs.pop('code_list', 1)]
    
    def setdatas(self, cerebro, datas):
        '''Set the datas'''
        for data in datas:
            cerebro.adddata(data)

    def getbroker(self, *args, **kwargs):
        '''Returns broker with *args, **kwargs from registered ``BrokerCls``'''
        return self.__class__.BrokerCls(*args, **kwargs)
    
    def __init__(self, server_url=None, **kwargs):
        self.server_url = server_url or os.getenv('QMTBT_SERVER_URL', 'http://192.168.100.110:8000')

    # live subscribe helpers used by QMTFeed

    def _auto_expand_array_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Write by ChatGPT4

        Automatically identify and expand DataFrame columns containing array values.

        Returns:
        - A new DataFrame with the expanded columns.
        """
        for col in df.columns:
            if df[col].apply(lambda x: isinstance(x, (list, tuple))).all():
                # Expand the array column into a new DataFrame
                expanded_df = df[col].apply(pd.Series)
                # Generate new column names
                expanded_df.columns = [f"{col}{i+1}" for i in range(expanded_df.shape[1])]
                # Drop the original column and join the expanded columns
                df = df.drop(col, axis=1).join(expanded_df)
        return df

    def _fetch_history(self, symbol, period, start_time='', end_time='', count=-1, dividend_type='none', download=True):
        """
        通过 HTTP 从 server.py 提供的服务获取历史数据。

        参数：
            symbol: 标的代码
            period: 周期 ('1d'/'1m'/'tick')
            start_time: 起始日期字符串（按 period 规则格式化）
            end_time: 结束日期字符串（按 period 规则格式化）
            count: 返回数量限制，-1 表示不限制
            dividend_type: 复权类型
            download: 是否允许服务端回退到 xtquant 下载缺失数据（映射为 fallback）
        """
        params = {
            'symbol': symbol,
            'period': period,
            'start_time': start_time or '',
            'end_time': end_time or '',
            'count': str(count if count is not None else -1),
            'dividend_type': dividend_type or 'none',
            'fallback': 'true' if download else 'false',
        }
        url = self.server_url.rstrip('/') + '/history'

        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            # 请求失败时返回空 DataFrame
            return pd.DataFrame()

        records = payload.get('records', [])
        df = pd.DataFrame(records)
        if period == 'tick' and not df.empty:
            df = self._auto_expand_array_columns(df)
        return df
    
    def _subscribe_live(self, symbol, period, callback, start_time='', end_time=''):
        from xtquant import xtdata
        seq = xtdata.subscribe_quote(stock_code=symbol, period=period, start_time=start_time, end_time=end_time, callback=callback)

        return seq

    
    def _unsubscribe_live(self, seq):
        try:
            from xtquant import xtdata
            xtdata.unsubscribe_quote(seq)
        except Exception:
            pass
