import rpyc
import time
from rpyc.utils.classic import obtain

def main():
    # 连接到 RPC 服务
    # 如果运行在远程机器，请将 'localhost' 替换为服务器 IP
    print("正在连接到 XtQuant RPC 服务...")
    conn = rpyc.connect("localhost", 18812)
    print("连接成功！")
    
    # 动态获取远程模块
    # 由于服务端的动态暴露机制，我们可以直接访问 conn.root 下的任何 xtquant 子模块
    xtdata = conn.root.xtdata
    xttrader = conn.root.xttrader
    
    print("\n--- 测试 1: xtdata 模块 ---")
    stock_code = "000001.SZ"
    
    # 注意: 所有函数都在服务端执行
    print(f"正在服务端下载 {stock_code} 的历史数据...")
    xtdata.download_history_data(stock_code, period='1d', start_time='20240101', end_time='20240110')
    
    print(f"正在获取 {stock_code} 的行情数据...")
    market_data = xtdata.get_market_data(field_list=[], stock_list=[stock_code], period='1d', start_time='20240101', end_time='20240110')
    
    # 将远程数据拉取到本地
    local_data = obtain(market_data)
    print("行情数据 (本地副本):")
    print(local_data)
    
    print("\n--- 测试 2: xttrader 模块 ---")
    try:
        # 在服务端创建交易对象
        # 请根据实际环境修改 path 和 session_id
        path = 'D:\\SimuTrader\\userdata_mini'
        session_id = 123456
        
        print(f"正在服务端创建 XtQuantTrader 实例 (path={path}, session={session_id})...")
        trader = xttrader.XtQuantTrader(path, session_id)
        
        print(f"Trader 实例创建成功: {trader}")
        
    except Exception as e:
        print(f"测试 xttrader 时发生错误 (可能是因为服务端未安装 QMT 或路径错误): {e}")

    conn.close()
    print("\n测试结束，连接已关闭。")

if __name__ == "__main__":
    main()
