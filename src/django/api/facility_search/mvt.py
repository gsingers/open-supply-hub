"""
A minimal Mapbox Vector Tile (spec v2.1) encoder for point layers.

The grid layer only needs points with a few numeric properties, which is
a few dozen lines of protobuf. Written in-house rather than taking the
`mapbox-vector-tile` package, which would pull in numpy, shapely and
pyclipper and downgrade protobuf below what the Google client libraries
here require.
"""
import struct
from typing import Dict, Iterable, List, Tuple

EXTENT = 4096

# Field numbers from vector_tile.proto.
TILE_LAYERS = 3
LAYER_NAME, LAYER_FEATURES, LAYER_KEYS = 1, 2, 3
LAYER_VALUES, LAYER_EXTENT, LAYER_VERSION = 4, 5, 15
FEATURE_TAGS, FEATURE_TYPE, FEATURE_GEOMETRY = 2, 3, 4
VALUE_STRING, VALUE_DOUBLE, VALUE_INT, VALUE_BOOL = 1, 3, 4, 7
GEOM_POINT = 1
CMD_MOVE_TO = 1

WIRE_VARINT, WIRE_FIXED64, WIRE_BYTES = 0, 1, 2

Point = Tuple[int, int]
Properties = Dict[str, object]


def _varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64  # int64 two's complement
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _zigzag(value: int) -> int:
    return (value << 1) ^ (value >> 63)


def _key(field: int, wire_type: int) -> bytes:
    return _varint((field << 3) | wire_type)


def _bytes_field(field: int, payload: bytes) -> bytes:
    return _key(field, WIRE_BYTES) + _varint(len(payload)) + payload


def _varint_field(field: int, value: int) -> bytes:
    return _key(field, WIRE_VARINT) + _varint(value)


def _packed(field: int, values: Iterable[int]) -> bytes:
    return _bytes_field(field, b''.join(_varint(v) for v in values))


def _value(value) -> bytes:
    if isinstance(value, bool):
        return _varint_field(VALUE_BOOL, int(value))
    if isinstance(value, int):
        return _varint_field(VALUE_INT, value)
    if isinstance(value, float):
        return _key(VALUE_DOUBLE, WIRE_FIXED64) + struct.pack('<d', value)
    return _bytes_field(VALUE_STRING, str(value).encode('utf-8'))


def _point_geometry(point: Point) -> List[int]:
    x, y = point
    return [(CMD_MOVE_TO & 0x7) | (1 << 3), _zigzag(x), _zigzag(y)]


def encode_point_layer(name: str,
                       features: Iterable[Tuple[Point, Properties]],
                       extent: int = EXTENT) -> bytes:
    """
    One tile with one layer of point features. Points are tile pixel
    coordinates in [0, extent], y pointing down.
    """
    keys: List[str] = []
    values: List[object] = []
    key_index: Dict[str, int] = {}
    value_index: Dict[Tuple[type, object], int] = {}
    encoded_features = []
    for point, properties in features:
        tags = []
        for key, value in properties.items():
            if key not in key_index:
                key_index[key] = len(keys)
                keys.append(key)
            value_id = (type(value), value)
            if value_id not in value_index:
                value_index[value_id] = len(values)
                values.append(value)
            tags += [key_index[key], value_index[value_id]]
        encoded_features.append(_bytes_field(LAYER_FEATURES, (
            _packed(FEATURE_TAGS, tags)
            + _varint_field(FEATURE_TYPE, GEOM_POINT)
            + _packed(FEATURE_GEOMETRY, _point_geometry(point))
        )))
    if not encoded_features:
        return b''  # ST_AsMVT also returns an empty tile for no rows
    layer = (
        _varint_field(LAYER_VERSION, 2)
        + _bytes_field(LAYER_NAME, name.encode('utf-8'))
        + b''.join(encoded_features)
        + b''.join(_bytes_field(LAYER_KEYS, k.encode('utf-8'))
                   for k in keys)
        + b''.join(_bytes_field(LAYER_VALUES, _value(v)) for v in values)
        + _varint_field(LAYER_EXTENT, extent)
    )
    return _bytes_field(TILE_LAYERS, layer)
