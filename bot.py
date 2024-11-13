import nonebot
from loguru import logger   # 使用loguru
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter

default_format = "{time} | {level} | {message}"

nonebot.init()
#  logger.add("trace.log", level="TRACE", format=default_format)
#  上面注释的内容是输出Trace日志到目录下，如果想DEBUG可以取消注释
driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)

nonebot.load_plugins("mybot/plugins")  # 定位插件位置

if __name__ == "__main__":
    nonebot.run()

