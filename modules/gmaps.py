if __name__ == "__main__":
    import env
else:
    from modules import env, notion
import os
import requests
import urllib.parse
import logging

link_log = logging.getLogger("link_logger")

STATE_TAGS = env.STATE_TAGS
AREA_DATABASE_ID = env.AREA_DATABASE_ID

NOMINATIM_LOOKUP_URL = "https://nominatim.openstreetmap.org/lookup"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GOOGLE_PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
GOOGLE_STATIC_MAP_URL = "https://maps.googleapis.com/maps/api/staticmap"
REQUEST_TIMEOUT_SECONDS = 15
REQUEST_HEADERS = {
    "User-Agent": "radio-ravnica-discord-bot/1.0 (paper-events geocoding)"
}
MAP_WIDTH = 600
MAP_HEIGHT = 640
DACH_COUNTRY_CODES = {"DE", "AT", "CH"}

DACH_VIEWPORT = {"name": "dach", "center": "50.4,10.6", "zoom": 6}
NORTH_AMERICA_VIEWPORT = {"name": "north_america", "center": "44.0,-100.0", "zoom": 3}
SOUTH_AMERICA_VIEWPORT = {"name": "south_america", "center": "-15.0,-60.0", "zoom": 3}
EUROPE_VIEWPORT = {"name": "europe", "center": "53.0,15.0", "zoom": 4}
AFRICA_VIEWPORT = {"name": "africa", "center": "2.0,20.0", "zoom": 3}
ASIA_VIEWPORT = {"name": "asia", "center": "35.0,95.0", "zoom": 3}
OCEANIA_VIEWPORT = {"name": "oceania", "center": "-23.0,135.0", "zoom": 3}
ANTARCTICA_VIEWPORT = {"name": "antarctica", "center": "-82.0,0.0", "zoom": 2}
WORLD_VIEWPORT = {"name": "world", "center": "20.0,0.0", "zoom": 1}

NORTH_AMERICA_BOUNDS = {"lat_min": 7.0, "lat_max": 85.0, "lng_min": -170.0, "lng_max": -20.0}
SOUTH_AMERICA_BOUNDS = {"lat_min": -60.0, "lat_max": 15.0, "lng_min": -92.0, "lng_max": -30.0}
EUROPE_BOUNDS = {"lat_min": 34.0, "lat_max": 72.0, "lng_min": -25.0, "lng_max": 45.0}
AFRICA_BOUNDS = {"lat_min": -35.0, "lat_max": 38.0, "lng_min": -20.0, "lng_max": 55.0}
ASIA_BOUNDS = {"lat_min": 0.0, "lat_max": 82.0, "lng_min": 25.0, "lng_max": 180.0}
OCEANIA_BOUNDS = {"lat_min": -50.0, "lat_max": 10.0, "lng_min": 110.0, "lng_max": 180.0}
ANTARCTICA_BOUNDS = {"lat_min": -90.0, "lat_max": -60.0, "lng_min": -180.0, "lng_max": 180.0}
TMP_DIR = "tmp"
GERMAN_STATE_SHORT_NAMES = {
    "Baden-Württemberg": "BW",
    "Baden-Wuerttemberg": "BW",
    "Bayern": "BY",
    "Berlin": "BE",
    "Brandenburg": "BB",
    "Bremen": "HB",
    "Hamburg": "HH",
    "Hessen": "HE",
    "Mecklenburg-Vorpommern": "MV",
    "Niedersachsen": "NI",
    "Nordrhein-Westfalen": "NRW",
    "Rheinland-Pfalz": "RP",
    "Saarland": "SL",
    "Sachsen": "SN",
    "Sachsen-Anhalt": "ST",
    "Schleswig-Holstein": "SH",
    "Thüringen": "TH",
    "Thueringen": "TH",
}

COUNTRY_NAME_TO_CODE = {
    "DEUTSCHLAND": "DE",
    "GERMANY": "DE",
    "ÖSTERREICH": "AT",
    "OESTERREICH": "AT",
    "AUSTRIA": "AT",
    "SCHWEIZ": "CH",
    "SWITZERLAND": "CH",
}


def _request_google_geocode(params: dict) -> dict:
    response = requests.get(
        GOOGLE_GEOCODE_URL,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _request_google_place_details(params: dict) -> dict:
    response = requests.get(
        GOOGLE_PLACE_DETAILS_URL,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _lookup_nominatim(params: dict) -> list[dict]:
    response = requests.get(
        NOMINATIM_LOOKUP_URL,
        params=params,
        headers=REQUEST_HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _reverse_nominatim(params: dict) -> dict:
    response = requests.get(
        NOMINATIM_REVERSE_URL,
        params=params,
        headers=REQUEST_HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _make_component(long_name: str | None, short_name: str | None = None) -> dict | None:
    if not long_name:
        return None
    return {
        "long_name": long_name,
        "short_name": short_name or long_name,
    }


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _get_google_component(components: list, target_type: str) -> dict | None:
    for component in components:
        if target_type in _as_list(component.get("types")):
            return component
    return None


def _get_google_city_component(components: list) -> dict | None:
    for city_type in ["locality", "postal_town", "administrative_area_level_3", "administrative_area_level_2"]:
        component = _get_google_component(components, city_type)
        if component:
            return component
    return None


def _build_google_url(lat: float, lng: float, place_id: str | None = None) -> str:
    query = f"{lat},{lng}"
    if place_id:
        return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(query)}&query_place_id={urllib.parse.quote(place_id)}"
    return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(query)}"


def _normalize_country_code(country_value: str | None) -> str | None:
    if not country_value:
        return None
    cleaned = country_value.strip()
    if not cleaned:
        return None
    upper = cleaned.upper()
    if len(upper) == 2:
        return upper
    return COUNTRY_NAME_TO_CODE.get(upper)


def _get_state_short_name(country_code: str | None, state_name: str | None) -> str | None:
    if not state_name:
        return None
    if (country_code or "").upper() == "DE":
        return GERMAN_STATE_SHORT_NAMES.get(state_name, state_name)
    return state_name


def _build_osm_url(search_result: dict) -> str:
    lat = search_result["lat"]
    lng = search_result["lon"]
    zoom = 16 if search_result.get("category") in ["shop", "amenity", "tourism", "leisure"] else 13
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map={zoom}/{lat}/{lng}"


def _is_in_bounds(lat: float | None, lng: float | None, bounds: dict) -> bool:
    if lat is None or lng is None:
        return False
    return bounds["lat_min"] <= lat <= bounds["lat_max"] and bounds["lng_min"] <= lng <= bounds["lng_max"]


def _get_continent_viewport(lat: float | None, lng: float | None) -> dict:
    if _is_in_bounds(lat, lng, ANTARCTICA_BOUNDS):
        return ANTARCTICA_VIEWPORT
    if _is_in_bounds(lat, lng, EUROPE_BOUNDS):
        return EUROPE_VIEWPORT
    if _is_in_bounds(lat, lng, NORTH_AMERICA_BOUNDS):
        return NORTH_AMERICA_VIEWPORT
    if _is_in_bounds(lat, lng, SOUTH_AMERICA_BOUNDS):
        return SOUTH_AMERICA_VIEWPORT
    if _is_in_bounds(lat, lng, AFRICA_BOUNDS):
        return AFRICA_VIEWPORT
    if _is_in_bounds(lat, lng, OCEANIA_BOUNDS):
        return OCEANIA_VIEWPORT
    if _is_in_bounds(lat, lng, ASIA_BOUNDS):
        return ASIA_VIEWPORT
    return WORLD_VIEWPORT


def _get_viewport(country_code: str | None, lat: float | None, lng: float | None) -> dict:
    if country_code in DACH_COUNTRY_CODES:
        return DACH_VIEWPORT
    return _get_continent_viewport(lat, lng)

class Coordinates():
    def __init__(self, coordinates) -> None:
        self.lng = float(coordinates['lng'])
        self.lat = float(coordinates['lat'])

class Location():
    def __init__(self):
        self.country = None
        self.state = None
        self.city = None
        self.county = None
        self.street = None
        self.street_number = None
        self.zip = None
        self.formatted_address = None
        self.coord = None
        self.types = None
        self.name = None
        self.gmaps_url = None
        self.url = None
        self.osm_type = None
        self.osm_id = None
        self.place_id = None
        self.file_name = None
        self.file_path = None

    def __str__(self):
        parts = []
        if self.name:
            parts.append(self.name)
        
        address_parts = []
        if self.street:
            address_parts.append(self.street.get('long_name', ''))
        if self.street_number:
            address_parts.append(self.street_number.get('long_name', ''))
        
        if address_parts:
            parts.append(" ".join(address_parts))
        
        location_parts = []
        if self.zip:
            location_parts.append(self.zip.get('long_name', ''))
        if self.city:
            location_parts.append(self.city.get('long_name', ''))
        
        if location_parts:
            parts.append(" ".join(location_parts))
        
        return " - ".join(parts) if parts else self.formatted_address or "Location"

    @classmethod
    def from_geocode_result(cls, geocode_result, place_details=None) -> "Location":
        location = Location()
        address = _as_dict(geocode_result.get('address'))
        country_code = (address.get('country_code') or '').upper() or None
        country_name = address.get('country')
        state_name = address.get('state') or address.get('region')
        city_name = (
            address.get('city')
            or address.get('town')
            or address.get('village')
            or address.get('municipality')
        )

        location.country = _make_component(country_name, country_code)
        location.state = _make_component(state_name, _get_state_short_name(country_code, state_name))
        location.city = _make_component(city_name)
        location.county = _make_component(address.get('county'))
        location.street = _make_component(address.get('road') or address.get('pedestrian'))
        location.street_number = _make_component(address.get('house_number'))
        location.zip = _make_component(address.get('postcode'))

        if not address:
            link_log.error(f"No address details for {geocode_result.get('display_name', 'unknown location')}")

        location.formatted_address = geocode_result['display_name']
        location.coord = Coordinates({
            'lat': geocode_result['lat'],
            'lng': geocode_result['lon'],
        })
        location.types = [value for value in [geocode_result.get('category'), geocode_result.get('type')] if value]
        namedetails = _as_dict(geocode_result.get('namedetails'))
        location.name = (
            namedetails.get('name')
            or geocode_result.get('name')
            or address.get('amenity')
            or address.get('shop')
            or address.get('building')
        )
        location.gmaps_url = _build_osm_url(geocode_result)
        extratags = _as_dict(geocode_result.get('extratags'))
        location.url = (
            extratags.get('website')
            or extratags.get('contact:website')
            or extratags.get('url')
        )
        location.osm_type = geocode_result.get('osm_type')
        location.osm_id = str(geocode_result.get('osm_id')) if geocode_result.get('osm_id') is not None else None
        location.place_id = str(geocode_result.get('place_id')) if geocode_result.get('place_id') is not None else None
        location.get_static_map()
        
        return location

    @classmethod
    def from_google_result(cls, geocode_result: dict, place_details: dict | None = None) -> "Location":
        location = Location()
        components = _as_list(geocode_result.get("address_components"))

        country = _get_google_component(components, "country")
        state = _get_google_component(components, "administrative_area_level_1")
        city = _get_google_city_component(components)
        county = _get_google_component(components, "administrative_area_level_2")
        street = _get_google_component(components, "route")
        street_number = _get_google_component(components, "street_number")
        postal_code = _get_google_component(components, "postal_code")

        country_code = country.get("short_name") if country else None
        state_name = state.get("long_name") if state else None

        location.country = _make_component(country.get("long_name") if country else None, country_code)
        location.state = _make_component(state_name, _get_state_short_name(country_code, state_name))
        location.city = _make_component(city.get("long_name") if city else None)
        location.county = _make_component(county.get("long_name") if county else None)
        location.street = _make_component(street.get("long_name") if street else None)
        location.street_number = _make_component(street_number.get("long_name") if street_number else None)
        location.zip = _make_component(postal_code.get("long_name") if postal_code else None)

        location.formatted_address = geocode_result.get("formatted_address") or "Location"
        geometry = _as_dict(geocode_result.get("geometry"))
        coordinates = _as_dict(geometry.get("location"))
        location.coord = Coordinates(
            {
                "lat": coordinates.get("lat"),
                "lng": coordinates.get("lng"),
            }
        )
        location.types = _as_list(geocode_result.get("types"))
        place_details = _as_dict(place_details)
        location.name = (
            place_details.get("name")
            or geocode_result.get("formatted_address")
        )
        location.place_id = geocode_result.get("place_id")
        location.gmaps_url = _build_google_url(location.coord.lat, location.coord.lng, location.place_id)
        location.url = place_details.get("website") or place_details.get("url")
        location.osm_type = None
        location.osm_id = None
        location.get_static_map()

        return location

    @classmethod
    def from_saved_data(
        cls,
        *,
        name: str | None,
        formatted_address: str | None,
        city: str | None,
        country: str | None,
        state: str | None,
        lat: float | None,
        lng: float | None,
        url: str | None,
        osm_type: str | None = None,
        osm_id: str | None = None,
        place_id: str | None = None,
    ) -> "Location | None":
        if lat is None or lng is None:
            return None

        location = Location()
        country_short = _normalize_country_code(country)
        state_name = state or None
        city_name = city or None

        # DB fallback rows may miss state/city and only keep coordinates.
        # Use reverse geocoding once to recover administrative details.
        if (not state_name or not city_name or not country_short):
            try:
                reverse_result = _reverse_nominatim(
                    {
                        "lat": lat,
                        "lon": lng,
                        "format": "jsonv2",
                        "addressdetails": 1,
                        "accept-language": "de",
                    }
                )
                reverse_address = reverse_result.get("address", {})
                reverse_country = reverse_address.get("country")
                reverse_country_code = (reverse_address.get("country_code") or "").upper() or None
                reverse_state = reverse_address.get("state") or reverse_address.get("region")
                reverse_city = (
                    reverse_address.get("city")
                    or reverse_address.get("town")
                    or reverse_address.get("village")
                    or reverse_address.get("municipality")
                )

                if not country and reverse_country:
                    country = reverse_country
                if not country_short:
                    country_short = reverse_country_code or _normalize_country_code(reverse_country)
                if not state_name and reverse_state:
                    state_name = reverse_state
                if not city_name and reverse_city:
                    city_name = reverse_city
            except Exception as exc:
                link_log.warning(
                    f"Could not reverse-geocode saved location ({lat}, {lng}) to enrich area fields: {exc}"
                )

        location.name = name
        location.formatted_address = formatted_address or name or "Location"
        location.city = _make_component(city_name)
        location.country = _make_component(country, country_short or country)
        location.state = _make_component(state_name, _get_state_short_name(country_short or country, state_name))
        location.coord = Coordinates({"lat": lat, "lng": lng})
        location.url = url
        location.gmaps_url = url or f"https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map=16/{lat}/{lng}"
        location.osm_type = osm_type
        location.osm_id = str(osm_id) if osm_id is not None else None
        location.place_id = str(place_id) if place_id is not None else None
        location.types = []
        location.get_static_map()
        return location

    def get_area_and_tag_name(self):
        country_short = self.country['short_name'] if self.country else None
        tag_name = "nicht DACH"
        area_name = self.country['long_name'] if self.country else (self.formatted_address or "Unbekannter Ort")
        if country_short == 'DE':
            state = self.state
            state_short = state['short_name'] if state else None
            area_name = state['long_name'] if state else area_name
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
        if not area_response:
            payload = notion.NotionPayloadBuilder() \
                .add_title("Name", area_name) \
                .add_select("Type", "Land")
            result = notion.add_to_database(database_id=AREA_DATABASE_ID, payload=payload.build())
            entry = notion.Entry(result)
            area_page_id = entry.id
        else:
            area_page_id = area_response[0].id
        return area_page_id

    def get_search_url(self):
        if self.gmaps_url:
            return self.gmaps_url
        search_term = self.formatted_address or self.name or "Location"
        return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(search_term)

    def get_static_map(self):
        if not self.coord:
            self.file_name = None
            self.file_path = None
            return

        token = env.GMAPS_TOKEN
        if not token:
            self.file_name = None
            self.file_path = None
            return

        country_code = self.country['short_name'] if self.country else None
        viewport = _get_viewport(country_code, self.coord.lat, self.coord.lng)
        safe_lat = str(self.coord.lat).replace(".", "_")
        safe_lng = str(self.coord.lng).replace(".", "_")
        self.file_name = f"google_map_{viewport['name']}_{safe_lat}_{safe_lng}.png"
        self.file_path = f"{TMP_DIR}/{self.file_name}"
        os.makedirs(TMP_DIR, exist_ok=True)

        params = {
            "size": f"{MAP_WIDTH}x{MAP_HEIGHT}",
            "center": viewport["center"],
            "zoom": viewport["zoom"],
            "markers": f"color:red|{self.coord.lat},{self.coord.lng}",
            "scale": 2,
            "language": "de",
            "region": (country_code or "DE").lower(),
            "key": token,
        }
        response = requests.get(GOOGLE_STATIC_MAP_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        with open(self.file_path, "wb") as static_map_file:
            static_map_file.write(response.content)

def get_location(location:str, language="de", details=False) -> Location:
    token = env.GMAPS_TOKEN
    if not token:
        raise Exception("Google Maps API key fehlt. Bitte GMAPS_TOKEN setzen.")

    response_payload = _request_google_geocode(
        {
            "address": location,
            "language": language,
            "key": token,
        }
    )
    status = response_payload.get("status")
    if status not in ["OK", "ZERO_RESULTS"]:
        error_message = response_payload.get("error_message") or status
        raise Exception(f"Google Geocoding Fehler: {error_message}")

    geocode_results = _as_list(response_payload.get("results"))
    if len(geocode_results) == 0:
        # not found
        raise Exception(f"No location  found for {location}")
    if len(geocode_results) > 1:
        locations: list[Location] = []
        for geocode_result in geocode_results:
            location_result:Location = Location.from_google_result(geocode_result)
            locations.append(location_result)
        locations_str = '\n'.join([f"{loc.name or 'Ort'}\n{loc.formatted_address}\n{loc.get_search_url()}\n" for loc in locations])
        raise Exception(f"Mehrere Orte für '{location}' gefunden:\n\n{locations_str}\nBitte versuche den Ort zu spezifizieren!")

    geocode_result = geocode_results[0]
    place_details = None
    place_id = geocode_result.get("place_id")
    if details and place_id:
        details_payload = _request_google_place_details(
            {
                "place_id": place_id,
                "language": language,
                "fields": "name,website,url",
                "key": token,
            }
        )
        details_status = details_payload.get("status")
        if details_status == "OK":
            place_details = details_payload.get("result")

    return Location.from_google_result(geocode_result, place_details=place_details)


def get_location_by_osm_id(osm_type: str, osm_id: str | int, language="de") -> Location:
    osm_type_prefix = {
        "node": "N",
        "way": "W",
        "relation": "R",
        "N": "N",
        "W": "W",
        "R": "R",
    }.get(str(osm_type), None)
    if not osm_type_prefix:
        raise Exception(f"Unsupported osm_type: {osm_type}")

    lookup_results = _lookup_nominatim({
        "osm_ids": f"{osm_type_prefix}{osm_id}",
        "format": "jsonv2",
        "addressdetails": 1,
        "namedetails": 1,
        "extratags": 1,
        "accept-language": language,
    })
    if not lookup_results:
        raise Exception(f"No location found for OSM id {osm_type_prefix}{osm_id}")
    return Location.from_geocode_result(lookup_results[0])