from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.event.filter import PermissionType
from astrbot.api import logger
from astrbot.api import AstrBotConfig
from gotify import AsyncGotify
from gotify.response_types import Message
import asyncio
import requests
import json

from astrbot.core.message.message_event_result import MessageChain


@register(
    "astrbot_plugin_gotify",
    "BetaCat",
    "此插件可以监听Gotify的消息，并推送给你的机器人",
    "1.0.1",
)
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.context = context
        self.server = config.get("server")
        self.token = config.get("token")
        self.monitor_app_name = set(config.get("application") or [])
        self.chat_id = list(config.get("chat_id") or [])
        self.chat_newserver = config.get("chat_newserver")
        self.gotify: AsyncGotify = AsyncGotify(
            base_url=self.server, client_token=self.token
        )

        self.cache_app = {}  # dict{id: application}

    async def update_applications(self):
        """更新应用列表"""
        applications = await self.gotify.get_applications()
        self.cache_app = {app.get("id"): app for app in applications if "id" in app}

    async def initialize(self):
        """获取要监听的App。"""
        self.listen_task = asyncio.create_task(self.start_listen())
        logger.info("插件初始化完成")

    async def handle_message(self, msg: Message):
        """处理收到的消息"""
        # 确保appid已记录
        if not self.cache_app.get(msg.get("appid")):
            await self.update_applications()
            # 重新获取应用列表
            if not self.cache_app.get(msg.get("appid")):
                logger.info(f"appid {msg.get('appid')} 不在应用列表中")

        # 获取应用名称
        appname = self.cache_app.get(msg.get("appid")).get("name")

        # 设置了监听的app
        if self.monitor_app_name:
            if appname not in self.monitor_app_name:
                logger.info(f"未监听的App: {msg.get('appname')}")
                return

        for chat_id in self.chat_id:
            sendMsg = MessageChain().message(
                f"{msg.get('title')}\n{msg.get('message')}"
            )
            await self.context.send_message(chat_id, sendMsg)

    async def start_listen(self):
        """开始监听 Gotify 消息的异步方法，掉线时尝试重连"""
        while True:
            received: int = 0
            wechat = "Bot已离线！"
            try:
                async for msg in self.gotify.stream():
                    logger.info(msg)
                    wechat = f"{msg.get('title')}\n{msg.get('message')}"
                    received = received + 1
                    await self.handle_message(msg)

            except Exception as e:
                logger.error(f"Gotify 连接断开，已收到的消息 {received}，尝试重连: {e}")
                if self.chat_newserver:
                    requests.post(self.chat_newserver, json={
                        "msgtype": "text",
                        "text": {
                            "content":wechat
                        }
                    })
                    logger.error(f"由于 Gotify 连接断开，本次已收到的消息已转发给 WeChat")
            if received == 0:
                await asyncio.sleep(60)  # 等待 1 分钟后重连
        pass

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("开启转发")
    async def gotify_register_chat(self, event: AstrMessageEvent):
        session_id = event.unified_msg_origin
        self.chat_id.append(session_id)
        self.chat_id = list(set(self.chat_id))  # 去重
        self.config["chat_id"] = self.chat_id
        self.config.save_config()
        logger.info(f"已从转发ID开启会话: {session_id}")
        yield event.plain_result("当前会话开启 转发通道 成功！")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("关闭转发")
    async def gotify_unregister_chat(self, event: AstrMessageEvent):
        session_id = event.unified_msg_origin
        if session_id in self.chat_id:
            self.chat_id.remove(session_id)
            self.config["chat_id"] = self.chat_id
            self.config.save_config()
            logger.info(f"已从转发ID关闭会话: {session_id}")
            yield event.plain_result("当前会话关闭 转发通道 成功！")
        else:
            yield event.plain_result("当前会话关闭 转发通道 成功！")
    
    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("转发列表")
    async def gotify_register_lists(self, event: AstrMessageEvent):
        # 定义格式化函数：解析 GroupMessage:123456 → 群聊:123456(群名)
        async def format_item(item: str):
            try:
                # 分割字符串：[昵称, 消息类型, ID]
                _, msg_type, target_id = item.split(':')
                # 转换类型名称
                type_text = "群聊" if msg_type == "GroupMessage" else "私聊"
                
                # 获取对应名称
                if type_text == "群聊":
                    # 获取群名（正常可用）
                    group_info = await event.bot.get_group_info(group_id=int(target_id))
                    name = group_info.get("group_name", "未知群")
                else:
                    # ✅ 修复：获取机器人【好友】的信息（备注优先 + 昵称兜底）
                    friend_list = await event.bot.get_friend_list()
                    name = "未知好友"
                    for friend in friend_list:
                        if str(friend.get("user_id")) == target_id:
                            # 优先取备注，没有备注取昵称
                            name = friend.get("remark") or friend.get("nickname")
                            break
                
                # 拼接最终格式
                return f"{type_text}:{target_id}({name})"
            except Exception as e:
                # 解析失败强制返回未知，绝对不会空
                return f"{type_text}:{target_id}(未知)"

        # 处理通道
        formatted_list = []
        for item in self.chat_id:
            formatted_list.append(await format_item(item))
        list = "\n".join(formatted_list) if formatted_list else "无"

        # 最终消息
        result = (
            f"转发列表：\n{list}"
        )
        yield event.plain_result(result)
    
    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        if hasattr(self, "listen_task") and not self.listen_task.done():
            logger.info("Gotify 连接关闭")
            self.listen_task.cancel()
