"""Synology Chat platform adapter.

Runs an aiohttp HTTP server to receive outgoing webhook/bot callback POSTs from Synology Chat,
validates tokens, transforms payloads into agent prompts, and sends replies asynchronously
via Synology Chat Bot API.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8645
DEFAULT_HOST = "0.0.0.0"


def check_synology_chat_requirements() -> bool:
    """Check if Synology Chat adapter dependencies are available."""
    # Since aiohttp is already a core requirement for other gateways, we check it here
    return AIOHTTP_AVAILABLE


class SynologyChatAdapter(BasePlatformAdapter):
    """Synology Chat <-> Hermes gateway adapter.

    Accepts inbound webhook messages from Synology Chat and posts replies
    asynchronously using the Chatbot/Incoming API.
    """

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.SYNOLOGY_CHAT)
        self._token = config.token or os.getenv("SYNOLOGY_CHAT_BOT_TOKEN", "")
        self._api_url = config.extra.get("api_url") or os.getenv("SYNOLOGY_CHAT_API_URL", "")
        if self._api_url:
            self._api_url = self._api_url.rstrip("/")

        self._webhook_port = int(
            config.extra.get("webhook_port") or os.getenv("SYNOLOGY_CHAT_WEBHOOK_PORT", str(DEFAULT_PORT))
        )
        self._webhook_host = config.extra.get("webhook_host") or os.getenv("SYNOLOGY_CHAT_WEBHOOK_HOST", DEFAULT_HOST)
        
        self._runner = None
        self._http_session = None

    async def connect(self) -> bool:
        import aiohttp
        from aiohttp import web

        if not self._token:
            msg = "[synology_chat] SYNOLOGY_CHAT_BOT_TOKEN not set — cannot authorize webhook or send replies"
            logger.error(msg)
            self._set_fatal_error("synology_chat_missing_token", msg, retryable=False)
            return False

        if not self._api_url:
            msg = "[synology_chat] SYNOLOGY_CHAT_API_URL not set — cannot send replies"
            logger.error(msg)
            self._set_fatal_error("synology_chat_missing_api_url", msg, retryable=False)
            return False

        # Set up HTTP server to receive callbacks from Synology Chat
        app = web.Application()
        app.router.add_post("/webhooks/synology", self._handle_webhook)
        app.router.add_get("/health", lambda _: web.Response(text="ok"))

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._webhook_host, self._webhook_port)
        await site.start()

        self._http_session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False),
            timeout=aiohttp.ClientTimeout(total=30),
            trust_env=True,
        )
        self._running = True

        logger.info(
            "[synology_chat] Webhook server listening on %s:%d/webhooks/synology, api: %s",
            self._webhook_host,
            self._webhook_port,
            self._api_url,
        )
        return True

    async def disconnect(self) -> None:
        if self._http_session:
            await self._http_session.close()
            self._http_session = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._running = False
        logger.info("[synology_chat] Disconnected")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        import json

        if not self._http_session:
            return SendResult(success=False, error="HTTP session not initialized")

        formatted = self.format_message(content)
        chunks = self.truncate_message(formatted)
        last_result = SendResult(success=True)

        url = f"{self._api_url}/webapi/entry.cgi?api=SYNO.Chat.External&method=chatbot&version=2&token={self._token}"

        if chat_id.startswith("channel:"):
            target_channel_id = int(chat_id.split("channel:", 1)[1])
            target_kwargs = {"channel_id": target_channel_id}
        elif chat_id.startswith("dm:"):
            target_user_id = int(chat_id.split("dm:", 1)[1])
            target_kwargs = {"user_ids": [target_user_id]}
        else:
            try:
                target_channel_id = int(chat_id)
                target_kwargs = {"channel_id": target_channel_id}
            except ValueError:
                return SendResult(success=False, error=f"Invalid chat_id: {chat_id}")

        for chunk in chunks:
            payload = {
                "text": chunk,
                **target_kwargs
            }
            
            data = {"payload": json.dumps(payload)}
            
            try:
                async with self._http_session.post(url, data=data) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        logger.error("[synology_chat] Send failed with status %d: %s", resp.status, err_text)
                        return SendResult(success=False, error=f"HTTP {resp.status}: {err_text}")
                    
                    resp_data = await resp.json()
                    if not resp_data.get("success"):
                        err_info = resp_data.get("error", {})
                        err_code = err_info.get("code", "unknown")
                        logger.error("[synology_chat] API error %s: %s", err_code, resp_data)
                        return SendResult(success=False, error=f"Synology Chat API error code: {err_code}")
                        
            except Exception as e:
                logger.error("[synology_chat] Exception sending message: %s", e)
                return SendResult(success=False, error=str(e))

        return last_result

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        import json

        if not self._http_session:
            return SendResult(success=False, error="HTTP session not initialized")

        url = f"{self._api_url}/webapi/entry.cgi?api=SYNO.Chat.External&method=chatbot&version=2&token={self._token}"

        if chat_id.startswith("channel:"):
            target_channel_id = int(chat_id.split("channel:", 1)[1])
            target_kwargs = {"channel_id": target_channel_id}
        elif chat_id.startswith("dm:"):
            target_user_id = int(chat_id.split("dm:", 1)[1])
            target_kwargs = {"user_ids": [target_user_id]}
        else:
            try:
                target_channel_id = int(chat_id)
                target_kwargs = {"channel_id": target_channel_id}
            except ValueError:
                return SendResult(success=False, error=f"Invalid chat_id: {chat_id}")

        payload = {
            "text": caption or "",
            "file_url": image_url,
            **target_kwargs
        }
        
        data = {"payload": json.dumps(payload)}
        
        try:
            async with self._http_session.post(url, data=data) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    return SendResult(success=False, error=f"HTTP {resp.status}: {err_text}")
                
                resp_data = await resp.json()
                if not resp_data.get("success"):
                    err_info = resp_data.get("error", {})
                    err_code = err_info.get("code", "unknown")
                    return SendResult(success=False, error=f"Synology Chat API error code: {err_code}")
                    
        except Exception as e:
            return SendResult(success=False, error=str(e))

        return SendResult(success=True)

    async def send_typing(self, chat_id: str) -> None:
        # Synology Chat does not have a public API for bots to send typing indicators.
        pass

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        if chat_id.startswith("dm:"):
            return {"name": chat_id, "type": "dm"}
        elif chat_id.startswith("channel:"):
            return {"name": chat_id, "type": "channel"}
        return {"name": chat_id, "type": "channel"}

    async def _handle_webhook(self, request) -> "aiohttp.web.Response":
        from aiohttp import web

        try:
            if request.content_type == "application/json":
                data = await request.json()
            else:
                form = await request.post()
                if "payload" in form:
                    data = json.loads(form["payload"])
                else:
                    data = dict(form)
        except Exception as e:
            logger.error("[synology_chat] Webhook parse error: %s", e)
            return web.Response(status=400, text="Bad Request")

        incoming_token = data.get("token")
        if not incoming_token:
            incoming_token = request.query.get("token")

        if incoming_token != self._token:
            logger.warning("[synology_chat] Rejected: token mismatch (expected %s, got %s)", self._token, incoming_token)
            return web.Response(status=403, text="Forbidden")

        # Extract fields
        text = data.get("text", "").strip()
        user_id = data.get("user_id")
        username = data.get("username", "User")
        channel_id = data.get("channel_id")
        channel_name = data.get("channel_name")
        post_id = data.get("post_id")

        if not user_id or not text:
            return web.Response(status=200, text="No content")

        # Determine if it is a DM or channel message
        if channel_id:
            chat_id = f"channel:{channel_id}"
            chat_type = "channel"
            chat_name = channel_name or f"Channel {channel_id}"
        else:
            chat_id = f"dm:{user_id}"
            chat_type = "dm"
            chat_name = username or f"User {user_id}"

        logger.info(
            "[synology_chat] inbound from %s (%s) on chat %s: %s",
            username,
            user_id,
            chat_id,
            text[:80],
        )

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_name,
            chat_type=chat_type,
            user_id=str(user_id),
            user_name=username,
            message_id=str(post_id) if post_id else None,
        )

        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=data,
            message_id=str(post_id) if post_id else None,
        )

        # Dispatch asynchronously so we return a fast 200 OK to Synology Chat
        task = asyncio.create_task(self.handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return web.Response(status=200, text="OK")
