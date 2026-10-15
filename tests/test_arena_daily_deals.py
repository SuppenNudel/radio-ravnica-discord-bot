import os
import sys
import unittest
from atproto_client.exceptions import NetworkError
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
    async def test_on_ready_starts_polling_without_resolving_author(self):
        cog = ArenaDailyDeals(MagicMock())
        loop = MagicMock()
        loop.is_running.return_value = False
        with patch.object(cog, "check_bsky_posts", loop), \
             patch.object(bluesky, "get_target_did") as get_target_did:
            await cog.on_ready()
            loop.is_running.return_value = True
            await cog.on_ready()

        loop.start.assert_called_once_with()
        get_target_did.assert_not_called()
        self.assertIsNone(cog.author_did)

    async def test_startup_failure_is_retried_on_next_poll(self):
        cog = ArenaDailyDeals(MagicMock())
        post = SimpleNamespace(uri="initial")
        with patch.object(bluesky, "get_target_did", side_effect=[
            NetworkError("502 Bad Gateway"), "target-did"
        ]) as get_target_did, \
             patch.object(bluesky, "check_for_new_post", return_value=post) as check_for_new_post, \
             patch("cogs.feed.arena_daily_deals.log") as log:
            await ArenaDailyDeals.check_bsky_posts.coro(cog)
            self.assertIsNone(cog.author_did)
            self.assertIsNone(cog.latest_post)
            check_for_new_post.assert_not_called()
            log.exception.assert_called_once()

            await ArenaDailyDeals.check_bsky_posts.coro(cog)
            await ArenaDailyDeals.check_bsky_posts.coro(cog)

        self.assertEqual(get_target_did.call_count, 2)
        self.assertEqual(check_for_new_post.call_count, 2)
        check_for_new_post.assert_called_with("target-did")
        self.assertEqual(cog.author_did, "target-did")
        self.assertIs(cog.latest_post, post)

    async def test_get_latest_post_resolves_author_in_thread(self):
        cog = ArenaDailyDeals(MagicMock())
        post = MagicMock()
        with patch("cogs.feed.arena_daily_deals.asyncio.to_thread",
                   AsyncMock(side_effect=["target-did", post])) as to_thread:
            result = await cog.get_latest_post()

        self.assertIs(result, post)
        self.assertEqual(to_thread.await_count, 2)
        self.assertEqual(to_thread.await_args_list[0].args, (
            bluesky.get_target_did, "notarenadailydeals.bsky.social"
        ))
        self.assertEqual(to_thread.await_args_list[1].args, (
            bluesky.check_for_new_post, "target-did"
        ))

    async def test_get_latest_post_runs_bluesky_request_in_thread(self):
        cog = ArenaDailyDeals(MagicMock())
        cog.author_did = "target-did"
        post = MagicMock()
        with patch("cogs.feed.arena_daily_deals.asyncio.to_thread", AsyncMock(return_value=post)) as to_thread:
            result = await cog.get_latest_post()

        self.assertIs(result, post)
        to_thread.assert_awaited_once_with(bluesky.check_for_new_post, "target-did")

    async def test_feed_failure_preserves_state_and_retries(self):
        cog = ArenaDailyDeals(MagicMock())
        cog.author_did = "target-did"
        post = SimpleNamespace(uri="previous")
        cog.latest_post = post
        with patch.object(bluesky, "get_target_did") as get_target_did, \
             patch.object(bluesky, "check_for_new_post",
                          side_effect=[NetworkError("502 Bad Gateway"), post]) as check_for_new_post, \
             patch("cogs.feed.arena_daily_deals.log") as log:
            await ArenaDailyDeals.check_bsky_posts.coro(cog)
            self.assertIs(cog.latest_post, post)
            self.assertEqual(cog.author_did, "target-did")
            log.exception.assert_called_once()

            await ArenaDailyDeals.check_bsky_posts.coro(cog)

        self.assertIs(cog.latest_post, post)
        self.assertEqual(check_for_new_post.call_count, 2)
        get_target_did.assert_not_called()

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