import struct
from unittest.mock import MagicMock, patch

import mercantile
from django.contrib.gis.geos import Point
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from opensearchpy.exceptions import ConnectionTimeout
from waffle.testutils import override_switch

from api.facility_search import backend
from api.facility_search.mvt import encode_point_layer
from api.facility_search.rebuild import rebuild
from api.facility_search.tiles import (
    encode_grid_buckets,
    grid_tile,
    grid_tile_body,
    h3_resolution,
    tile_pixel,
)
from api.tests.facility_search_helpers import FacilityFactoryMixin
from api.tests.opensearch_live import LiveIndexMixin, requires_opensearch


# A small independent protobuf reader, enough to check the tiles.
def read_varint(data, pos):
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def read_message(data):
    pos, fields = 0, []
    while pos < len(data):
        key, pos = read_varint(data, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = read_varint(data, pos)
        elif wire == 1:
            value, pos = data[pos:pos + 8], pos + 8
        elif wire == 2:
            length, pos = read_varint(data, pos)
            value, pos = data[pos:pos + length], pos + length
        else:
            raise ValueError(f'wire type {wire}')
        fields.append((field, value))
    return fields


def packed(data):
    pos, values = 0, []
    while pos < len(data):
        value, pos = read_varint(data, pos)
        values.append(value)
    return values


def unzigzag(value):
    return (value >> 1) ^ -(value & 1)


def decode_value(data):
    (field, value), = read_message(data)
    if field == 1:
        return value.decode()
    if field == 3:
        return struct.unpack('<d', value)[0]
    if field == 4:
        return value
    if field == 7:
        return bool(value)
    raise ValueError(field)


def decode_tile(data):
    layers = {}
    for field, layer_bytes in read_message(data):
        assert field == 3
        layer = read_message(layer_bytes)
        name = next(v for f, v in layer if f == 1).decode()
        keys = [v.decode() for f, v in layer if f == 3]
        values = [decode_value(v) for f, v in layer if f == 4]
        extent = next(v for f, v in layer if f == 5)
        version = next(v for f, v in layer if f == 15)
        features = []
        for f, feature_bytes in layer:
            if f != 2:
                continue
            feature = dict(read_message(feature_bytes))
            tags = packed(feature[2])
            command, dx, dy = packed(feature[4])
            assert command == 9  # MoveTo, count 1
            features.append({
                'type': feature[3],
                'point': (unzigzag(dx), unzigzag(dy)),
                'properties': {keys[tags[i]]: values[tags[i + 1]]
                               for i in range(0, len(tags), 2)},
            })
        layers[name] = {'extent': extent, 'version': version,
                        'features': features}
    return layers


class MvtEncoderTest(SimpleTestCase):
    def test_points_and_properties_round_trip(self):
        tile = encode_point_layer('facilitygrid', [
            ((10, 4000), {'count': 3, 'xmin': -1.5, 'label': 'a'}),
            ((4096, 0), {'count': 100, 'xmin': 2.25, 'flag': True}),
        ])

        layer = decode_tile(tile)['facilitygrid']
        self.assertEqual(layer['version'], 2)
        self.assertEqual(layer['extent'], 4096)
        first, second = layer['features']
        self.assertEqual(first['type'], 1)
        self.assertEqual(first['point'], (10, 4000))
        self.assertEqual(first['properties'],
                         {'count': 3, 'xmin': -1.5, 'label': 'a'})
        self.assertEqual(second['point'], (4096, 0))
        self.assertEqual(second['properties'],
                         {'count': 100, 'xmin': 2.25, 'flag': True})

    def test_negative_coordinates_zigzag(self):
        tile = encode_point_layer('l', [((-3, -64), {})])

        self.assertEqual(decode_tile(tile)['l']['features'][0]['point'],
                         (-3, -64))

    def test_no_features_is_an_empty_tile(self):
        self.assertEqual(encode_point_layer('l', []), b'')


class GridTileTest(SimpleTestCase):
    def bucket(self, lng, lat, count):
        return {
            'doc_count': count,
            'centroid': {'location': {'lat': lat, 'lon': lng}},
            'bounds': {'bounds': {
                'top_left': {'lat': lat + 1, 'lon': lng - 1},
                'bottom_right': {'lat': lat - 1, 'lon': lng + 1},
            }},
        }

    def test_buckets_become_capped_points_with_click_bounds(self):
        tile = encode_grid_buckets(
            [self.bucket(10, 20, 3), self.bucket(-10, -20, 250)], 0, 0, 0)

        features = decode_tile(tile)['facilitygrid']['features']
        self.assertEqual(features[0]['properties'], {
            'count': 3, 'xmin': 9.0, 'ymin': 19.0, 'xmax': 11.0,
            'ymax': 21.0})
        self.assertEqual(features[1]['properties']['count'], 100)
        self.assertEqual(features[0]['point'], tile_pixel(10, 20, 0, 0, 0))

    def test_pixels_follow_the_tile_grid(self):
        x, y = mercantile.tile(10, 20, 6)[:2]
        west, south, east, north = mercantile.bounds(x, y, 6)

        self.assertEqual(tile_pixel(west, north, 6, x, y), (0, 0))
        self.assertEqual(tile_pixel(east, south, 6, x, y), (4096, 4096))
        self.assertIsNone(tile_pixel(east + 1, south, 6, x, y))

    def test_resolution_tracks_the_legacy_hexagon_width(self):
        resolutions = [h3_resolution(z) for z in range(0, 20)]
        self.assertEqual(resolutions, sorted(resolutions))
        self.assertLessEqual(max(resolutions), 15)

    def test_body_combines_filters_and_tile_bounds(self):
        from django.http import QueryDict
        body = grid_tile_body(QueryDict('countries=US'), 2, 1, 1)

        filters = body['query']['bool']['filter']
        self.assertEqual(filters[0], {'terms': {'country_code': ['US']}})
        self.assertIn('geo_bounding_box', filters[1])
        self.assertEqual(body['size'], 0)


@override_settings(ALLOWED_HOSTS=['testserver', '.allowed.org'])
class TileViewTest(FacilityFactoryMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client_mock = MagicMock()
        patcher = patch('api.facility_search.backend.client',
                        return_value=self.client_mock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, backend, 'breaker', backend.CircuitBreaker())
        self.create_facility(location=Point(10, 20))

    def tile(self, query=''):
        url = reverse('tile', kwargs={
            'layer': 'facilitygrid', 'cachekey': '1-1', 'z': 0, 'x': 0,
            'y': 0, 'ext': 'pbf'})
        return self.client.get(url + query, HTTP_REFERER='http://allowed.org/')

    @override_switch('vector_tile', active=True)
    def test_switch_off_uses_postgis(self):
        response = self.tile()

        self.assertEqual(response.status_code, 200)
        self.client_mock.search.assert_not_called()

    @override_switch('vector_tile', active=True)
    @override_switch(backend.TILES_SWITCH, active=True)
    def test_switch_on_serves_the_opensearch_grid(self):
        self.client_mock.search.return_value = {'aggregations': {'grid': {
            'buckets': [GridTileTest.bucket(None, 10, 20, 7)]}}}

        response = self.tile()

        features = decode_tile(response.content)['facilitygrid']['features']
        self.assertEqual(features[0]['properties']['count'], 7)

    @override_switch('vector_tile', active=True)
    @override_switch(backend.TILES_SWITCH, active=True)
    def test_errors_and_unsupported_params_fall_back(self):
        self.client_mock.search.side_effect = ConnectionTimeout(
            'TIMEOUT', 'slow', None)
        self.assertEqual(self.tile().status_code, 200)

        self.client_mock.search.reset_mock()
        self.assertEqual(self.tile('?embed=1').status_code, 200)
        self.client_mock.search.assert_not_called()


@requires_opensearch
class LiveGridTileTest(LiveIndexMixin, FacilityFactoryMixin, TestCase):
    def test_counts_cover_every_facility_in_the_tile(self):
        from django.http import QueryDict
        for i in range(5):
            self.create_facility(name=f'F{i}', location=Point(10 + i * 0.01,
                                                              20))
        self.create_facility(name='Far', location=Point(-120, -40),
                             country_code='CN')
        rebuild(self.client, replicas=0, report=lambda _: None)

        world = decode_tile(grid_tile(self.client, QueryDict(''), 0, 0, 0,
                                      timeout=10))
        filtered = decode_tile(grid_tile(
            self.client, QueryDict('countries=CN'), 0, 0, 0, timeout=10))

        counts = [f['properties']['count']
                  for f in world['facilitygrid']['features']]
        self.assertEqual(sum(counts), 6)
        self.assertEqual(
            [f['properties']['count']
             for f in filtered['facilitygrid']['features']], [1])
