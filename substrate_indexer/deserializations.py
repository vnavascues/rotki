from typing import Any, Dict

from rotkehlchen.errors import DeserializationError
from rotkehlchen.serialization.deserialize import deserialize_timestamp_from_date
from rotkehlchen.typing import Timestamp
from substrate_indexer.utils import BLOCK_INHERENT_DATETIME_FORMAT


def deserialize_inherent_timestamp(
        value: Dict[str, Any],
        location: str,
) -> Timestamp:
    """May raise DeserializationError"""
    try:
        inherent_datetime = value['params'][0]['value']
    except (KeyError, ValueError) as e:
        msg = str(e)
        if isinstance(e, KeyError):
            msg = f'Missing key in value: {msg}'
        raise DeserializationError(msg) from e

    return deserialize_timestamp_from_date(
        date=inherent_datetime,
        formatstr=BLOCK_INHERENT_DATETIME_FORMAT,
        location=location,
        skip_milliseconds=True,
    )
