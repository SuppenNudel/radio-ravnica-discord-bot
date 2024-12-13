from notion_client import Client
import os
from ezcord import log
from datetime import datetime

STATUS_RECORDED = "Aufgenommen"
STATUS_SEEN = "Gesehen"
STATUS_NOT_STARTED = "Not started"

EMOJI_THUMBS_UP = "👍"
EMOJI_NOTEPAD = "🗒️"

notion = Client(auth=os.getenv("NOTION_TOKEN"))

class NotionPayloadBuilder():

    def __init__(self):
        self.payload = {}
    
    def add_title(self, title_name:str, title_value:str):
        self.payload[title_name] = { "title": [{"text": { "content": title_value }}]}
        return self

    def add_text(self, name:str, text:str):
        self.payload[name] = {
            "rich_text": [{"text": {"content": text}}]
        }
        return self

    def add_date(self, name:str, start:datetime, end:datetime|None=None):
        self.payload[name] = {
            "date": {
                "start": start.isoformat(),
                "end": end.isoformat() if end else None

            }
        }
        return self
    
    def add_number(self, name:str, number:int):
        self.payload[name] = {
            "number": number
        }
        return self

    def add_url(self, name:str, url:str):
        self.payload[name] = {
            "url": url
        }
        return self
    
    def add_status(self, name:str, status:str):
        self.payload[name] = {
            "type": "status",
            "status": { "name": status}
        }
        return self

    def build(self):
        return self.payload

def add_to_database(database_id, payload):
    response = notion.pages.create(
        parent={"database_id": database_id},
        properties=payload
    )
    return response

def update_entry(page_id, update_properties):
    update_response = notion.pages.update(
        page_id=page_id,
        properties=update_properties
    )
    return update_response

def check_entry(database_id, filter):
    query_response = notion.databases.query(
        database_id=database_id,
        filter=filter
    )
    return query_response

def fetch_database_entries(database_id):
    """
    Fetch all entries in the Notion database.
    """
    results = []
    next_cursor = None

    while True:
        response = notion.databases.query(
            **{
                "database_id": database_id,
                "start_cursor": next_cursor,
            }
            # database_id=database_id,
            # start_cursor=next_cursor
        )
        results.extend(response["results"])
        next_cursor = response.get("next_cursor")
        if not next_cursor:
            break

    return results

def remove_duplicates(entries):
    """
    Remove duplicate entries based on the 'Date' property.
    """
    seen_dates = set()
    to_delete = []

    for entry in entries:
        date_property = entry["properties"].get("Date", {})
        if not date_property or not date_property.get("date"):
            continue  # Skip if Date is missing

        date_value = date_property["date"]["start"]

        if date_value in seen_dates:
            to_delete.append(entry["id"])  # Mark for deletion
        else:
            seen_dates.add(date_value)  # Add to seen set

    # Delete duplicate entries
    for entry_id in to_delete:
        notion.blocks.delete(block_id=entry_id)
        print(f"Deleted entry with ID: {entry_id}")


if __name__ == "__main__":
    database_id = "159f020626c2807d839eec8dc4bfb0a0"
    # Fetch entries
    database_entries = fetch_database_entries(database_id)

    # Remove duplicates
    remove_duplicates(database_entries)
