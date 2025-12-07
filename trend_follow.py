import backtrader as bt
from qmtbt import QMTStore
from datetime import datetime
import os
import matplotlib
matplotlib.use('Agg')
from matplotlib import rcParams
rcParams['font.sans-serif'] = ['PingFang SC', 'Hiragino Sans GB', 'Arial Unicode MS', 'Microsoft YaHei', 'SimHei']
rcParams['axes.unicode_minus'] = False

class BuySellCN(bt.observers.BuySell):
    plotlines = dict(buy=dict(_name='买入'), sell=dict(_name='卖出'))
    def plotlabel(self):
        return '买卖信号'

class TradesCN(bt.observers.Trades):
    plotname = '交易盈亏'
    plotlines = dict(positive=dict(_name='盈利'), negative=dict(_name='亏损'))
    def plotlabel(self):
        return '交易盈亏'

class ValueCN(bt.observers.Value):
    plotname = '资金/净值'
    plotlines = dict(cash=dict(_name='现金'), value=dict(_name='净值'))
    def plotlabel(self):
        return '资金/净值'

class Sizer(bt.Sizer):
    params = (('buy_count', 1),)
    def _getsizing(self, comminfo, cash, data, isbuy):
        if isbuy:
            commission_rate = comminfo.p.commission
            return int((cash * (1 - commission_rate) / data.close[0] / self.params.buy_count) // 100) * 100
        else:
            return self.broker.getposition(data).size

class TrendFollowStrategy(bt.Strategy):
    params = (
        ('ma_fast', 20),
        ('ma_slow', 60),
        ('adx_period', 14),
        ('adx_threshold', 25),
        ('max_positions', 10),
        ('stop_loss', 0.08),
        ('take_profit', 0.2),
    )

    def __init__(self):
        self.sma_fast = {d: bt.indicators.SimpleMovingAverage(d.close, period=self.params.ma_fast) for d in self.datas}
        self.sma_slow = {d: bt.indicators.SimpleMovingAverage(d.close, period=self.params.ma_slow) for d in self.datas}
        self.adx = {d: bt.indicators.ADX(d, period=self.params.adx_period) for d in self.datas}
        self.entry_price = {d: None for d in self.datas}

    def next(self):
        buy_list, sell_list = [], []
        for d in self.datas:
            pos = self.getposition(d).size
            fast = self.sma_fast[d][0]
            slow = self.sma_slow[d][0]
            trend = d.close[0] > slow and fast > slow and self.adx[d][0] >= self.params.adx_threshold
            if pos:
                ep = self.entry_price[d] or d.close[0]
                stoploss = d.close[0] <= ep * (1 - self.params.stop_loss)
                takeprofit = d.close[0] >= ep * (1 + self.params.take_profit)
                lose_trend = not trend
                if stoploss or takeprofit or lose_trend:
                    sell_list.append(d)
            else:
                if trend:
                    buy_list.append(d)

        current_positions = sum(1 for dd in self.datas if self.getposition(dd).size)
        available = max(0, self.params.max_positions - current_positions)

        for d in sell_list:
            self.sell(data=d)
            self.entry_price[d] = None

        slots = available
        for d in buy_list:
            if slots <= 0:
                break
            self.buy(data=d)
            self.entry_price[d] = d.close[0]
            slots -= 1

if __name__ == '__main__':
    server_url = os.getenv('QMTBT_SERVER_URL')
    store = QMTStore(server_url=server_url) if server_url else QMTStore()
    try:
        from xtquant import xtdata
        code_list = xtdata.get_stock_list_in_sector('沪深300')
    except Exception:
        code_list = ['000001.SZ', '600000.SH', '600519.SH']

    datas = store.getdatas(code_list=code_list, timeframe=bt.TimeFrame.Days, fromdate=datetime(2022, 7, 1))

    os.makedirs('plots', exist_ok=True)
    cerebro = bt.Cerebro(stdstats=False, maxcpus=16)
    for d in datas:
        cerebro.adddata(d)

    cerebro.addstrategy(TrendFollowStrategy)
    cerebro.addobserver(BuySellCN)
    cerebro.addobserver(TradesCN)
    cerebro.addobserver(ValueCN)
    cerebro.addsizer(Sizer)

    cerebro.broker.setcash(1000000.0)
    cerebro.broker.setcommission(commission=0.001)

    cerebro.run()
    figs = cerebro.plot(iplot=False)
    def _flatten(items):
        for it in items:
            if isinstance(it, (list, tuple)):
                yield from _flatten(it)
            else:
                yield it
    for idx, fig in enumerate(_flatten(figs)):
        out = os.path.join('plots', f'HS300_trend{"" if idx == 0 else f"_{idx}"}.png')
        fig.savefig(out, bbox_inches='tight')
