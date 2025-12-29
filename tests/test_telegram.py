"""Tests for Telegram notification client"""

from unittest.mock import Mock, patch

import pytest
import requests

from src.clients.telegram import TelegramNotifier


def test_telegram_initialization_enabled():
    """Test Telegram initialization with valid config"""
    config = {
        "enabled": True,
        "bot_token": "123456:ABC-DEF",
        "chat_id": "-1001234567890",
        "region": "India"
    }
    
    notifier = TelegramNotifier(config)
    
    assert notifier.enabled is True
    assert notifier.token == "123456:ABC-DEF"
    assert notifier.chat_id == "-1001234567890"
    assert notifier.region == "India"


def test_telegram_initialization_disabled():
    """Test Telegram initialization when disabled"""
    config = {"enabled": False}
    
    notifier = TelegramNotifier(config)
    
    assert notifier.enabled is False


def test_telegram_initialization_missing_credentials():
    """Test Telegram disabled when credentials missing"""
    config = {
        "enabled": True,
        # Missing bot_token and chat_id
    }
    
    notifier = TelegramNotifier(config)
    
    # Should auto-disable when credentials missing
    assert notifier.enabled is False


@patch('src.clients.telegram.requests.post')
def test_send_message_success(mock_post):
    """Test sending text message successfully"""
    mock_response = Mock()
    mock_response.ok = True
    mock_post.return_value = mock_response
    
    config = {
        "enabled": True,
        "bot_token": "test_token",
        "chat_id": "test_chat"
    }
    notifier = TelegramNotifier(config)
    
    result = notifier.send("Test message")
    
    assert result is True
    assert mock_post.called
    call_args = mock_post.call_args
    assert "https://api.telegram.org/bot" in call_args[0][0]
    assert call_args[1]["json"]["text"] == "Test message"
    assert call_args[1]["json"]["chat_id"] == "test_chat"


@patch('src.clients.telegram.requests.post')
def test_send_message_with_buttons(mock_post):
    """Test sending message with inline keyboard"""
    mock_response = Mock()
    mock_response.ok = True
    mock_post.return_value = mock_response
    
    config = {
        "enabled": True,
        "bot_token": "test_token",
        "chat_id": "test_chat"
    }
    notifier = TelegramNotifier(config)
    
    buttons = [[{"text": "Override", "callback_data": "override:movie:123"}]]
    result = notifier.send("Message with buttons", buttons=buttons)
    
    assert result is True
    payload = mock_post.call_args[1]["json"]
    assert "reply_markup" in payload
    assert payload["reply_markup"]["inline_keyboard"] == buttons


@patch('src.clients.telegram.requests.post')
def test_send_message_failure(mock_post):
    """Test sending message failure"""
    mock_response = Mock()
    mock_response.ok = False
    mock_response.status_code = 400
    mock_response.text = "Bad Request"
    mock_post.return_value = mock_response
    
    config = {
        "enabled": True,
        "bot_token": "test_token",
        "chat_id": "test_chat"
    }
    notifier = TelegramNotifier(config)
    
    result = notifier.send("Test message")
    
    assert result is False


@patch('src.clients.telegram.requests.post')
def test_send_message_request_exception(mock_post):
    """Test sending message with network error"""
    mock_post.side_effect = requests.RequestException("Network error")
    
    config = {
        "enabled": True,
        "bot_token": "test_token",
        "chat_id": "test_chat"
    }
    notifier = TelegramNotifier(config)
    
    result = notifier.send("Test message")
    
    assert result is False


def test_send_message_disabled():
    """Test sending message when Telegram disabled"""
    config = {"enabled": False}
    notifier = TelegramNotifier(config)
    
    result = notifier.send("Test message")
    
    assert result is False


@patch('src.clients.telegram.requests.post')
def test_send_photo_success(mock_post):
    """Test sending photo with caption"""
    mock_response = Mock()
    mock_response.ok = True
    mock_post.return_value = mock_response
    
    config = {
        "enabled": True,
        "bot_token": "test_token",
        "chat_id": "test_chat"
    }
    notifier = TelegramNotifier(config)
    
    result = notifier.send_photo("https://example.com/poster.jpg", "Caption text")
    
    assert result is True
    payload = mock_post.call_args[1]["json"]
    assert payload["photo"] == "https://example.com/poster.jpg"
    assert payload["caption"] == "Caption text"


@patch('src.clients.telegram.requests.post')
def test_send_photo_with_buttons(mock_post):
    """Test sending photo with inline buttons"""
    mock_response = Mock()
    mock_response.ok = True
    mock_post.return_value = mock_response
    
    config = {
        "enabled": True,
        "bot_token": "test_token",
        "chat_id": "test_chat"
    }
    notifier = TelegramNotifier(config)
    
    buttons = [[{"text": "Download", "callback_data": "override:movie:456"}]]
    result = notifier.send_photo("https://example.com/poster.jpg", "Caption", buttons=buttons)
    
    assert result is True
    payload = mock_post.call_args[1]["json"]
    assert "reply_markup" in payload


@patch('src.clients.telegram.requests.post')
def test_send_photo_failure(mock_post):
    """Test photo send failure"""
    mock_response = Mock()
    mock_response.ok = False
    mock_post.return_value = mock_response
    
    config = {
        "enabled": True,
        "bot_token": "test_token",
        "chat_id": "test_chat"
    }
    notifier = TelegramNotifier(config)
    
    result = notifier.send_photo("https://example.com/poster.jpg", "Caption")
    
    assert result is False
