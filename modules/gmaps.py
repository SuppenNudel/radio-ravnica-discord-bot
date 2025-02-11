import googlemaps
from modules import env, notion
# import env
import requests
import urllib.parse

GMAPS_TOKEN = env.GMAPS_TOKEN
STATE_TAGS = env.STATE_TAGS
AREA_DATABASE_ID = env.AREA_DATABASE_ID

gmaps = googlemaps.Client(key=GMAPS_TOKEN)

def get_distances(origin, destinations):
    # Call the Distance Matrix API
    result = gmaps.distance_matrix(origins=origin, destinations=destinations)

    # Initialize the result map
    distances_map = {}

    if result['status'] == 'OK':
        elements = result['rows'][0]['elements']  # Get the array of results
        distances_map = dict(zip(destinations, elements))  # Map destinations to elements
    else:
        # Handle errors
        error_message = result.get("error_message", "Unknown error occurred")
        raise Exception(f"Distance Matrix API Error: {error_message}")

    return distances_map

class Coordinates():
    def __init__(self, coordinates) -> None:
        self.lng = coordinates['lng']
        self.lat = coordinates['lat']

class Location():
    def __init__(self, geocode_result, place_details):#:type:Literal['geocode', 'places']):
        # if type == 'geocode':
        for obj in geocode_result['address_components']:
            types = obj.get('types', [])
            if 'country' in types:
                self.country = obj
            elif 'administrative_area_level_1' in types:
                self.state = obj
            elif 'locality' in types:
                self.city = obj
            elif 'administrative_area_level_3' in types:
                self.county = obj # Landkreis
            elif 'route' in types:
                self.street = obj
            elif 'street_number' in types:
                self.street_number = obj
        # elif type == 'places':
        #     self.name = geocode_result['name']
        self.formatted_address:str = geocode_result['formatted_address']
        self.coord:Coordinates = Coordinates(geocode_result['geometry']['location'])
        self.types = geocode_result['types']
        self.get_static_map()
        if place_details:
            self.phone_number = place_details['international_phone_number']
            self.name = place_details['name']
            self.gmaps_url = place_details['url']
            self.url = place_details['website']

    def get_area_and_tag_name(self):
        country_short = self.country['short_name']
        tag_name = "nicht DACH"
        if country_short == 'DE':
            state = self.state
            state_short = state['short_name']
            area_name = state['long_name']
            if state_short in STATE_TAGS:
                tag_name = STATE_TAGS[state_short]
        else:
            if country_short in STATE_TAGS:
                tag_name = STATE_TAGS[country_short]
        return (area_name, tag_name)
    
    def get_area_page_id(self):
        (area_name, tag_name) = self.get_area_and_tag_name()
        filter = notion.NotionFilterBuilder().add_text_filter("Name", notion.TextCondition.EQUALS, area_name).build()
        area_response = notion.get_all_entries(database_id=AREA_DATABASE_ID, filter=filter)
        area_page_id = area_response[0]['id']
        return area_page_id

    def get_search_url(self):
        if self.gmaps_url:
            return self.gmaps_url
        search_term = self.formatted_address
        if 'store' in self.types:
            search_term = "https://www.google.com/maps/search/"+urllib.parse.quote(search_term)
        return "https://www.google.com/maps/search/"+urllib.parse.quote(search_term)

    def get_static_map(self):
        lng = self.coord.lng
        lat = self.coord.lat
        map_url = f"https://maps.googleapis.com/maps/api/staticmap?center=50.6,11&zoom=6&size=600x640&markers=color:red%257label:S%7C{lat},{lng}&language=de&key={GMAPS_TOKEN}"

        response = requests.get(map_url)
        # Save the file locally
        self.file_name = "google_map.png"
        self.file_path = f"tmp/{self.file_name}"
        with open(self.file_path, "wb") as file_maps:
            file_maps.write(response.content)

# def get_places(location:str, language="de", details=False):
#     places_results = gmaps.places(location, language=language)['results']
#     if len(places_results) == 0:
#         # not found
#         raise Exception(f"No location found for {location}")
#     if len(places_results) > 1:
#         locations = [Location(places_result, 'places') for places_result in places_results]
#         raise Exception(f"Multiple locations found for {location}", locations)
#     place_details = None
#     if details:
#         place_details = gmaps.place(place_id=places_results[0]['place_id'])['result']
#     # place_details enthält alles, kann ich aber erst bekommen, wenn ich die place_id habe
#     return Location(places_results[0], place_details)

def get_location(location:str, language="de", details=False) -> Location:
    geocode_results = gmaps.geocode(location, language=language)
    if len(geocode_results) == 0:
        # not found
        raise Exception(f"No location found for {location}")
    if len(geocode_results) > 1:
        locations = [Location(geocode_result, 'geocode') for geocode_result in geocode_results]
        raise Exception(f"Multiple locations found for {location}", locations)
    # location_coords = geocode_results[0]["geometry"]["location"]
    if details:
        place_details = gmaps.place(place_id=geocode_results[0]['place_id'])['result']
    return Location(geocode_results[0], place_details)

if __name__ == "__main__":
    search_string = "Battlebear kaiserslautern"
    # get_places(search_string)
    # location = get_location(search_string)
    # print(location)
    exit()

    # try:
    #     location = get_location("BattleBearTCG Kaiserslautern")
    # except googlemaps.exceptions.HTTPError as e:
    #     print(e)
    origin = "66484"
    destinations = ["Kaiserslautern", "Saarbrücken", "Berlin"]

    distances = get_distances(origin, destinations)
    pass