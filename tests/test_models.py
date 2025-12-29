"""Tests for models and helper functions"""

from datetime import datetime
from unittest.mock import patch

import pytest

from src.models import ProcessingMetrics, build_telegram_caption


def test_processing_metrics_initialization():
    """Test ProcessingMetrics dataclass initialization"""
    metrics = ProcessingMetrics()
    
    assert metrics.checked == 0
    assert metrics.cleaned == 0
    assert metrics.marked_processed == 0
    assert metrics.skipped_override == 0
    assert metrics.already_processed == 0
    assert metrics.errors == 0


def test_processing_metrics_with_values():
    """Test ProcessingMetrics with custom values"""
    metrics = ProcessingMetrics(
        checked=10,
        cleaned=3,
        marked_processed=5,
        skipped_override=1,
        already_processed=1,
        errors=0
    )
    
    assert metrics.checked == 10
    assert metrics.cleaned == 3
    assert metrics.marked_processed == 5
    assert metrics.skipped_override == 1
    assert metrics.already_processed == 1
    assert metrics.errors == 0


def test_build_telegram_caption_movie():
    """Test building Telegram caption for movie"""
    caption = build_telegram_caption(
        title="Inception",
        year=2010,
        provider="Netflix",
        region="India",
        item_type="movie",
        item_id=123,
        requested_by="john_doe"
    )
    
    assert "🎬 *Inception*" in caption
    assert "(2010)" in caption
    assert "📺 *Available on:* Netflix (India)" in caption
    assert "📂 *Type:* Movie" in caption
    assert "🆔 *ID:* 123" in caption
    assert "👤 *Requested by:* john_doe" in caption
    assert "⚠️ This title is already available on OTT" in caption


def test_build_telegram_caption_series():
    """Test building Telegram caption for series"""
    caption = build_telegram_caption(
        title="Stranger Things",
        year=2016,
        provider="Netflix",
        region="US",
        item_type="series",
        item_id=456,
        requested_by="overseerr"
    )
    
    assert "🎬 *Stranger Things*" in caption
    assert "(2016)" in caption
    assert "📂 *Type:* Series" in caption
    assert "🆔 *ID:* 456" in caption


def test_build_telegram_caption_no_year():
    """Test building caption without year"""
    caption = build_telegram_caption(
        title="Unknown Show",
        year=None,
        provider="Prime Video",
        region="India",
        item_type="series",
        item_id=789,
        requested_by="admin"
    )
    
    assert "🎬 *Unknown Show*" in caption
    assert "(None)" not in caption  # Should not show None
    assert "📺 *Available on:* Prime Video (India)" in caption


def test_build_telegram_caption_no_requester():
    """Test building caption without requester info"""
    caption = build_telegram_caption(
        title="Test Movie",
        year=2021,
        provider="Disney Plus Hotstar",
        region="India",
        item_type="movie",
        item_id=999,
        requested_by=None
    )
    
    assert "👤 *Requested by:* Unknown" in caption


@patch('src.models.datetime')
def test_build_telegram_caption_timestamp(mock_datetime):
    """Test timestamp formatting in caption"""
    # Mock datetime to return fixed time
    mock_now = datetime(2025, 12, 29, 15, 30, 45)
    mock_datetime.now.return_value = mock_now
    
    caption = build_telegram_caption(
        title="Test",
        year=2021,
        provider="Netflix",
        region="India",
        item_type="movie",
        item_id=1,
        requested_by="user"
    )
    
    assert "⏱ *Detected at:* 29 Dec 2025, 15:30" in caption


def test_build_telegram_caption_markdown_formatting():
    """Test that caption uses proper Markdown formatting"""
    caption = build_telegram_caption(
        title="The Matrix",
        year=1999,
        provider="Netflix",
        region="India",
        item_type="movie",
        item_id=100,
        requested_by="neo"
    )
    
    # Check for Markdown bold formatting
    assert "*The Matrix*" in caption
    assert "*Available on:*" in caption
    assert "*Type:*" in caption
    assert "*ID:*" in caption
    assert "*Requested by:*" in caption
    assert "*Detected at:*" in caption


def test_build_telegram_caption_action_prompt():
    """Test that caption includes action prompt"""
    caption = build_telegram_caption(
        title="Test",
        year=2021,
        provider="Netflix",
        region="India",
        item_type="movie",
        item_id=1,
        requested_by="user"
    )
    
    assert "⚠️ This title is already available on OTT" in caption
    assert "*not* be downloaded unless you approve" in caption
    assert "👇 Choose an action:" in caption
