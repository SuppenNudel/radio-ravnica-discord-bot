from ezcord import log, Cog
from modules.util import check_website
from discord import Bot
import discord.ext.tasks
import logging
from modules import env
import re

link_log = logging.getLogger("link_logger")

URL_WIZARDS = "https://magic.wizards.com"

NEWS_URLS = {
    "de": {
        "url": f"{URL_WIZARDS}/de/news",
        "channel_id": env.CHANNEL_NEWS_DE
    },
    "en": {
        "url": f"{URL_WIZARDS}/en/news",
        "channel_id": env.CHANNEL_NEWS_EN
    }
}

SELECTORS = {
    "title": "h3.css-9f4rq",
    "authors": ".css-l31Oj",
    "type": ".css-kId4u",
    "type_url": (".css-kId4u", "href"),
    "url": (".css-3qxBv > a", "href"),
    "description": ".css-p4BJO > p"
}

def html_to_discord(text):
    # Replace <i>, <em> with *italic*
    text = re.sub(r'</?(i|em)>', '*', text)
    # Replace <b>, <strong> with **bold**
    text = re.sub(r'</?(b|strong)>', '**', text)
    # Replace <u> with __underline__
    text = re.sub(r'</?u>', '__', text)
    # Remove all other HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    return text

class MtgNews(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.posted_articles = []

    @Cog.listener()
    async def on_ready(self):
        if not self.check_mtg_news.is_running():
            self.check_mtg_news.start()
            
        log.debug(self.__class__.__name__ + " is ready")

    @discord.ext.tasks.loop(minutes=15)
    async def check_mtg_news(self):

        for lang, obj in NEWS_URLS.items():
            latest_articles = check_website.request_website(obj["url"], "article", SELECTORS)
            channel:discord.TextChannel = await discord.utils.get_or_fetch(self.bot, "channel", obj["channel_id"])
            if latest_articles is None:
                log.error("Failed to fetch latest articles from Magic News DE")
                return

            # On first run, just save all articles and skip posting
            if self.check_mtg_news.current_loop == 0:
                self.posted_articles.extend(
                    f'{URL_WIZARDS}{article["url"]}' for article in latest_articles
                )
                continue

            for article in latest_articles:
                article_url = f'{URL_WIZARDS}{article["url"]}'
                if article_url in self.posted_articles:
                    continue  # Already posted

                authors = ', '.join(
                    f'[{author["name"]}](<{URL_WIZARDS}{author["link"]}>)'
                    for author in article['authors']
                )

                await channel.send(f"""
# {article["title"]}
{"von" if lang == "de" else "by"} {authors}
{article_url}
{"Weitere" if lang == "de" else "More"} [{article["type"]} {"Artikel" if lang == "de" else "articles"}](<{article_url}>)""")
                self.posted_articles.append(article_url)

def setup(bot:Bot):
    bot.add_cog(MtgNews(bot))
