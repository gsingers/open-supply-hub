"""
`facilitygrid` vector tiles from the `facilities` index.

Per tile: the search filters plus the tile's bounding box, and a
`geohex_grid` (H3) aggregation at the resolution whose cells are closest
to the legacy hexagon width (an eighth of the tile). Each cell becomes a
point at the centroid of its facilities, with the properties the React
grid layer reads: `count` (capped like the legacy tiler) and the
`xmin`/`ymin`/`xmax`/`ymax` box it zooms to on click, here the bounds of
the facilities in the cell.
"""
import logging
from typing import List, Optional

import mercantile

from api.facility_search import backend
from api.facility_search.index_definition import read_alias
from api.facility_search.mvt import EXTENT, encode_point_layer
from api.facility_search.query import UnsupportedQuery, build_filters

log = logging.getLogger(__name__)

GRID_LAYER = 'facilitygrid'
GRID_ZOOM_FACTOR = 3  # legacy: hexagon width = tile width / 2**3
HI_LIMIT = 100  # legacy count cap
MAX_BUCKETS = 5000

# Average H3 edge length in km by resolution (0-15).
H3_EDGE_KM = (
    1107.712591, 418.6760055, 158.2446558, 59.81085794, 22.6063794,
    8.544408276, 3.229482772, 1.220629759, 0.461354684, 0.174375668,
    0.065907807, 0.024910561, 0.009415526, 0.003559893, 0.001348575,
    0.000509713,
)
EARTH_CIRCUMFERENCE_KM = 40075.016686


def h3_resolution(z: int) -> int:
    """The resolution whose hexagons (about 2 edges wide) best match."""
    target_km = EARTH_CIRCUMFERENCE_KM / 2 ** z / 2 ** GRID_ZOOM_FACTOR
    return min(range(len(H3_EDGE_KM)),
               key=lambda r: abs(2 * H3_EDGE_KM[r] - target_km))


def grid_tile_body(params, z: int, x: int, y: int) -> dict:
    """Raises UnsupportedQuery for params only Postgres can answer."""
    bounds = mercantile.bounds(x, y, z)
    tile_filter = {'geo_bounding_box': {'location': {
        'top_left': {'lat': bounds.north, 'lon': bounds.west},
        'bottom_right': {'lat': bounds.south, 'lon': bounds.east},
    }}}
    return {
        'size': 0,
        'track_total_hits': False,
        'query': {'bool': {'filter': [*build_filters(params), tile_filter]}},
        'aggs': {'grid': {
            'geohex_grid': {'field': 'location',
                            'precision': h3_resolution(z),
                            'size': MAX_BUCKETS},
            'aggs': {
                'centroid': {'geo_centroid': {'field': 'location'}},
                'bounds': {'geo_bounds': {'field': 'location',
                                          'wrap_longitude': False}},
            },
        }},
    }


def tile_pixel(lng: float, lat: float, z: int, x: int,
               y: int) -> Optional[tuple]:
    tile = mercantile.xy_bounds(x, y, z)
    px, py = mercantile.xy(lng, lat)
    col = round((px - tile.left) / (tile.right - tile.left) * EXTENT)
    row = round((tile.top - py) / (tile.top - tile.bottom) * EXTENT)
    if not (0 <= col <= EXTENT and 0 <= row <= EXTENT):
        return None
    return col, row


def encode_grid_buckets(buckets: List[dict], z: int, x: int,
                        y: int) -> bytes:
    features = []
    for bucket in buckets:
        centroid = bucket['centroid']['location']
        pixel = tile_pixel(centroid['lon'], centroid['lat'], z, x, y)
        if pixel is None:
            continue
        box = bucket['bounds']['bounds']
        features.append((pixel, {
            'count': min(bucket['doc_count'], HI_LIMIT),
            'xmin': box['top_left']['lon'],
            'ymin': box['bottom_right']['lat'],
            'xmax': box['bottom_right']['lon'],
            'ymax': box['top_left']['lat'],
        }))
    return encode_point_layer(GRID_LAYER, features)


def grid_tile(client, params, z: int, x: int, y: int,
              timeout: float) -> bytes:
    response = client.search(index=read_alias(),
                             body=grid_tile_body(params, z, x, y),
                             request_timeout=timeout)
    buckets = response.get('aggregations', {}).get('grid', {}).get(
        'buckets', [])
    return encode_grid_buckets(buckets, z, x, y)


def opensearch_grid_tile(params, z: int, x: int, y: int) -> Optional[bytes]:
    """The tile from OpenSearch, or None to make the caller use PostGIS."""
    try:
        tile = grid_tile(backend.client(), params, int(z), int(x), int(y),
                         backend.request_timeout())
    except UnsupportedQuery as reason:
        log.info('[Map OpenSearch] PostGIS serves this tile: %s', reason)
        return None
    except Exception as error:
        backend.breaker.record_failure()
        log.warning('[Map OpenSearch] Tile falling back to PostGIS: %s',
                    error)
        return None
    backend.breaker.record_success()
    return tile
