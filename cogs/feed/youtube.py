import os
import sys
from pathlib import Path

from discord import Bot

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from discord_bot_shared.youtube_feed import (  # noqa: E402
    ContentType,
    YoutubeChannel,
    YoutubeChannelDefinition,
    YoutubeFeedConfig,
    create_youtube_cog,
)


def build_message(yt_channel: YoutubeChannel, content: dict) -> str:
    url = f"https://www.youtube.com/watch?v={content['videoId']}"
    return f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\nneues Video von **<@{yt_channel.dc_user_id}>**\n\n{url}"


Youtube = create_youtube_cog(
    YoutubeFeedConfig(
        notification_channel_env="CHANNEL_YOUTUBE",
        channels=[
            YoutubeChannelDefinition(
                "GameRii",
                270288996666441728,
                "YOUTUBE_CHANNEL_GAMERII_ID",
            )
        ],
        message_builder=build_message,
    )
)


def setup(bot: Bot):
    bot.add_cog(Youtube(bot))

