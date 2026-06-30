import unittest
from unittest.mock import patch

from lolita_radar.models import EventType, ItemStatus, RadarEvent, RadarItem
from lolita_radar.notifiers import ConsoleNotifier, build_notifiers_from_env, format_event


class NotificationTests(unittest.TestCase):
    def test_notification_contains_actionable_fields(self) -> None:
        item = RadarItem(
            source="angelic_pretty",
            title="New Arrival: Shell JSK",
            url="https://example.com/shell",
            status=ItemStatus.NEW_ARRIVAL,
            published_at="2026-06-30",
            metadata={"brand": "Angelic Pretty", "matched_keywords": ["Shell", "JSK"], "price": "¥38,280"},
        )

        text = format_event(RadarEvent(source=item.source, event_type=EventType.NEW_ITEM, item=item))

        self.assertIn("RELEASE · Angelic Pretty", text)
        self.assertIn("New Arrival: Shell JSK", text)
        self.assertIn("源头发布时间 / 掲載元日: 2026-06-30", text)
        self.assertIn("状态 / 状態: 新作上架 / 新着", text)
        self.assertIn("来源 / ソース: angelic_pretty", text)
        self.assertIn("价格 / 価格: ¥38,280", text)
        self.assertIn("链接 / URL: https://example.com/shell", text)
        self.assertIn("关键词 / キーワード: Shell, JSK", text)
        self.assertNotIn("event_type:", text)
        self.assertNotIn("published_at:", text)

    def test_content_changed_notification_includes_short_hashes(self) -> None:
        item = RadarItem(
            source="generic_page",
            title="Shop page",
            url="https://example.com/shop",
            status=ItemStatus.SHOP_NEWS,
            content="new content",
        )

        text = format_event(
            RadarEvent(
                source=item.source,
                event_type=EventType.CONTENT_CHANGED,
                item=item,
                previous_content_hash="abcdef1234567890",
            )
        )

        self.assertIn("ALERT · generic_page", text)
        self.assertIn("变化 / 変更: abcdef1234 ->", text)

    def test_shop_news_notification_uses_drop_model_boundary(self) -> None:
        drop_item = RadarItem(
            source="generic_page",
            title="Shell Garden JSK 预约",
            url="https://example.com/shop/shell",
            status=ItemStatus.SHOP_NEWS,
            published_at="2026-06-30",
            metadata={
                "shop": {"name": "Tokyo Proxy", "url": "https://example.com/shop"},
                "item": {"title": "Shell Garden JSK 预约", "url": "https://example.com/shop/shell"},
                "matched_keywords": ["JSK", "预约"],
            },
        )
        page_item = RadarItem(
            source="generic_page",
            title="Proxy watched page",
            url="https://example.com/shop",
            status=ItemStatus.SHOP_NEWS,
            published_at="2026-06-30",
            metadata={
                "shop": {"name": "Tokyo Proxy", "url": "https://example.com/shop"},
                "item": {"title": "Proxy watched page", "url": "https://example.com/shop"},
                "page_level": True,
                "matched_keywords": ["JSK", "预约"],
            },
        )

        drop_text = format_event(RadarEvent(source=drop_item.source, event_type=EventType.NEW_ITEM, item=drop_item))
        page_text = format_event(RadarEvent(source=page_item.source, event_type=EventType.NEW_ITEM, item=page_item))

        self.assertIn("DROP · generic_page", drop_text)
        self.assertIn("ALERT · generic_page", page_text)

    def test_build_notifiers_from_env_stays_local_only(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "TELEGRAM_CHAT_ID": "chat",
                "DISCORD_WEBHOOK_URL": "https://example.com/webhook",
            },
        ):
            notifiers = build_notifiers_from_env()

        self.assertEqual(len(notifiers), 1)
        self.assertIsInstance(notifiers[0], ConsoleNotifier)


if __name__ == "__main__":
    unittest.main()
