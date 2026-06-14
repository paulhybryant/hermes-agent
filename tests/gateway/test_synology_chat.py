"""Tests for Synology Chat platform integration.

Covers config loading, adapter lifecycle, webhook processing, message sending,
and standalone send_message tool integration.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, SendResult


# ── Config Loading ──────────────────────────────────────────────────

class TestSynologyChatConfigLoading:
    """Verify _apply_env_overrides wires Synology Chat correctly."""

    def test_env_overrides_create_synology_chat_config(self):
        from gateway.config import load_gateway_config

        env = {
            "SYNOLOGY_CHAT_BOT_TOKEN": "synology_token_123",
            "SYNOLOGY_CHAT_API_URL": "https://my-nas:5001",
            "SYNOLOGY_CHAT_ALLOWED_USERS": "5,10",
            "SYNOLOGY_CHAT_HOME_CHANNEL": "channel:42",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_gateway_config()
            assert Platform.SYNOLOGY_CHAT in config.platforms
            pc = config.platforms[Platform.SYNOLOGY_CHAT]
            assert pc.enabled is True
            assert pc.token == "synology_token_123"
            assert pc.extra.get("api_url") == "https://my-nas:5001"
            assert pc.home_channel is not None
            assert pc.home_channel.chat_id == "channel:42"
            assert pc.home_channel.platform == Platform.SYNOLOGY_CHAT


# ── Requirements Check ──────────────────────────────────────────────

def test_check_synology_chat_requirements():
    from gateway.platforms.synology_chat import check_synology_chat_requirements
    assert check_synology_chat_requirements() is True


# ── Adapter Lifecycle and Webhook Processing ────────────────────────

class TestSynologyChatAdapter:
    """Test Synology Chat adapter operations and callback webhook processing."""

    def _make_adapter(self) -> "SynologyChatAdapter":
        from gateway.platforms.synology_chat import SynologyChatAdapter
        pc = PlatformConfig(
            enabled=True,
            token="my_test_token",
            extra={"api_url": "https://my-nas:5001", "webhook_port": "8645"}
        )
        adapter = SynologyChatAdapter(pc)
        # Mock handle_message to avoid invoking the real agent pipeline
        adapter.handle_message = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        adapter = self._make_adapter()
        
        # Mock aiohttp AppRunner and TCPSite
        mock_runner = MagicMock()
        mock_runner.setup = AsyncMock()
        mock_runner.cleanup = AsyncMock()
        
        mock_site = MagicMock()
        mock_site.start = AsyncMock()
        
        mock_session = MagicMock()
        mock_session.close = AsyncMock()
        
        with patch("aiohttp.web.AppRunner", return_value=mock_runner), \
             patch("aiohttp.web.TCPSite", return_value=mock_site), \
             patch("aiohttp.ClientSession", return_value=mock_session):
            
            connected = await adapter.connect()
            assert connected is True
            assert adapter._running is True
            
            await adapter.disconnect()
            assert adapter._running is False
            mock_runner.cleanup.assert_called_once()
            mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_webhook_invalid_token(self):
        adapter = self._make_adapter()
        
        # Build mock request
        mock_request = MagicMock()
        mock_request.content_type = "application/json"
        
        # Correct token is 'my_test_token'
        mock_request.json = AsyncMock(return_value={
            "token": "wrong_token",
            "text": "hello",
            "user_id": 5
        })
        mock_request.query = {}
        
        from aiohttp import web
        resp = await adapter._handle_webhook(mock_request)
        assert resp.status == 403
        assert resp.text == "Forbidden"
        adapter.handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_webhook_valid_channel_msg(self):
        adapter = self._make_adapter()
        
        mock_request = MagicMock()
        mock_request.content_type = "application/json"
        mock_request.json = AsyncMock(return_value={
            "token": "my_test_token",
            "text": "hello chatbot",
            "user_id": 5,
            "username": "john_doe",
            "channel_id": 12,
            "channel_name": "General",
            "post_id": "9999"
        })
        
        resp = await adapter._handle_webhook(mock_request)
        assert resp.status == 200
        assert resp.text == "OK"
        
        # Check background task was dispatched
        await asyncio.sleep(0.01) # let background task run
        adapter.handle_message.assert_called_once()
        event = adapter.handle_message.call_args[0][0]
        assert isinstance(event, MessageEvent)
        assert event.text == "hello chatbot"
        assert event.source.chat_id == "channel:12"
        assert event.source.chat_type == "channel"
        assert event.source.user_id == "5"
        assert event.source.user_name == "john_doe"
        assert event.message_id == "9999"

    @pytest.mark.asyncio
    async def test_handle_webhook_form_urlencoded(self):
        adapter = self._make_adapter()
        
        mock_request = MagicMock()
        mock_request.content_type = "application/x-www-form-urlencoded"
        
        payload_data = {
            "token": "my_test_token",
            "text": "hello from form",
            "user_id": 10,
            "username": "alice"
        }
        
        mock_request.post = AsyncMock(return_value={
            "payload": json.dumps(payload_data)
        })
        
        resp = await adapter._handle_webhook(mock_request)
        assert resp.status == 200
        assert resp.text == "OK"
        
        await asyncio.sleep(0.01)
        adapter.handle_message.assert_called_once()
        event = adapter.handle_message.call_args[0][0]
        assert event.text == "hello from form"
        assert event.source.chat_id == "dm:10"
        assert event.source.chat_type == "dm"


# ── Adapter Sending Methods ─────────────────────────────────────────

class TestSynologyChatSending:
    """Test send and send_image on SynologyChatAdapter."""

    def _make_adapter_with_session(self, mock_session) -> "SynologyChatAdapter":
        from gateway.platforms.synology_chat import SynologyChatAdapter
        pc = PlatformConfig(
            enabled=True,
            token="my_test_token",
            extra={"api_url": "https://my-nas:5001"}
        )
        adapter = SynologyChatAdapter(pc)
        adapter._http_session = mock_session
        return adapter

    @pytest.mark.asyncio
    async def test_send_channel_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"success": True})
        
        mock_post_ctx = MagicMock()
        mock_post_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_ctx.__aexit__ = AsyncMock()
        
        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_ctx)
        
        adapter = self._make_with_session(mock_session)
        result = await adapter.send("channel:12", "hello channel")
        
        assert result.success is True
        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert "api=SYNO.Chat.External" in call_args[0][0]
        assert "token=my_test_token" in call_args[0][0]
        
        data_param = call_args[1]["data"]
        payload = json.loads(data_param["payload"])
        assert payload["text"] == "hello channel"
        assert payload["channel_id"] == 12

    @pytest.mark.asyncio
    async def test_send_dm_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"success": True})
        
        mock_post_ctx = MagicMock()
        mock_post_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_ctx.__aexit__ = AsyncMock()
        
        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_ctx)
        
        adapter = self._make_with_session(mock_session)
        result = await adapter.send("dm:5", "hello direct message")
        
        assert result.success is True
        data_param = mock_session.post.call_args[1]["data"]
        payload = json.loads(data_param["payload"])
        assert payload["text"] == "hello direct message"
        assert payload["user_ids"] == [5]

    @pytest.mark.asyncio
    async def test_send_api_error(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "success": False,
            "error": {"code": 401}
        })
        
        mock_post_ctx = MagicMock()
        mock_post_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_ctx.__aexit__ = AsyncMock()
        
        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_ctx)
        
        adapter = self._make_with_session(mock_session)
        result = await adapter.send("channel:12", "hello channel")
        
        assert result.success is False
        assert "Synology Chat API error code: 401" in result.error

    @pytest.mark.asyncio
    async def test_send_image(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"success": True})
        
        mock_post_ctx = MagicMock()
        mock_post_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_ctx.__aexit__ = AsyncMock()
        
        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_ctx)
        
        adapter = self._make_with_session(mock_session)
        result = await adapter.send_image("channel:12", "https://example.com/pic.png", caption="look at this")
        
        assert result.success is True
        data_param = mock_session.post.call_args[1]["data"]
        payload = json.loads(data_param["payload"])
        assert payload["text"] == "look at this"
        assert payload["file_url"] == "https://example.com/pic.png"
        assert payload["channel_id"] == 12

    def _make_with_session(self, mock_session):
        return self._make_adapter_with_session(mock_session)


# ── Standalone Send Message Tool ────────────────────────────────────

class TestStandaloneSendMessageTool:
    """Test _send_synology_chat function directly."""

    @pytest.mark.asyncio
    async def test_send_synology_chat_standalone_success(self):
        from tools.send_message_tool import _send_synology_chat
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={"success": True})
        mock_resp.raise_for_status = MagicMock()
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        
        mock_client_context = MagicMock()
        mock_client_context.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_context.__aexit__ = AsyncMock()
        
        pconfig = PlatformConfig(
            enabled=True,
            token="standalone_token",
            extra={"api_url": "https://my-nas:5001"}
        )
        
        with patch("httpx.AsyncClient", return_value=mock_client_context):
            result = await _send_synology_chat(pconfig, "channel:12", "hello from standalone")
            
            assert result.get("success") is True
            assert result.get("platform") == "synology_chat"
            assert result.get("chat_id") == "channel:12"
            
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            data_param = call_args[1]["data"]
            payload = json.loads(data_param["payload"])
            assert payload["text"] == "hello from standalone"
            assert payload["channel_id"] == 12
