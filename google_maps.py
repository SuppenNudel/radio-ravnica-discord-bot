import googlemaps
import os
from dotenv import load_dotenv

load_dotenv()
gmaps = googlemaps.Client(key=os.environ["GMAPS_TOKEN"])
result = gmaps.geocode("Würfelzwerg Esslingen")
print(result)
