import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

with patch.dict(os.environ, {"CHANNEL_ID_ARENA": "1234567890"}):
    from cogs.feed.arena_daily_deals import ArenaDailyDeals, ROLE_ID_ARENA_DEALS
from modules import bluesky


class BlueskyFeedTestCase(unittest.TestCase):
    def test_check_for_new_post_skips_reposts(self):
        repost = SimpleNamespace(post=SimpleNamespace(author=SimpleNamespace(did="other-did")))
        own_post = SimpleNamespace(post=SimpleNamespace(author=SimpleNamespace(did="target-did")))
        client = MagicMock()
        client.me = object()
        client.app.bsky.feed.get_author_feed.return_value.feed = [repost, own_post]

        with patch.object(bluesky, "client", client):
            result = bluesky.check_for_new_post("target-did")

        self.assertIs(result, own_post.post)
        client.app.bsky.feed.get_author_feed.assert_called_once_with(
            {"actor": "target-did", "limit": 100}
        )

    def test_check_for_new_post_returns_none_when_feed_only_has_reposts(self):
        repost = SimpleNamespace(post=SimpleNamespace(author=SimpleNamespace(did="other-did")))
        client = MagicMock()
        client.me = object()
        client.app.bsky.feed.get_author_feed.return_value.feed = [repost]

        with patch.object(bluesky, "client", client):
            result = bluesky.check_for_new_post("target-did")

        self.assertIsNone(result)


class ArenaDailyDealsTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_get_latest_post_runs_bluesky_request_in_thread(self):
        cog = ArenaDailyDeals(MagicMock())
        cog.author_did = "target-did"
        post = MagicMock()
        with patch("cogs.feed.arena_daily_deals.asyncio.to_thread", AsyncMock(return_value=post)) as to_thread:
            result = await cog.get_latest_post()

        self.assertIs(result, post)
        to_thread.assert_awaited_once_with(bluesky.check_for_new_post, "target-did")

    async def check_post(self, text, image_urls=(), previous_uri: str | None = "old"):
        cog = ArenaDailyDeals(MagicMock())
        if previous_uri is not None:
            cog.latest_post = MagicMock(uri=previous_uri)
        post = SimpleNamespace(
            uri="new",
            record=SimpleNamespace(text=text),
            embed=SimpleNamespace(
                images=[SimpleNamespace(fullsize=url) for url in image_urls]
            ),
        )
        channel = SimpleNamespace(send=AsyncMock())
        with patch.object(cog, "get_latest_post", AsyncMock(return_value=post)), \
             patch("cogs.feed.arena_daily_deals.discord.utils.get_or_fetch", AsyncMock(return_value=channel)):
            await ArenaDailyDeals.check_bsky_posts.coro(cog)
        self.assertIs(cog.latest_post, post)
        return channel.send

    async def test_matching_hashtags_mention_role_once_before_images(self):
        for text in ("#gold", "#draftToken", "#GOLD! #DRAFTTOKEN"):
            with self.subTest(text=text):
                send = await self.check_post(text, ["https://example.com/deal.png"])
                self.assertEqual(send.await_count, 2)
                mention = send.await_args_list[0]
                self.assertEqual(mention.args, (f"<@&{ROLE_ID_ARENA_DEALS}>",))
                allowed_mentions = mention.kwargs["allowed_mentions"]
                self.assertEqual([role.id for role in allowed_mentions.roles], [ROLE_ID_ARENA_DEALS])
                self.assertFalse(allowed_mentions.users)
                self.assertFalse(allowed_mentions.everyone)
                self.assertEqual(send.await_args_list[1].args, ("https://example.com/deal.png",))

    async def test_other_text_only_sends_images(self):
        for text in ("Daily deals", "#golden #draftTokens", "gold draftToken", "word#gold", "#gold_bonus"):
            with self.subTest(text=text):
                send = await self.check_post(text, ["https://example.com/deal.png"])
                send.assert_awaited_once_with("https://example.com/deal.png")

    async def test_matching_post_without_images_still_mentions_role(self):
        send = await self.check_post("#gold")
        self.assertEqual(send.await_count, 1)
        self.assertEqual(send.await_args.args, (f"<@&{ROLE_ID_ARENA_DEALS}>",))

    async def test_unchanged_post_is_not_sent(self):
        send = await self.check_post("#gold", previous_uri="new")
        send.assert_not_awaited()

    async def test_initial_snapshot_is_not_sent(self):
        send = await self.check_post("#draftToken", previous_uri=None)
        send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()