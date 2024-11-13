from nonebot import get_driver
from pydantic import BaseModel

class Config(BaseModel):
    oachat_on_command: str
    api_url: str
    openai_api_key: str
    cloudflare_api_key: str
    cloudflare_account_id: str
    openai_max_tokens: int
    oachat_queue_size_group: int  # 群聊消息队列条数
    oachat_queue_size_private: int  # 私聊消息队列条数

config = Config.parse_obj(get_driver().config.dict())

#  这里是一个用于读取配置文件的部分，这里的配置文件是通过nonebot2的config.toml来配置的，也没什么好改的
