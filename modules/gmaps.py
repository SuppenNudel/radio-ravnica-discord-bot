from googlemaps.client import Client as Gmaps
import os

class DistanceCalculator:
    def __init__(self, gmaps_token):
        self.gmaps = Gmaps(key=gmaps_token)

    def get_distances(self, origin, destinations):
        # Call the Distance Matrix API
        result = self.gmaps.distance_matrix(origins=origin, destinations=destinations)

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

if __name__ == "__main__":
    gmaps_token = os.getenv("GMAPS_TOKEN")

    origin = "66484"
    destinations = ["Kaiserslautern", "Saarbrücken", "Berlin"]

    calculator = DistanceCalculator(gmaps_token)
    distances = calculator.get_distances(origin, destinations)
    pass