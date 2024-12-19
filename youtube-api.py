import scrapetube
import notion

GAMERII = "gamerii"
DATABASE_ID="15ef020626c28097acc4ec8a14c1fcca"

videos = scrapetube.get_channel(channel_username=GAMERII, limit=5)
# video_list = list(videos)[::-1]

for video in videos:
    title:str = video['title']['runs'][0]['text']
    
    print(title)
    if "Ask Us Anything" in title:

        title = title.split(' | Ask Us Anything | ')[0]
        video_id = video['videoId']

        payload = notion.NotionPayloadBuilder().add_title("Video Title", title).add_text("Video ID", video_id).build()
        filter_criteria = {
            "property": "Video ID",
            "rich_text": {
                "equals": video_id
            }
        }

        notion.add_or_ignore(DATABASE_ID, filter=filter_criteria, payload=payload)
    else:
        pass

