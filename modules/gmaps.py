if __name__ == "__main__":
    import env
else:
    from modules import env, notion
from io import BytesIO
import math
import os
import requests
import urllib.parse
import logging
from PIL import Image, ImageDraw

DEFAULT_LOG = "ezcord"
log = logging.getLogger(DEFAULT_LOG)
link_log = logging.getLogger("link_logger")

STATE_TAGS = env.STATE_TAGS
AREA_DATABASE_ID = env.AREA_DATABASE_ID

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_LOOKUP_URL = "https://nominatim.openstreetmap.org/lookup"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
REQUEST_TIMEOUT_SECONDS = 15
REQUEST_HEADERS = {
    "User-Agent": "radio-ravnica-discord-bot/1.0 (paper-events geocoding)"
}
TILE_SIZE = 256
MAP_WIDTH = 600
MAP_HEIGHT = 640
DACH_MAP_CENTER = {"lat": 50.4, "lng": 10.6}
DACH_COUNTRY_CODES = {"DE", "AT", "CH"}
EUROPE_MAP_CENTER = {"lat": 53.0, "lng": 15.0}
TMP_DIR = "tmp"
GERMAN_STATE_SHORT_NAMES = {
    "Baden-Wurttemberg": "BW",
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
    "Thuringen": "TH",
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


def _request_nominatim(params: dict) -> list[dict]:
    response = requests.get(
        NOMINATIM_SEARCH_URL,
        params=params,
        headers=REQUEST_HEADERS,
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


def _get_viewport(country_code: str | None) -> dict:
    if country_code in DACH_COUNTRY_CODES:
        return {
            "name": "dach",
            "zoom": 6,
            "center_lat": DACH_MAP_CENTER["lat"],
            "center_lng": DACH_MAP_CENTER["lng"],
        }
    return {
        "name": "europe",
        "zoom": 4,
        "center_lat": EUROPE_MAP_CENTER["lat"],
        "center_lng": EUROPE_MAP_CENTER["lng"],
    }


def _get_base_map_file_path(viewport: dict) -> str:
    return (
        f"{TMP_DIR}/osm_base_{viewport['name']}_"
        f"z{viewport['zoom']}_{str(viewport['center_lat']).replace('.', '_')}_"
        f"{str(viewport['center_lng']).replace('.', '_')}.png"
    )


def _lon_to_pixel_x(lon: float, zoom: int) -> float:
    scale = TILE_SIZE * (2 ** zoom)
    return (lon + 180.0) / 360.0 * scale


def _lat_to_pixel_y(lat: float, zoom: int) -> float:
    lat_rad = math.radians(lat)
    scale = TILE_SIZE * (2 ** zoom)
    return (1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * scale


def _fetch_tile(zoom: int, tile_x: int, tile_y: int) -> Image.Image:
    max_index = 2 ** zoom
    wrapped_x = tile_x % max_index
    clamped_y = min(max(tile_y, 0), max_index - 1)
    response = requests.get(
        OSM_TILE_URL.format(z=zoom, x=wrapped_x, y=clamped_y),
        headers=REQUEST_HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return Image.open(BytesIO(response.content)).convert("RGBA")


def _render_base_map(viewport: dict) -> Image.Image:
    zoom = viewport["zoom"]
    center_lat = viewport["center_lat"]
    center_lng = viewport["center_lng"]
    center_pixel_x = _lon_to_pixel_x(center_lng, zoom)
    center_pixel_y = _lat_to_pixel_y(center_lat, zoom)

    left = center_pixel_x - MAP_WIDTH / 2
    top = center_pixel_y - MAP_HEIGHT / 2
    right = left + MAP_WIDTH
    bottom = top + MAP_HEIGHT

    start_tile_x = math.floor(left / TILE_SIZE)
    end_tile_x = math.floor((right - 1) / TILE_SIZE)
    start_tile_y = math.floor(top / TILE_SIZE)
    end_tile_y = math.floor((bottom - 1) / TILE_SIZE)

    canvas_width = (end_tile_x - start_tile_x + 1) * TILE_SIZE
    canvas_height = (end_tile_y - start_tile_y + 1) * TILE_SIZE
    canvas = Image.new("RGBA", (canvas_width, canvas_height), "white")

    for tile_x in range(start_tile_x, end_tile_x + 1):
        for tile_y in range(start_tile_y, end_tile_y + 1):
            tile = _fetch_tile(zoom, tile_x, tile_y)
            paste_x = (tile_x - start_tile_x) * TILE_SIZE
            paste_y = (tile_y - start_tile_y) * TILE_SIZE
            canvas.paste(tile, (paste_x, paste_y))

    crop_left = int(left - start_tile_x * TILE_SIZE)
    crop_top = int(top - start_tile_y * TILE_SIZE)
    return canvas.crop((crop_left, crop_top, crop_left + MAP_WIDTH, crop_top + MAP_HEIGHT))


def _load_or_create_base_map(viewport: dict) -> Image.Image:
    base_map_path = _get_base_map_file_path(viewport)
    os.makedirs(TMP_DIR, exist_ok=True)
    if os.path.exists(base_map_path):
        return Image.open(base_map_path).convert("RGBA")

    base_map = _render_base_map(viewport)
    base_map.convert("RGB").save(base_map_path, format="PNG")
    return base_map


def _draw_pin(image: Image.Image, x: float, y: float):
    draw = ImageDraw.Draw(image)
    radius = 12
    pin_color = "#D12B2B"
    outline_color = "#7D1313"
    draw.ellipse((x - radius, y - 2 * radius, x + radius, y), fill=pin_color, outline=outline_color, width=2)
    draw.polygon(
        [(x, y + 16), (x - 9, y - 2), (x + 9, y - 2)],
        fill=pin_color,
        outline=outline_color,
    )
    draw.ellipse((x - 4, y - radius - 4, x + 4, y - radius + 4), fill="white")

def get_distances(origin, destinations):
    raise NotImplementedError("Distance calculations are not implemented for the OpenStreetMap backend.")

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
        address = geocode_result.get('address', {})
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
        location.name = (
            geocode_result.get('namedetails', {}).get('name')
            or geocode_result.get('name')
            or address.get('amenity')
            or address.get('shop')
            or address.get('building')
        )
        location.gmaps_url = _build_osm_url(geocode_result)
        extratags = geocode_result.get('extratags', {})
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
        return "https://www.openstreetmap.org/search?query=" + urllib.parse.quote(search_term)

    def get_static_map(self):
        if not self.coord:
            self.file_name = None
            self.file_path = None
            return

        country_code = self.country['short_name'] if self.country else None
        viewport = _get_viewport(country_code)
        zoom = viewport["zoom"]
        center_lat = viewport["center_lat"]
        center_lng = viewport["center_lng"]
        center_pixel_x = _lon_to_pixel_x(center_lng, zoom)
        center_pixel_y = _lat_to_pixel_y(center_lat, zoom)
        left = center_pixel_x - MAP_WIDTH / 2
        top = center_pixel_y - MAP_HEIGHT / 2
        pin_pixel_x = _lon_to_pixel_x(self.coord.lng, zoom)
        pin_pixel_y = _lat_to_pixel_y(self.coord.lat, zoom)

        cropped = _load_or_create_base_map(viewport).copy()
        pin_x = pin_pixel_x - left
        pin_y = pin_pixel_y - top
        _draw_pin(cropped, pin_x, pin_y)

        safe_lat = str(self.coord.lat).replace(".", "_")
        safe_lng = str(self.coord.lng).replace(".", "_")
        self.file_name = f"osm_map_{viewport['name']}_{safe_lat}_{safe_lng}.png"
        self.file_path = f"{TMP_DIR}/{self.file_name}"
        os.makedirs(TMP_DIR, exist_ok=True)
        cropped.convert("RGB").save(self.file_path, format="PNG")

def get_location(location:str, language="de", details=False) -> Location:
    geocode_results = _request_nominatim({
        "q": location,
        "format": "jsonv2",
        "addressdetails": 1,
        "namedetails": 1,
        "extratags": 1,
        "limit": 5,
        "accept-language": language,
    })
    if len(geocode_results) == 0:
        # not found
        raise Exception(f"No location  found for {location}")
    if len(geocode_results) > 1:
        locations: list[Location] = []
        for geocode_result in geocode_results:
            location_result:Location = Location.from_geocode_result(geocode_result)
            locations.append(location_result)
        locations_str = '\n'.join([f"{loc.name or 'Ort'}\n{loc.formatted_address}\n{loc.get_search_url()}\n" for loc in locations])
        raise Exception(f"Mehrere Orte für '{location}' gefunden:\n\n{locations_str}\nBitte versuche den Ort zu spezifizieren!")
    return Location.from_geocode_result(geocode_results[0])


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