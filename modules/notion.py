from notion_client import Client
import os
from ezcord import log

notion = Client(auth=os.getenv("NOTION_TOKEN"))

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
