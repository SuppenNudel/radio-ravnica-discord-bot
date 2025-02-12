from notion_client import Client, APIResponseError
from notion_client.api_endpoints import DatabasesEndpoint
import os
from datetime import datetime
import logging
from notion_client.helpers import collect_paginated_api
from enum import Enum
from typing import Union, Type
import time, json

from dotenv import load_dotenv
load_dotenv()
notion_token = os.getenv("NOTION_TOKEN")
notion = Client(auth=notion_token, logger=logging.getLogger())

# Enums for different property types
class TextCondition(Enum):
    EQUALS = "equals"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"


class NumberCondition(Enum):
    EQUALS = "equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    GREATER_THAN_OR_EQUAL_TO = "greater_than_or_equal_to"
    LESS_THAN_OR_EQUAL_TO = "less_than_or_equal_to"


class CheckboxCondition(Enum):
    EQUALS = "equals"

class URLCondition(Enum):
    EQUALS = "equals"
    CONTAINS = "contains"
    IS_NOT_EMPTY = "is_not_empty"
    IS_EMPTY = "is_empty"

class DateCondition(Enum):
    EQUALS = "equals"
    BEFORE = "before"
    AFTER = "after"
    ON_OR_BEFORE = "on_or_before"
    ON_OR_AFTER = "on_or_after"


class MultiSelectCondition(Enum):
    CONTAINS = "contains"
    DOES_NOT_CONTAIN = "does_not_contain"


class NotionFilterBuilder:
    def __init__(self):
        self.filters = []

    def add_text_filter(self, property_name: str, condition: TextCondition, value: str):
        self.filters.append({
            "property": property_name,
            "rich_text": {condition.value: value}
        })
        return self
    
    def add_url_filter(self, property_name: str, condition: URLCondition, value: str|bool):
        self.filters.append({
            "property": property_name,
            "url": {condition.value: value}
        })
        return self

    def add_number_filter(self, property_name: str, condition: NumberCondition, value: Union[int, float]):
        self.filters.append({
            "property": property_name,
            "number": {condition.value: value}
        })
        return self

    def add_checkbox_filter(self, property_name: str, condition: CheckboxCondition, value: bool):
        self.filters.append({
            "property": property_name,
            "checkbox": {condition.value: value}
        })
        return self

    def add_date_filter(self, property_name: str, condition: DateCondition, value: str):
        self.filters.append({
            "property": property_name,
            "date": {condition.value: value}
        })
        return self

    def add_multi_select_filter(self, property_name: str, condition: MultiSelectCondition, value: str):
        self.filters.append({
            "property": property_name,
            "multi_select": {condition.value: value}
        })
        return self

    def build(self):
        """
        Constructs the final filter object.

        :return: A dictionary representing the complete filter.
        """
        if len(self.filters) == 1:
            return self.filters[0]  # Return a single filter directly
        return {"and": self.filters}  # Combine all filters with 'AND'

class Entry():

    def __init__(self, entry):
        self.entry = entry
        self.public_url = entry['public_url']
        self.id = entry['id']

    def get_property(self, name):
        property = self.entry['properties'][name]
        p_type = property['type']
        value = property[p_type]
        return value
    
    def get_text_property(self, name) -> str | None:
        prop = self.get_property(name)
        if prop:
            return prop[0]['plain_text']
        else:
            return None
    
    def get_checkbox_property(self, name) -> bool:
        prop = self.get_property(name)
        return prop
        
    def get_date_property(self, name):
        prop = self.get_property(name)
        return {
            'start': datetime.fromisoformat(prop['start']) if prop['start'] else None,
            'end': datetime.fromisoformat(prop['end']) if prop['end'] else None,
            'tz': prop['time_zone']
        }
    
    def get_status_property(self, name, enum_class: Type[Enum]|None=None) -> Enum|str|None:
        prop = self.get_property(name)
        if not prop:
            return None
        value = prop['name']
        if enum_class:
            try:
                return enum_class(value)
            except KeyError:
                raise ValueError(f"{value} is not a valid value for {enum_class.__name__}")
        else:
            return value
    
    def get_multi_select_property(self, name) -> list:
        prop = self.get_property(name)
        names = [item['name'] for item in prop]
        return names
    
    def get_url_property(self, name):
        prop = self.get_property(name)
        return prop
    
    def get_file_property(self, name):
        prop = self.get_property(name)
        if prop:
            type = prop[0]["type"]
            file_url = prop[0][type]["url"]
            return file_url
        return None
    
    def get_number_property(self, name):
        prop = self.get_property(name)
        return prop

    def get_formula_property(self, name) -> str:
        prop = self.get_property(name)
        v_type = prop['type']
        result = prop[v_type]
        return result

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
    
    def add_number(self, name:str, number:int|float):
        self.payload[name] = {
            "number": number
        }
        return self

    def add_url(self, name:str, url:str):
        self.payload[name] = {
            "url": url
        }
        return self
    
    def add_status(self, name:str, status:str|Enum):
        if isinstance(status, Enum):
            status = status.value
        self.payload[name] = {
            "type": "status",
            "status": { "name": status}
        }
        return self
    
    def add_relation(self, name:str, releated_page_id:int):
        self.payload[name] = {
            "relation": [{"id": releated_page_id}]
        }
        return self

    def add_checkbox(self, name: str, checked: bool):
        self.payload[name] = {
            "checkbox": checked
        }
        return self

    def build(self):
        return self.payload


def retry_with_rate_limit(func, *args, **kwargs):
    """
    Retries a Notion API call if a rate limit is encountered.

    :param func: The Notion API function to call.
    :param args: Positional arguments for the function.
    :param kwargs: Keyword arguments for the function.
    :return: The result of the API call.
    """
    while True:
        try:
            return func(*args, **kwargs)
        except APIResponseError as e:
            # Check if 'Retry-After' is in the response headers
            retry_after = e.headers.get("Retry-After", None)
            
            if e.status == 429 and retry_after:
                retry_after = int(retry_after)  # Convert to integer
                print(f"Rate limit hit. Retrying after {retry_after} seconds.")
                time.sleep(retry_after)
            else:
                # Log or handle the error differently if no retry-after is present
                print(f"Error: {e}")
                raise  # Re-raise other exceptions
        except Exception as e:
            raise e
    
async def add_or_ignore(database_id, filter, payload):
    # creates a page if no filter matches
    query_response = get_all_entries(database_id=database_id, filter=filter)
    if query_response:
        pass
    else:
        add_to_database(database_id=database_id, payload=payload)

def add_to_database(database_id, payload) -> dict:
    # Creates a page in the database
    response = retry_with_rate_limit(
        notion.pages.create,
        parent={"database_id": database_id},
        properties=payload
    )
    if not isinstance(response, dict):
        raise Exception("Response is not a dict")
    return response

def update_entry(page_id, update_properties) -> dict:
    update_response = notion.pages.update(
        page_id=page_id,
        properties=update_properties
    )
    if not type(update_response) == dict:
        raise Exception("Response is not a dict")
    else:
        return update_response

# def get_entry(database_id, filter=None):
#     pprint(filter)
#     result = DatabasesEndpoint(notion).query(database_id=database_id, filter=filter)
#     return result

def get_all_entries(database_id, filter=None) -> list[dict]:
    if filter:
        all_entries = retry_with_rate_limit(
            collect_paginated_api,
            notion.databases.query,
            database_id=database_id,
            filter=filter
        )
    else:
        all_entries = retry_with_rate_limit(
            collect_paginated_api,
            notion.databases.query,
            database_id=database_id
        )
    return all_entries

def remove_entry(entry:Entry):
    result = notion.blocks.delete(block_id=entry.id)
    if not result:
        raise Exception("Entry not deleted")

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

def add_or_update_entry(database_id: str, filter: dict, payload: dict):
    """
    Updates an entry if it exists or creates a new one if not.

    :param database_id: The ID of the Notion database.
    :param filter: The filter to search for existing entries.
    :param payload: The properties of the entry to create or update.
    :return: The response from Notion API (creation or update).
    """
    # Get all matching entries
    matching_entries = get_all_entries(database_id, filter=filter)

    if matching_entries:
        if len(matching_entries) > 1:
            raise Exception("Multiple entries found, not going to update")
        # Update the first matching entry
        page_id = matching_entries[0]["id"]
        # print(f"Updating entry with ID: {page_id}")
        response = update_entry(page_id, payload)
    else:
        # Create a new entry
        # print("No matching entry found. Creating a new entry.")
        response = add_to_database(database_id, payload)
    
    return response

def update_database_description(database_id: str, description: str):
    """
    Updates the description of a Notion database.

    :param database_id: The ID of the Notion database to update.
    :param description: The new description text.
    """
    try:
        response = retry_with_rate_limit(
            notion.databases.update,
            database_id=database_id,
            description=[
                {
                    "type": "text",
                    "text": {"content": description}
                }
            ]
        )
        print(f"Updated description for database: {database_id}")
        return response
    except Exception as e:
        print(f"Error updating database description: {e}")
        raise

def get_select_options(database_id: str, field_name: str) -> list[str]:
    logging.debug(f"retreiving select options from database {database_id} column {field_name}")
    database:dict = notion.databases.retrieve(database_id)

    # Extract options from the select or multi-select field
    if field_name in database["properties"] and database["properties"][field_name]["type"] in ["select", "multi_select"]:
        options = database["properties"][field_name][database["properties"][field_name]["type"]]["options"]
        select_options = [option["name"] for option in options]
        return select_options
    else:
        raise Exception(f"Field '{field_name}' not found or not a select/multi-select field.")

if __name__ == "__main__":
    EVENT_DATABASE_ID="f05d532cf91f4f9cbce38e27dc85b522"
    db_id_card_score = "179f020626c280599916d453caeb0123"
    # youtube_videos_id = "15ef020626c28097acc4ec8a14c1fcca"
    # db_id_aua_questions = "159f020626c2807d839eec8dc4bfb0a0"
    # update_database_description(db_id_card_score, "Test")
    get_select_options(EVENT_DATABASE_ID, "Format(e)")

# Decks seit 16.07.2024, COMP, MAJOR, PROFESSIONAL -> 4516 decks
