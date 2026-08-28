import asyncio
import base64
import json
import secrets
import struct
import time

import httpx
from components import SETTINGS, get_logger, session_scope
from modules.channels import ChannelBinding, ChannelPeer
from sqlalchemy import select

from ..base import ChannelAdapter, ChannelBindingSnapshot, ChannelError, InboundMessage, OnInbound
from ..registry import register
from ..state import update_binding_status

logger = get_logger(__name__)

# 微信个人号 Bot API（iLink / ClawBot 协议）基址；确认登录后返回的 baseurl 可能不同，优先用返回值。
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com/"
# 通用请求头与 base_info：channel_version 需跟随官方 iLink SDK 演进（omp-wechat 同源值 2.2.0）。
CHANNEL_VERSION = "2.2.0"
BOT_AGENT = "SpiritAgent/1.0.0"
# 会话过期错误码（getupdates / sendmessage / getconfig 均可能返回；恢复手段只有重新扫码）。
SESSION_EXPIRED = -14

QR_POLL_INTERVAL_SECONDS = 3.0
QR_LOGIN_TIMEOUT_SECONDS = 300.0
# 常规请求（sendmessage/getconfig/登录）默认超时；getupdates 长轮询按配置放宽。
REQUEST_TIMEOUT_SECONDS = 15.0
# typing_ticket 由 getconfig 下发，缓存 ~20h（略短于服务端 24h TTL，避免临界过期）。
TYPING_TICKET_TTL_SECONDS = 20 * 3600


class IlinkSessionExpired(Exception):
    """iLink 会话过期（-14）的内部信号：run 循环据此清凭据转 login_required，不算适配器故障。"""


def _base_info() -> dict:
    return {"channel_version": CHANNEL_VERSION, "bot_agent": BOT_AGENT}


def _random_wechat_uin() -> str:
    uint32 = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(uint32).encode()).decode()


def _extract_text(item_list: list) -> str:
    """入站 item 列表归一化为文本：文本/语音转写直取，媒体项渲染占位（真实媒体收发是 P3）。"""
    parts: list[str] = []
    for item in item_list or []:
        item_type = item.get("type")
        if item_type == 1:
            text = (item.get("text_item") or {}).get("text")
            if text:
                parts.append(text)
        elif item_type == 2:
            parts.append("[图片]")
        elif item_type == 3:
            # 语音条自带 ASR 转写文本；缺失时占位。
            parts.append((item.get("voice_item") or {}).get("text") or "[语音]")
        elif item_type == 4:
            file_name = (item.get("file_item") or {}).get("file_name") or ""
            parts.append(f"[文件 {file_name}]" if file_name else "[文件]")
        elif item_type == 5:
            parts.append("[视频]")
    return "\n".join(p for p in parts if p).strip()


class WeixinIlinkAdapter(ChannelAdapter):
    """微信 iLink（ClawBot 个人号 Bot API）适配器：QR 扫码登录 + getupdates 长轮询 + reply-only 回复。

    硬约束（协议决定）：回复必须回显入站消息的 context_token（每 peer 缓存最新值并持久化，
    重启免重扫、断线不丢回复凭据）；不能主动发起会话（can_initiate=False）。
    凭据 JSON 形如 {bot_token, baseurl, ilink_user_id, ilink_bot_id, context_tokens{peer→token},
    get_updates_buf, typing_ticket, typing_ticket_ts}。
    """

    channel_name = "weixin_ilink"
    conversation_title = "微信对话"
    supports_typing = True
    can_initiate = False
    requires_login = True

    def __init__(self, snapshot: ChannelBindingSnapshot, on_inbound: OnInbound) -> None:
        super().__init__(snapshot, on_inbound)
        self._creds: dict = _load_credentials(snapshot.credentials)
        self._login_gate = asyncio.Event()
        if self.has_credentials():
            self._login_gate.set()
        self._login_task: asyncio.Task | None = None
        self._login_state: dict = {"state": "connected" if self.has_credentials() else "login_required"}
        self._client: httpx.AsyncClient | None = None

    # ---- 基础 HTTP 层 ---------------------------------------------------------------

    def has_credentials(self) -> bool:
        return bool(self._creds.get("bot_token"))

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS))
        return self._client

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_wechat_uin(),
        }
        token = self._creds.get("bot_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _base_url(self) -> str:
        return self._creds.get("baseurl") or DEFAULT_BASE_URL

    async def _request(self, method: str, path: str, *, params: dict | None = None, payload: dict | None = None, timeout: float | None = None) -> dict:
        url = path if path.startswith("http") else self._base_url().rstrip("/") + "/" + path
        try:
            resp = await self._ensure_client().request(method, url, params=params, json=payload, headers=self._headers(), timeout=timeout)
        except httpx.TimeoutException:
            raise
        except httpx.HTTPError as e:
            raise ChannelError(f"iLink transport error: {e}", fatal=False) from e
        if resp.status_code >= 500:
            raise ChannelError(f"iLink server error {resp.status_code}", fatal=False)
        if resp.status_code >= 400:
            raise ChannelError(f"iLink auth/request error {resp.status_code}", fatal=False)
        data = resp.json() if resp.content else {}
        ret = data.get("ret")
        errcode = data.get("errcode")
        if ret == SESSION_EXPIRED or errcode == SESSION_EXPIRED:
            raise IlinkSessionExpired()
        if (ret is not None and ret != 0) or (errcode is not None and errcode != 0):
            raise ChannelError(f"iLink API error: {data.get('errmsg') or data}", fatal=False)
        return data

    # ---- 登录流 ---------------------------------------------------------------------

    async def start_login(self) -> None:
        if self._login_task is not None and not self._login_task.done():
            return
        self._login_task = asyncio.create_task(self._login_flow(), name=f"channels.weixin.login.{self.snapshot.id}")

    async def login_state(self) -> dict:
        return dict(self._login_state)

    async def _login_flow(self) -> None:
        """QR 登录状态机：取码 → 3s 轮询 wait→scaned→confirmed|expired（5 分钟总超时）。
        confirmed 返回 bot_token/baseurl/ilink_user_id——凭据与游标清零重建（新会话旧 token/对端回复凭据全部失效），
        登录账号本人自动加入白名单（微信侧自聊文传入站即本人 id，omp-wechat 同款语义）。
        """
        try:
            data = await self._request("GET", "ilink/bot/get_bot_qrcode", params={"bot_type": 3})
            qrcode = data.get("qrcode")
            qr_image = data.get("qrcode_img_content")
            if not qrcode:
                self._login_state = {"state": "error", "error": "二维码获取失败"}
                return
            self._login_state = {"state": "wait", "qr_image": qr_image or qrcode or ""}
            deadline = time.monotonic() + QR_LOGIN_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                await asyncio.sleep(QR_POLL_INTERVAL_SECONDS)
                data = await self._request("GET", "ilink/bot/get_qrcode_status", params={"qrcode": qrcode})
                status = data.get("status")
                if status == "wait":
                    continue
                if status == "scaned":
                    self._login_state = {"state": "scaned"}
                    continue
                if status == "expired":
                    self._login_state = {"state": "expired"}
                    return
                if status == "confirmed":
                    bot_token = data.get("bot_token")
                    if not bot_token:
                        self._login_state = {"state": "error", "error": "登录响应缺少 bot_token"}
                        return
                    self._creds = {
                        "bot_token": bot_token,
                        "baseurl": data.get("baseurl") or DEFAULT_BASE_URL,
                        "ilink_user_id": data.get("ilink_user_id") or "",
                        "ilink_bot_id": data.get("ilink_bot_id") or "",
                        "context_tokens": {},
                        "get_updates_buf": "",
                    }
                    await self._persist_credentials()
                    await self._auto_allow_owner()
                    await update_binding_status(
                        self.snapshot.id,
                        "connected",
                        account_ref=data.get("ilink_bot_id") or "",
                        account_name="微信",
                    )
                    self._login_state = {"state": "confirmed"}
                    self._login_gate.set()
                    return
            self._login_state = {"state": "expired"}
        except asyncio.CancelledError:
            raise
        except IlinkSessionExpired:
            self._login_state = {"state": "error", "error": "登录会话已过期，请重试"}
        except Exception as e:
            self._login_state = {"state": "error", "error": str(e)}
            logger.warning("weixin login flow failed", extra={"binding": self.snapshot.id, "error": str(e)})

    async def _auto_allow_owner(self) -> None:
        owner_id = self._creds.get("ilink_user_id")
        if not owner_id:
            return
        async with session_scope() as db:
            existing = (await db.execute(select(ChannelPeer).where(ChannelPeer.binding_id == self.snapshot.id, ChannelPeer.peer_id == owner_id))).scalar_one_or_none()
            if existing is None:
                db.add(ChannelPeer(binding_id=self.snapshot.id, peer_id=owner_id, peer_name="微信本人", status="allowed"))
            else:
                existing.status = "allowed"
            await db.commit()

    async def logout(self) -> None:
        if self._login_task is not None and not self._login_task.done():
            self._login_task.cancel()
        self._creds = {}
        await self._persist_credentials()
        self._login_state = {"state": "login_required"}
        self._login_gate.clear()
        await update_binding_status(self.snapshot.id, "login_required")

    async def _expire_session(self) -> None:
        """-14 处理：凭据清空落库、转 login_required（channel.status 事件驱动 Hub 提示重新扫码）。"""
        self._creds = {}
        await self._persist_credentials()
        self._login_state = {"state": "login_required"}
        self._login_gate.clear()
        await update_binding_status(self.snapshot.id, "login_required")

    # ---- 常驻轮询 -------------------------------------------------------------------

    async def run(self) -> None:
        try:
            while True:
                if not self.has_credentials():
                    # login_pending 已由守卫循环标记；等 REST 触发的登录流置位 gate。
                    await self._login_gate.wait()
                    continue
                try:
                    await self._poll_loop()
                except IlinkSessionExpired:
                    await self._expire_session()
                except httpx.TimeoutException:
                    # 长轮询客户端超时（服务端 35s hold 临界抖动）：立即用同一游标重试。
                    continue
        finally:
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    async def _poll_loop(self) -> None:
        while self.has_credentials():
            data = await self._request(
                "POST",
                "ilink/bot/getupdates",
                payload={"get_updates_buf": self._creds.get("get_updates_buf") or "", "base_info": _base_info()},
                timeout=SETTINGS.weixin_ilink_poll_timeout_seconds,
            )
            cursor = data.get("get_updates_buf")
            if cursor:
                self._creds["get_updates_buf"] = cursor
            msgs = data.get("msgs") or []
            for msg in msgs:
                self._accept_inbound(msg)
            # 游标与新增 context_token 一批一存（~35s 一次，DB 写频率可忽略）。
            if msgs or cursor:
                await self._persist_credentials()

    def _accept_inbound(self, msg: dict) -> None:
        peer_id = msg.get("from_user_id") or ""
        text = _extract_text(msg.get("item_list"))
        if not peer_id or not text:
            return
        token = msg.get("context_token")
        if token:
            self._creds.setdefault("context_tokens", {})[peer_id] = token
        inbound = InboundMessage(channel=self.channel_name, peer_id=peer_id, peer_name=peer_id, text=text, context_token=token)

        async def _dispatch() -> None:
            # on_inbound 只入队即返回；随后等回合完成（异常只记日志，不牵连轮询循环）。
            try:
                future = await self._on_inbound(inbound)
                await future
            except Exception:
                logger.exception("weixin inbound turn failed", extra={"binding": self.snapshot.id, "peer": peer_id})

        asyncio.create_task(_dispatch())

    # ---- 出站 -----------------------------------------------------------------------

    async def send_text(self, peer_id: str, text: str, context_token: str | None = None) -> None:
        token = context_token or self._creds.get("context_tokens", {}).get(peer_id)
        if not token:
            raise ChannelError(f"no context_token for peer {peer_id!r} (iLink reply-only)", fatal=False)
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": peer_id,
                # client_id 供服务端去重（超时重试时复用同一 id 才不会被当成两条）。
                "client_id": f"spiritagent-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": token,
            },
            "base_info": _base_info(),
        }
        try:
            await self._request("POST", "ilink/bot/sendmessage", payload=payload)
        except IlinkSessionExpired:
            await self._expire_session()
            raise ChannelError("iLink session expired while sending", fatal=False) from None

    async def send_typing(self, peer_id: str, context_token: str | None = None, status: int = 1) -> None:
        """「对方正在输入…」指示：best-effort，失败只记 debug（omp-wechat 同款降级）。"""
        try:
            ticket = await self._typing_ticket()
            if not ticket:
                return
            await self._request(
                "POST",
                "ilink/bot/sendtyping",
                payload={
                    "ilink_user_id": self._creds.get("ilink_user_id") or "",
                    "typing_ticket": ticket,
                    "status": status,
                    "base_info": _base_info(),
                },
                timeout=10.0,
            )
        except IlinkSessionExpired:
            await self._expire_session()
        except Exception:
            logger.debug("typing indicator failed", extra={"binding": self.snapshot.id, "peer": peer_id})

    async def _typing_ticket(self) -> str:
        ticket = self._creds.get("typing_ticket")
        ts = self._creds.get("typing_ticket_ts") or 0
        if ticket and time.time() - ts < TYPING_TICKET_TTL_SECONDS:
            return ticket
        data = await self._request(
            "POST",
            "ilink/bot/getconfig",
            payload={"ilink_user_id": self._creds.get("ilink_user_id") or "", "base_info": _base_info()},
        )
        ticket = data.get("typing_ticket") or ""
        if ticket:
            self._creds["typing_ticket"] = ticket
            self._creds["typing_ticket_ts"] = time.time()
            await self._persist_credentials()
        return ticket

    def platform_hint(self) -> str | None:
        return "weixin"

    # ---- 凭据持久化 -----------------------------------------------------------------

    async def _persist_credentials(self) -> None:
        async with session_scope() as db:
            row = await db.get(ChannelBinding, self.snapshot.id)
            if row is None:
                return
            row.credentials = json.dumps(self._creds, ensure_ascii=False) if self._creds else ""
            await db.commit()


def _load_credentials(raw: str) -> dict:
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


register("weixin_ilink", WeixinIlinkAdapter)
