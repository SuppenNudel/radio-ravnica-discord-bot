import googlemaps
from modules import env
import requests

GMAPS_TOKEN = env.GMAPS_TOKEN
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
    def __init__(self, geocode_result):
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
        self.coord:Coordinates = Coordinates(geocode_result['geometry']['location'])
        self.formatted_address:str = geocode_result['formatted_address']
        self.get_static_map()

    def get_static_map(self) -> str:
        lng = self.coord.lng
        lat = self.coord.lat
        map_url = f"https://maps.googleapis.com/maps/api/staticmap?center=50.6,11&zoom=6&size=600x640&markers=color:red%257label:S%7C{lat},{lng}&language=de&key={GMAPS_TOKEN}"

        response = requests.get(map_url)
        # Save the file locally
        file_path = "tmp/google_map.png"
        with open(file_path, "wb") as file_maps:
            file_maps.write(response.content)
        return map_url

def get_location(location:str, language="de") -> Location:
    geocode_results = gmaps.geocode(location, language=language)
    if len(geocode_results) == 0:
        # not found
        raise Exception(f"No location found for {location}")
    if len(geocode_results) > 1:
        locations = [Location(geocode_result) for geocode_result in geocode_results]
        raise Exception(f"Multiple locations found for {location}", locations)
    return Location(geocode_results[0])

if __name__ == "__main__":
    geocode_results = get_location("BattleBearTCG Kaiserslautern")
    print(geocode_results)
    exit()

    # try:
    #     location = get_location("BattleBearTCG Kaiserslautern")
    # except googlemaps.exceptions.HTTPError as e:
    #     print(e)
    origin = "66484"
    destinations = ["Kaiserslautern", "Saarbrücken", "Berlin"]

    distances = get_distances(origin, destinations)
    pass