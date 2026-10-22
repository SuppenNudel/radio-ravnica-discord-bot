import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import requests

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from cogs.feed.youtube import ContentType, Youtube, YoutubeChannel
from discord_bot_shared.youtube_feed import BaseYoutube


class DummyResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class YoutubeCogTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        state_path = patch.object(
            BaseYoutube, "state_db_path", Path(temporary_directory.name) / "youtube_feed.db", create=True
        )
        state_path.start()
        self.addCleanup(state_path.stop)
        self.previous_role = os.environ.get("ROLE_ANNOUNCEMENT")
        self.previous_channel = os.environ.get("CHANNEL_YOUTUBE")
        self.previous_yt_channel = os.environ.get("YOUTUBE_CHANNEL_GAMERII_ID")

        os.environ["ROLE_ANNOUNCEMENT"] = "42"
        os.environ["CHANNEL_YOUTUBE"] = "1234567890"
        os.environ.pop("YOUTUBE_CHANNEL_GAMERII_ID", None)

        self.cog = Youtube(SimpleNamespace())

    def tearDown(self):
        if self.previous_role is None:
            os.environ.pop("ROLE_ANNOUNCEMENT", None)
        else:
            os.environ["ROLE_ANNOUNCEMENT"] = self.previous_role

        if self.previous_channel is None:
            os.environ.pop("CHANNEL_YOUTUBE", None)
        else:
            os.environ["CHANNEL_YOUTUBE"] = self.previous_channel

        if self.previous_yt_channel is None:
            os.environ.pop("YOUTUBE_CHANNEL_GAMERII_ID", None)
        else:
            os.environ["YOUTUBE_CHANNEL_GAMERII_ID"] = self.previous_yt_channel

    async def test_post_video_sends_expected_message(self):
        channel = YoutubeChannel("GameRii", 99, "UC123")
        self.cog.discord_channel = SimpleNamespace(send=AsyncMock())

        await self.cog.post_video(channel, ContentType.VIDEOS, {"videoId": "abc123"})

        self.cog.discord_channel.send.assert_awaited_once_with(
            "<@&42>\nneues Video von **<@99>**\n\nhttps://www.youtube.com/watch?v=abc123"
        )

    async def test_ensure_channel_id_uses_resolver_when_missing(self):
        channel = YoutubeChannel("GameRii", 99)

        def resolve_and_set(ch):
            ch.channel_id = "UC_RESOLVED"
            return ch.channel_id

        with patch.object(self.cog, "resolve_channel_id", side_effect=resolve_and_set) as resolve_channel_id:
            result = await self.cog.ensure_channel_id(channel)

        self.assertTrue(result)
        resolve_channel_id.assert_called_once_with(channel)
        self.assertEqual(channel.channel_id, "UC_RESOLVED")

    async def test_get_latest_content_retries_transient_errors(self):
        channel = YoutubeChannel("GameRii", 99, "UC123")
        expected_contents = [{"videoId": "new123", "title": "Newest", "published": "2026-04-13T12:00:00+00:00"}]

        with patch.object(self.cog, "ensure_channel_id", AsyncMock(return_value=True)), \
             patch.object(self.cog, "fetch_recent_videos_from_feed", side_effect=[Exception("Timeout"), expected_contents]) as fetch_latest, \
             patch("discord_bot_shared.youtube_feed.asyncio.sleep", new=AsyncMock()) as sleep:
            contents, fetch_failed, not_modified = await self.cog.get_latest_content(channel, ContentType.VIDEOS)

        self.assertFalse(fetch_failed)
        self.assertFalse(not_modified)
        self.assertEqual(contents, expected_contents)
        self.assertEqual(fetch_latest.call_count, 2)
        sleep.assert_awaited_once_with(1)

    async def test_get_latest_content_returns_failure_on_non_transient_error(self):
        channel = YoutubeChannel("GameRii", 99, "UC123")

        with patch.object(self.cog, "ensure_channel_id", AsyncMock(return_value=True)), \
             patch.object(self.cog, "fetch_recent_videos_from_feed", side_effect=Exception("bad xml")):
            contents, fetch_failed, not_modified = await self.cog.get_latest_content(channel, ContentType.VIDEOS)

        self.assertIsNone(contents)
        self.assertTrue(fetch_failed)
        self.assertFalse(not_modified)

    async def test_get_latest_content_marks_not_modified(self):
        channel = YoutubeChannel("GameRii", 99, "UC123")

        with patch.object(self.cog, "ensure_channel_id", AsyncMock(return_value=True)), \
             patch.object(self.cog, "fetch_recent_videos_from_feed", return_value=None):
            contents, fetch_failed, not_modified = await self.cog.get_latest_content(channel, ContentType.VIDEOS)

        self.assertIsNone(contents)
        self.assertFalse(fetch_failed)
        self.assertTrue(not_modified)

    async def test_check_channels_posts_all_unseen_videos_in_order(self):
        channel = self.cog.channels[0]
        channel.content[ContentType.VIDEOS] = {"videoId": "old123"}
        channel.seen_video_ids = {"old123"}
        feed_contents = [
            {"videoId": "new3", "title": "Newest", "published": "2026-04-13T12:02:00+00:00"},
            {"videoId": "new2", "title": "Middle", "published": "2026-04-13T12:01:00+00:00"},
            {"videoId": "new1", "title": "Oldest New", "published": "2026-04-13T12:00:00+00:00"},
            {"videoId": "old123", "title": "Old", "published": "2026-04-13T11:00:00+00:00"},
        ]

        with patch.object(self.cog, "get_latest_content", AsyncMock(return_value=(feed_contents, False, False))), \
             patch.object(self.cog, "post_video", AsyncMock()) as post_video:
            await Youtube.check_channels.coro(self.cog)

        self.assertEqual(post_video.await_count, 3)
        self.assertEqual(post_video.await_args_list[0].args, (channel, ContentType.VIDEOS, feed_contents[2]))
        self.assertEqual(post_video.await_args_list[1].args, (channel, ContentType.VIDEOS, feed_contents[1]))
        self.assertEqual(post_video.await_args_list[2].args, (channel, ContentType.VIDEOS, feed_contents[0]))
        self.assertEqual(channel.content[ContentType.VIDEOS], feed_contents[0])
        self.assertTrue({"new1", "new2", "new3"}.issubset(channel.seen_video_ids))

    async def test_check_channels_remembers_videos_missing_from_restart_snapshot(self):
        original_contents = [{"videoId": "new123"}, {"videoId": "old123"}]
        with patch.object(self.cog, "get_latest_content", AsyncMock(return_value=(original_contents, False, False))):
            await Youtube.check_channels.coro(self.cog)

        restarted_cog = Youtube(self.cog.bot)
        with patch.object(restarted_cog, "get_latest_content", AsyncMock(side_effect=[
            ([original_contents[0]], False, False),
            (original_contents, False, False),
        ])), patch.object(restarted_cog, "post_video", AsyncMock()) as post_video:
            await Youtube.check_channels.coro(restarted_cog)
            await Youtube.check_channels.coro(restarted_cog)

        post_video.assert_not_awaited()

    async def test_check_channels_persists_posts_and_catches_up_after_restart(self):
        old_video = {"videoId": "old123"}
        new_video = {"videoId": "new123"}
        offline_video = {"videoId": "offline123"}
        with patch.object(self.cog, "get_latest_content", AsyncMock(side_effect=[
            ([old_video], False, False),
            ([new_video, new_video, old_video], False, False),
        ])), patch.object(self.cog, "post_video", AsyncMock()) as post_video:
            await Youtube.check_channels.coro(self.cog)
            await Youtube.check_channels.coro(self.cog)

        post_video.assert_awaited_once_with(self.cog.channels[0], ContentType.VIDEOS, new_video)

        restarted_cog = Youtube(self.cog.bot)
        with patch.object(restarted_cog, "get_latest_content", AsyncMock(return_value=(
            [offline_video, new_video, old_video], False, False
        ))), patch.object(restarted_cog, "post_video", AsyncMock()) as post_video:
            await Youtube.check_channels.coro(restarted_cog)

        post_video.assert_awaited_once_with(restarted_cog.channels[0], ContentType.VIDEOS, offline_video)

    async def test_check_channels_does_not_remember_failed_posts(self):
        old_video = {"videoId": "old123"}
        new_video = {"videoId": "new123"}
        with patch.object(self.cog, "get_latest_content", AsyncMock(side_effect=[
            ([old_video], False, False),
            ([new_video, old_video], False, False),
        ])), patch.object(self.cog, "post_video", AsyncMock(side_effect=RuntimeError("send failed"))):
            await Youtube.check_channels.coro(self.cog)
            with self.assertRaisesRegex(RuntimeError, "send failed"):
                await Youtube.check_channels.coro(self.cog)

        restarted_cog = Youtube(self.cog.bot)
        self.assertEqual(restarted_cog.channels[0].seen_video_ids, {"old123"})

    async def test_seen_videos_are_scoped_to_destination_and_handle(self):
        self.cog.remember_videos(self.cog.channels[0], [{"videoId": "old123"}])

        with patch.dict(os.environ, {"CHANNEL_YOUTUBE": "9876543210"}):
            other_destination = Youtube(self.cog.bot)
        self.assertEqual(other_destination.channels[0].seen_video_ids, set())

        self.cog.remember_videos(YoutubeChannel("OtherCreator", 100), [{"videoId": "other123"}])
        restarted_cog = Youtube(self.cog.bot)
        self.assertEqual(restarted_cog.channels[0].seen_video_ids, {"old123"})

    async def test_check_channels_does_not_post_initial_snapshot(self):
        channel = self.cog.channels[0]
        feed_contents = [
            {"videoId": "first123", "title": "First", "published": "2026-04-13T12:00:00+00:00"},
            {"videoId": "older123", "title": "Older", "published": "2026-04-13T11:00:00+00:00"},
        ]

        with patch.object(self.cog, "get_latest_content", AsyncMock(return_value=(feed_contents, False, False))), \
             patch.object(self.cog, "post_video", AsyncMock()) as post_video:
            await Youtube.check_channels.coro(self.cog)

        post_video.assert_not_awaited()
        self.assertEqual(channel.content[ContentType.VIDEOS], feed_contents[0])
        self.assertEqual(channel.seen_video_ids, {"first123", "older123"})


class YoutubeCogParsingTestCase(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        state_path = patch.object(
            BaseYoutube, "state_db_path", Path(temporary_directory.name) / "youtube_feed.db", create=True
        )
        state_path.start()
        self.addCleanup(state_path.stop)
        os.environ["CHANNEL_YOUTUBE"] = "1234567890"
        self.cog = Youtube(SimpleNamespace())

    def test_resolve_channel_id_returns_cached_id_without_request(self):
        channel = YoutubeChannel("GameRii", 99, "UC_CACHED")

        with patch("discord_bot_shared.youtube_feed.requests.get") as requests_get:
            channel_id = self.cog.resolve_channel_id(channel)

        self.assertEqual(channel_id, "UC_CACHED")
        requests_get.assert_not_called()

    def test_resolve_channel_id_parses_handle_page(self):
        channel = YoutubeChannel("GameRii", 99)
        response = DummyResponse('<html>"channelId":"UCabc123-def"</html>')

        with patch("discord_bot_shared.youtube_feed.requests.get", return_value=response) as requests_get:
            channel_id = self.cog.resolve_channel_id(channel)

        self.assertEqual(channel_id, "UCabc123-def")
        self.assertEqual(channel.channel_id, "UCabc123-def")
        requests_get.assert_called_once()

    def test_fetch_recent_videos_from_feed_parses_entries(self):
        channel = YoutubeChannel("GameRii", 99, "UCabc123")
        response = DummyResponse(
            """
            <feed xmlns=\"http://www.w3.org/2005/Atom\" xmlns:yt=\"http://www.youtube.com/xml/schemas/2015\">
                <entry>
                    <yt:videoId>video456</yt:videoId>
                    <link rel=\"alternate\" href=\"https://www.youtube.com/watch?v=video456\" />
                    <title>Second Upload</title>
                    <published>2026-04-13T12:35:56+00:00</published>
                </entry>
                <entry>
                    <yt:videoId>video123</yt:videoId>
                    <link rel=\"alternate\" href=\"https://www.youtube.com/watch?v=video123\" />
                    <title>Fresh Upload</title>
                    <published>2026-04-13T12:34:56+00:00</published>
                </entry>
            </feed>
            """
        )

        with patch("discord_bot_shared.youtube_feed.requests.get", return_value=response):
            recent = self.cog.fetch_recent_videos_from_feed(channel)

        self.assertEqual(
            recent,
            [
                {
                    "videoId": "video456",
                    "title": "Second Upload",
                    "published": "2026-04-13T12:35:56+00:00",
                },
                {
                    "videoId": "video123",
                    "title": "Fresh Upload",
                    "published": "2026-04-13T12:34:56+00:00",
                },
            ],
        )

    def test_fetch_recent_videos_from_feed_returns_empty_when_feed_has_no_entries(self):
        channel = YoutubeChannel("GameRii", 99, "UCabc123")
        response = DummyResponse('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"></feed>')

        with patch("discord_bot_shared.youtube_feed.requests.get", return_value=response):
            recent = self.cog.fetch_recent_videos_from_feed(channel)

        self.assertEqual(recent, [])

    def test_fetch_recent_videos_from_feed_returns_none_on_not_modified(self):
        channel = YoutubeChannel("GameRii", 99, "UCabc123")
        channel.feed_etag = '"etag-1"'
        response = DummyResponse("", status_code=304)

        with patch("discord_bot_shared.youtube_feed.requests.get", return_value=response) as requests_get:
            recent = self.cog.fetch_recent_videos_from_feed(channel)

        self.assertIsNone(recent)
        self.assertEqual(requests_get.call_args.kwargs["headers"]["If-None-Match"], '"etag-1"')

    def test_fetch_recent_videos_falls_back_to_page_on_feed_404(self):
        channel = YoutubeChannel("GameRii", 99, "UCabc123")
        expected = [{"videoId": "video123", "title": "Video", "published": ""}]
        error = requests.HTTPError(response=SimpleNamespace(status_code=404))

        with patch.object(self.cog, "fetch_recent_videos_from_feed", side_effect=error), \
             patch.object(self.cog, "fetch_recent_videos_from_page", return_value=expected) as fetch_page:
            recent = self.cog.fetch_recent_videos(channel)

        self.assertEqual(recent, expected)
        fetch_page.assert_called_once_with(channel, 10)

    def test_fetch_recent_videos_from_page_parses_unbadged_videos(self):
        channel = YoutubeChannel("GameRii", 99, "UCabc123")
        response = DummyResponse(
            """
            <script>var ytInitialData = {"contents":[
                {"lockupViewModel":{
                    "contentId":"video123456","contentType":"LOCKUP_CONTENT_TYPE_VIDEO",
                    "metadata":{"lockupMetadataViewModel":{"title":{"content":"Public video"},
                        "metadata":{"contentMetadataViewModel":{"metadataRows":[]}}}}
                }},
                {"lockupViewModel":{
                    "contentId":"member123456","contentType":"LOCKUP_CONTENT_TYPE_VIDEO",
                    "metadata":{"lockupMetadataViewModel":{"title":{"content":"Members first"},
                        "metadata":{"contentMetadataViewModel":{"metadataRows":[{"badges":[{}]}]}}}}
                }}
            ]};</script>
            """
        )

        with patch("discord_bot_shared.youtube_feed.requests.get", return_value=response) as requests_get:
            recent = self.cog.fetch_recent_videos_from_page(channel)

        self.assertEqual(
            recent,
            [{"videoId": "video123456", "title": "Public video", "published": ""}],
        )
        self.assertEqual(requests_get.call_args.kwargs["headers"]["Cookie"], "SOCS=CAI")

    def test_fetch_recent_videos_from_feed_skips_shorts(self):
        channel = YoutubeChannel("GameRii", 99, "UCabc123")
        response = DummyResponse(
            """
            <feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
                <entry>
                    <yt:videoId>short123</yt:videoId>
                    <link rel="alternate" href="https://www.youtube.com/shorts/short123" />
                    <title>Short</title>
                    <published>2026-04-13T12:35:56+00:00</published>
                </entry>
                <entry>
                    <yt:videoId>video123</yt:videoId>
                    <link rel="alternate" href="https://www.youtube.com/watch?v=video123" />
                    <title>Video</title>
                    <published>2026-04-13T12:34:56+00:00</published>
                </entry>
            </feed>
            """
        )

        with patch("discord_bot_shared.youtube_feed.requests.get", return_value=response):
            recent = self.cog.fetch_recent_videos_from_feed(channel)

        self.assertEqual(
            recent,
            [
                {
                    "videoId": "video123",
                    "title": "Video",
                    "published": "2026-04-13T12:34:56+00:00",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()