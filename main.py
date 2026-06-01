from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.event.filter import PermissionType
from astrbot.api import logger
from astrbot.api import AstrBotConfig
from gotify import AsyncGotify
from gotify.response_types import Message
import asyncio
import aiohttp
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
        self.bindings = list(config.get("bindings") or [])
        self.backup_forward_server = config.get("backup_forward_server")
        self.backup_forward_format = config.get("backup_forward_format")
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
                return

        # 获取应用名称
        appname = self.cache_app.get(msg.get("appid")).get("name")
        title = msg.get('title', '')
        content = msg.get('message', '')
        sendMsg = MessageChain().message(f"{title}\n{content}")

        # 遍历绑定的列表
        for binding in self.bindings:
            try:
                # 分割配置：会话ID(含冒号) + 绑定的应用名
                *session_parts, bind_appname = binding.split(':')
                session_id = ':'.join(session_parts)
            except Exception as e:
                logger.error(f"绑定配置解析失败: {binding}, 错误: {e}")
                continue

            # 应用名匹配 → 转发消息
            if bind_appname == appname:
                await self.context.send_message(session_id, sendMsg)

    async def start_listen(self):
        """开始监听 Gotify 消息的异步方法，掉线时尝试重连"""
        while True:
            received: int = 0
            backup_forward_title = "Gotify转发 出现异常"
            backup_forward_message = "说明：Gotify断开连接，将在1分钟后尝试重连"
            try:
                async for msg in self.gotify.stream():
                    logger.info(msg)
                    backup_forward_title = "Gotify转发 可能遗漏"
                    backup_forward_message = f"标题：{msg.get('title', 'title获取错误')}"
                    received = received + 1
                    await self.handle_message(msg)

            except Exception as e:
                logger.error(f"Gotify 连接断开，已收到的消息 {received}，尝试重连: {e}")
                if self.backup_forward_server and self.backup_forward_format:
                    try:
                        backup_forward_str = self.backup_forward_format.format(
                            title=backup_forward_title,
                            message=backup_forward_message
                        )
                        backup_forward_data = json.loads(backup_forward_str)
                        async with aiohttp.ClientSession() as session:
                            await session.post(
                                self.backup_forward_server,
                                json=backup_forward_data
                            )
                        logger.error(f"由于 Gotify 连接断开，消息已转发给 备用消息服务器")
                    except Exception as ee:
                        logger.error(f"转发给 备用消息服务器 失败: {ee}")
            if received == 0:
                await asyncio.sleep(60)  # 等待 1 分钟后重连
        pass

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("转发绑定")
    async def gotify_binding_chat(self, event: AstrMessageEvent, app_name: str = ""):
        if not app_name:
            yield event.plain_result("此指令缺少参数appname！")
            return
        
        session_id = event.unified_msg_origin
        binding_item = f"{session_id}:{app_name}"  

        if binding_item not in self.bindings:
            self.bindings.append(binding_item)
            self.config["bindings"] = self.bindings
            self.config.save_config()
            logger.info(f"会话: {session_id} 绑定 {app_name} 成功！")
            yield event.plain_result(f"当前会话绑定【{app_name}】成功！")
        else:
            yield event.plain_result(f"当前会话已绑定【{app_name}】！无法重复绑定！")
            
    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("转发解绑")
    async def gotify_unbinding_chat(self, event: AstrMessageEvent, app_name: str = ""):
        if not app_name:
            yield event.plain_result("此指令缺少参数appname！")
            return
        
        session_id = event.unified_msg_origin
        unbinding_item = f"{session_id}:{app_name}"

        if unbinding_item in self.bindings:
            self.bindings.remove(unbinding_item)
            self.config["bindings"] = self.bindings
            self.config.save_config()
            logger.info(f"会话: {session_id} 解绑 {app_name} 成功！")
            yield event.plain_result(f"当前会话解绑【{app_name}】成功！")
        else:
            yield event.plain_result(f"当前会话未绑定【{app_name}】！无法重复解绑！")
    
    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("转发列表")
    async def gotify_binding_lists(self, event: AstrMessageEvent):
        async def optimize_item(session_str: str, bind_appname: str):
            try:
                # 分割字符串：[昵称, 消息类型, ID]
                _, msg_type, target_id = session_str.split(':')
                # 转换类型名称
                type_text = "群聊" if msg_type == "GroupMessage" else "私聊"
                
                if type_text == "群聊":
                    group_info = await event.bot.get_group_info(group_id=int(target_id))
                    name = group_info.get("group_name", "未知群")
                else:
                    friend_list = await event.bot.get_friend_list()
                    name = "未知好友"
                    for friend in friend_list:
                        if str(friend.get("user_id")) == target_id:
                            name = friend.get("remark") or friend.get("nickname")
                            break
                # 拼接最终格式            
                return f"{type_text}:{target_id}({name}):{bind_appname}"
            except Exception as e:
                return f"{session_str}:{bind_appname}"

        forward_list = []
        for binding in self.bindings:
            try:
                *session_parts, bind_appname = binding.split(':')
                session_id = ':'.join(session_parts)
                forward_list.append(await optimize_item(session_id, bind_appname))
            except:
                continue

        forwardlist_str = "\n".join(forward_list) if forward_list else "无"
        result = f"转发列表：\n{forwardlist_str}"
        yield event.plain_result(result)
    
    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        if hasattr(self, "listen_task") and not self.listen_task.done():
            logger.info("Gotify 连接关闭")
            self.listen_task.cancel()
