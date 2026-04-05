"""Telegram notification client"""

import logging
from typing import Any

import requests

logger = logging.getLogger("ott-hooks")


class TelegramNotifier:
    """Client for sending notifications via Telegram Bot API"""
    
    def __init__(self, config: dict[str, Any]):
        """Initialize Telegram notifier
        
        Args:
            config: Telegram configuration dictionary containing:
                - enabled (bool): Whether Telegram notifications are enabled
                - bot_token (str): Telegram bot token
                - chat_id (str): Telegram chat ID
                - region (str, optional): Region name for display
        """
        self.enabled: bool = config.get("enabled", False)
        self.token: str | None = config.get("bot_token")
        self.chat_id: str | None = config.get("chat_id")
        self.region: str = config.get("region", "your region")
        
        if self.enabled and (not self.token or not self.chat_id):
            logger.warning(
                "[TG] Telegram enabled but bot_token or chat_id missing. "
                "Notifications will be disabled."
            )
            self.enabled = False

    def send(
        self,
        message: str,
        buttons: list[list[dict]] | None = None,
        chat_id: str | int | None = None,
    ) -> bool:
        """Send text message to Telegram

        Args:
            message: Message text (supports Markdown formatting)
            buttons: Optional inline keyboard buttons
                Format: [[{"text": "Label", "callback_data": "data"}]]
            chat_id: Optional target chat id. Falls back to the notifier's
                default chat_id (set at construction) when omitted. Use this
                to route admin alerts to a separate chat.

        Returns:
            True if message sent successfully, False otherwise
        """
        if not self.enabled:
            logger.debug("[TG] Telegram disabled, skipping message")
            return False

        target_chat = chat_id if chat_id else self.chat_id
        if not self.token or not target_chat:
            logger.error("[TG] Missing bot_token or chat_id")
            return False

        payload = {
            "chat_id": target_chat,
            "text": message,
            "parse_mode": "Markdown",
        }
        
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}

        try:
            res = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json=payload,
                timeout=15,
            )
            
            if res.ok:
                logger.info("[TG] Message sent successfully")
                return True
            else:
                logger.error(f"[TG] Send failed: {res.status_code} - {res.text}")
                return False
                
        except requests.RequestException as e:
            logger.error(f"[TG] Request failed: {e}")
            return False
            
    def send_photo(
        self,
        photo_url: str,
        caption: str,
        buttons: list[list[dict]] | None = None,
        chat_id: str | int | None = None,
    ) -> bool:
        """Send photo with caption to Telegram

        Args:
            photo_url: URL of the photo to send
            caption: Photo caption (supports Markdown formatting)
            buttons: Optional inline keyboard buttons
                Format: [[{"text": "Label", "callback_data": "data"}]]
            chat_id: Optional target chat id. Falls back to the notifier's
                default chat_id (set at construction) when omitted.

        Returns:
            True if photo sent successfully, False otherwise
        """
        if not self.enabled:
            logger.debug("[TG] Telegram disabled, skipping photo")
            return False

        target_chat = chat_id if chat_id else self.chat_id
        if not self.token or not target_chat:
            logger.error("[TG] Missing bot_token or chat_id")
            return False

        payload = {
            "chat_id": target_chat,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "Markdown",
        }
        
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}

        try:
            url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
            res = requests.post(url, json=payload, timeout=15)
            
            if res.ok:
                logger.info("[TG] Photo sent successfully")
                return True
            else:
                logger.error(f"[TG] Photo send failed: {res.status_code} - {res.text}")
                return False
                
        except requests.RequestException as e:
            logger.error(f"[TG] Photo request failed: {e}")
            return False
