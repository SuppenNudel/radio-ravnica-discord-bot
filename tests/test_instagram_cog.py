import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from cogs.feed.instagram import InstagramMonitor, InstagramProfile


class InstagramCogTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_post_content_splits_long_captions_without_losing_text(self):
        cog = InstagramMonitor.__new__(InstagramMonitor)
        cog.discord_channel = SimpleNamespace(send=AsyncMock())
        channel = InstagramProfile("radioravnica", 123)
        post = {
            "caption": "Long caption " * 250,
            "url": "https://www.instagram.com/p/example/",
            "type": "Video",
            "videoUrl": "https://example.com/video",
        }

        await cog.post_content(channel, post)

        sent_messages = [call.kwargs["content"] for call in cog.discord_channel.send.await_args_list]
        expected_content = (
            f"📷 Neuer Instagram Post von **<@{channel.dc_user_id}>**\n"
            f"<{post['url']}>\n{post['caption']}\n[Video]({post['videoUrl']})"
        )
        self.assertGreater(len(sent_messages), 1)
        self.assertTrue(all(len(message) <= 2000 for message in sent_messages))
        self.assertEqual("".join(sent_messages), expected_content)


if __name__ == "__main__":
    unittest.main()