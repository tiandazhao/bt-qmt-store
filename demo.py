import backtrader as bt
from qmtbt import QMTStore
from datetime import datetime
import os
import math
import matplotlib
matplotlib.use('Agg')
from matplotlib import rcParams
rcParams['font.sans-serif'] = ['PingFang SC', 'Hiragino Sans GB', 'Arial Unicode MS', 'Microsoft YaHei', 'SimHei']
rcParams['axes.unicode_minus'] = False

class BuyCondition(bt.Indicator):
    '''买入条件'''
    lines = ('buy_signal',)
    plotinfo = dict(subplot=True, plot=True)
    plotname = '买入条件'
    plotlines = dict(buy_signal=dict(_name='买入信号'))

    params = (
        ('up_days', 10),  # 连续上涨的天数
    )

    def __init__(self):
        self.addminperiod(self.params.up_days + 2)

    def next(self):
        # 检查斜率是否恰好连续向上 up_days 个交易日，再往前一个交易日斜率下降
        if len(self) >= self.params.up_days + 2:
            slope_up = all(self.data.close[-i] > self.data.close[-i-1] for i in range(1, self.params.up_days + 1))
            slope_down_before = self.data.close[-self.params.up_days - 1] < self.data.close[-self.params.up_days - 2]
            if slope_up and slope_down_before:
                self.lines.buy_signal[0] = 1
            else:
                self.lines.buy_signal[0] = 0

class SellCondition(bt.Indicator):
    '''卖出条件'''
    lines = ('sell_signal',)
    plotinfo = dict(subplot=True, plot=True)
    plotname = '卖出条件'
    plotlines = dict(sell_signal=dict(_name='卖出信号'))

    params = (
        ('hold_days', 20),  # 持有天数
    )

    def __init__(self):
        self.hold_days = 0

    def next(self):
        # 持有self.params.hold_days个交易日卖出
        if self.hold_days >= self.params.hold_days:
            self.lines.sell_signal[0] = 1
            self.hold_days = 0
        else:
            self.lines.sell_signal[0] = 0
            self.hold_days += 1

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
    '''仓位控制'''
    params = (
        ('buy_count', 1),   # 最大持仓股票个数
    )

    def __init__(self):
        pass

    def _getsizing(self, comminfo, cash, data, isbuy):
        if isbuy:
            # 如果是买入，平均分配仓位
            commission_rate = comminfo.p.commission
            size = math.floor(cash * (1 - commission_rate) / data.close[0] / self.params.buy_count / 100) * 100
        else:
            # 如果是卖出，全部卖出
            position = self.broker.getposition(data)
            size = position.size

        return size

class DemoStrategy(bt.Strategy):
    params = (
        ('max_positions', 5),
        ('up_days', 10),
        ('hold_days', 20),
        ('ma_fast', 20),
        ('ma_slow', 60),
        ('rsi_period', 14),
        ('rsi_buy', 50),
        ('stop_loss', 0.08),
        ('take_profit', 0.2),
    )

    def log(self, txt, dt=None):
        """ 记录交易日志 """
        dt = dt or self.datas[0].datetime.date(0)
        print(f'{dt.isoformat()}, {txt}')

    def __init__(self):
        self.sizer = Sizer()
        self.buy_condition = {d: BuyCondition(d, up_days=self.params.up_days) for d in self.datas}
        self.sma_fast = {d: bt.indicators.SimpleMovingAverage(d.close, period=self.params.ma_fast) for d in self.datas}
        self.sma_slow = {d: bt.indicators.SimpleMovingAverage(d.close, period=self.params.ma_slow) for d in self.datas}
        self.rsi = {d: bt.indicators.RSI(d.close, period=self.params.rsi_period) for d in self.datas}
        self.hold_counter = {d: 0 for d in self.datas}
        self.entry_price = {d: None for d in self.datas}

    def next(self):
        buy_list = []
        sell_list = []

        for i, d in enumerate(self.datas):
            pos = self.getposition(d).size

            if pos:
                self.hold_counter[d] += 1
                ep = self.entry_price[d] or d.close[0]
                stoploss = d.close[0] <= ep * (1 - self.params.stop_loss)
                takeprofit = d.close[0] >= ep * (1 + self.params.take_profit)
                trendexit = d.close[0] < self.sma_fast[d][0]
                timeexit = self.hold_counter[d] >= self.params.hold_days
                if stoploss or takeprofit or trendexit or timeexit:
                    sell_list.append(d)
            else:
                self.hold_counter[d] = 0
                cond = self.buy_condition[d].lines.buy_signal[0] > 0
                trend = d.close[0] > self.sma_slow[d][0]
                momentum = self.rsi[d][0] > self.params.rsi_buy
                if cond and trend and momentum:
                    buy_list.append(d)

        # 动态设置Sizer的buy_count参数
        current_positions = sum(1 for dd in self.datas if self.getposition(dd).size)
        available = max(0, self.params.max_positions - current_positions)
        self.sizer.params.buy_count = max(1, min(len(buy_list), available) or 1)

        # 先执行卖出操作
        for d in sell_list:
            self.sell(data=d)
            self.entry_price[d] = None
            self.hold_counter[d] = 0

        # 再执行买入操作
        slots = available
        for d in buy_list:
            if slots <= 0:
                break
            self.buy(data=d)
            self.entry_price[d] = d.close[0]
            self.hold_counter[d] = 0
            slots -= 1

        
if __name__ == '__main__':
    
    # 支持从环境变量传入远端服务地址
    server_url = os.getenv('QMTBT_SERVER_URL')
    store = QMTStore(server_url=server_url) if server_url else QMTStore()
    print(f"Using server: {getattr(store, 'server_url', 'N/A')}")

    # 优先使用 xtquant 获取沪深300成分股；若不可用则使用备用列表
    try:
        from xtquant import xtdata
        code_list = xtdata.get_stock_list_in_sector('沪深300')
    except Exception:
        code_list = ['000001.SZ', '600000.SH', '600519.SH']

    # 添加数据
    datas = store.getdatas(code_list=code_list, timeframe=bt.TimeFrame.Days, fromdate=datetime(2022, 7, 1))

    os.makedirs('plots', exist_ok=True)
    for d in datas:
        cerebro = bt.Cerebro(stdstats=False, maxcpus=16)
        cerebro.adddata(d)
        cerebro.addstrategy(DemoStrategy)
        cerebro.addobserver(BuySellCN)
        cerebro.addobserver(TradesCN)
        cerebro.addobserver(ValueCN)
        cerebro.addsizer(Sizer)
        cerebro.broker.setcash(1000000.0)
        cerebro.broker.setcommission(commission=0.001)
        cerebro.run()
        if cerebro.broker.getvalue() != 1000000.0:
            print('Final Portfolio Value: %.2f' % cerebro.broker.getvalue())
        figs = cerebro.plot(iplot=False)
        def _flatten(items):
            for it in items:
                if isinstance(it, (list, tuple)):
                    yield from _flatten(it)
                else:
                    yield it
        name = getattr(d.p, 'dataname', 'data')
        for idx, fig in enumerate(_flatten(figs)):
            out = os.path.join('plots', f'{name}{"" if idx == 0 else f"_{idx}"}.png')
            fig.savefig(out, bbox_inches='tight')

    # data.test(1)
    # data.test(2)
    # data.test(3)
    # data.test(4)
    # xtdata.run()

    # 绘制结果
    # cerebro.plot()
