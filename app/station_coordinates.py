# Real latitude/longitude for major Indian Railway stations and their common
# city-name aliases (since source_station/destination_station in data is
# often just the city name, e.g. "Delhi" instead of "Delhi Junction").
STATION_COORDINATES = {
    "Ahmedabad Junction": (23.0272, 72.6014),
    "Ahmedabad": (23.0272, 72.6014),
    "Mumbai Central": (18.9696, 72.8194),
    "Mumbai": (18.9696, 72.8194),
    "Surat": (21.1702, 72.8311),
    "Vadodara Junction": (22.3072, 73.1812),
    "Vadodara": (22.3072, 73.1812),
    "Delhi Junction": (28.6667, 77.2167),
    "Delhi": (28.6667, 77.2167),
    "New Delhi": (28.6431, 77.2197),
    "Kolkata": (22.5851, 88.3468),
    "Chennai Central": (13.0827, 80.2707),
    "Chennai": (13.0827, 80.2707),
    "Bangalore": (12.9767, 77.5713),
    "Pune Junction": (18.5286, 73.8744),
    "Pune": (18.5286, 73.8744),
    "Jaipur": (26.9196, 75.7878),
    "Lucknow": (26.8318, 80.9145),
    "Amritsar": (31.6340, 74.8723),
    "Jammu": (32.7266, 74.8570),
    "Firozpur": (30.9163, 74.6132),
    "Porbandar": (21.6417, 69.6293),
    "Jodhpur": (26.2870, 73.0243),
    "Anand": (22.5645, 72.9289),
}


def get_station_coordinates(station_name):
    """Returns (lat, lng) for a known station/city name, or None if unknown."""
    return STATION_COORDINATES.get(station_name)