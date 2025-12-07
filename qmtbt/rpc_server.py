import rpyc
from rpyc.utils.server import ThreadedServer
import logging
import sys
import os
import functools
import types
import importlib

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')
logger = logging.getLogger('qmtbt.rpc_server')

# Try to import xtquant package
try:
    import xtquant
except ImportError:
    logger.warning("导入 xtquant 失败。请确保环境中已安装该模块。")
    xtquant = None

def log_call(func, module_name, func_name, client_info):
    """Decorator to log function calls with arguments and return values."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        arg_str = f"args={args}, kwargs={kwargs}"
        # Truncate long arguments for logging clarity
        if len(arg_str) > 500:
            arg_str = arg_str[:500] + "..."
            
        logger.info(f"[{client_info}] 调用 {module_name}.{func_name} | 参数: {arg_str}")
        
        try:
            result = func(*args, **kwargs)
            
            res_str = str(result)
            if len(res_str) > 200:
                res_str = res_str[:200] + "..."
            logger.info(f"[{client_info}] 返回 {module_name}.{func_name} | 结果: {res_str}")
            return result
        except Exception as e:
            logger.error(f"[{client_info}] 错误 {module_name}.{func_name} | 异常: {e}")
            raise
    return wrapper

class LoggingWrapper:
    """
    Wraps a module or object to intercept and log attribute access and method calls.
    """
    def __init__(self, obj, name, client_info):
        self._obj = obj
        self._name = name
        self._client_info = client_info

    def __getattr__(self, name):
        attr = getattr(self._obj, name)
        
        # If it's a callable (function/method), wrap it
        if callable(attr) or isinstance(attr, (types.FunctionType, types.MethodType, types.BuiltinFunctionType, types.BuiltinMethodType)):
            return log_call(attr, self._name, name, self._client_info)
        
        # If it's a class (like xttrader.XtQuantTrader), we want to wrap its instantiation
        if isinstance(attr, type):
            # Create a wrapper class that logs instantiation
            class WrappedClass(attr):
                def __init__(inner_self, *args, **kwargs):
                    log_call(super().__init__, self._name, f"{name}.__init__", self._client_info)(*args, **kwargs)
                
                # We could potentially wrap methods of the instance here too, 
                # but that might get too complex. For now, let's stick to module-level function calls.
            return WrappedClass
            
        return attr
    
    def __repr__(self):
        return f"<LoggingWrapper for {self._name}>"

class XtQuantService(rpyc.Service):
    """
    RPyC Service for xtquant.
    Dynamically exposes all xtquant submodules and attributes to remote clients.
    """
    
    def on_connect(self, conn):
        self.client_info = f"{conn._config['endpoints'][1][0]}:{conn._config['endpoints'][1][1]}"
        logger.info(f"客户端已连接: {self.client_info}")
        # Cache for resolved attributes to improve performance
        self._cache = {}

    def on_disconnect(self, conn):
        logger.info(f"客户端已断开: {self.client_info}")
        self._cache = {}

    def __getattr__(self, name):
        """
        Dynamically resolve attributes from the xtquant package.
        This allows clients to access conn.root.xtdata, conn.root.xttrader, etc.
        without explicit definition.
        """
        # Ignore private attributes or RPyC internal attributes
        if name.startswith('_'):
            raise AttributeError(name)
            
        # Check cache first
        if name in self._cache:
            return self._cache[name]

        if xtquant is None:
             raise AttributeError("xtquant package is not available on server.")

        # 1. Try to get attribute from xtquant package directly
        if hasattr(xtquant, name):
            val = getattr(xtquant, name)
            wrapper = LoggingWrapper(val, f"xtquant.{name}", self.client_info)
            self._cache[name] = wrapper
            return wrapper
        
        # 2. Try to import as a submodule (e.g., xtquant.xtdata)
        try:
            mod = importlib.import_module(f"xtquant.{name}")
            wrapper = LoggingWrapper(mod, f"xtquant.{name}", self.client_info)
            self._cache[name] = wrapper
            return wrapper
        except ImportError:
            pass
            
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

def main():
    if xtquant is None:
        logger.error("xtquant 模块不可用。服务可能无法正常工作。")

    # Configuration
    PORT = 18812
    
    # Protocol configuration to allow accessing attributes and methods
    protocol_config = {
        'allow_public_attrs': True,
        'allow_all_attrs': True,
        'allow_pickle': True,
        'allow_getattr': True,
        'allow_setattr': True,
        'allow_delattr': True,
        'import_custom_exceptions': True,
        'instantiate_custom_exceptions': True,
        'instantiate_oldstyle_exceptions': True,
    }

    server = ThreadedServer(
        XtQuantService, 
        port=PORT, 
        protocol_config=protocol_config,
        auto_register=False
    )
    
    logger.info(f"正在启动 XtQuant RPC 服务，端口: {PORT}...")
    logger.info("如果需要使用交易功能，请确保 QMT/MiniQMT 已运行。")
    logger.info("该服务将动态暴漏 xtquant 包下的所有模块。")
    
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("正在停止服务...")
        server.close()

if __name__ == "__main__":
    main()
