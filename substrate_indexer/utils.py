import logging

import requests
from substrateinterface.base import SubstrateInterface
from substrateinterface.exceptions import SubstrateRequestException
from websocket import WebSocketException

from rotkehlchen.errors import RemoteError
from rotkehlchen.logging import RotkehlchenLogsAdapter

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


BLOCK_INHERENT_DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%S'


def get_node_interface(
        url: str,
        type_registry_preset: str,
        location: str,
) -> SubstrateInterface:
    """May raise RemoteError"""
    try:
        node_interface = SubstrateInterface(
            url=url,
            type_registry_preset=type_registry_preset,
            use_remote_preset=True,
        )
    except (requests.exceptions.RequestException, WebSocketException) as e:
        message = (
            f'{location} could not connect to node at endpoint: {url}. '
            f'Connection error: {str(e)}.'
        )
        log.error(message)
        raise RemoteError(message) from e
    except (FileNotFoundError, ValueError, TypeError) as e:
        message = (
            f'{location} could not connect to node at endpoint: {url}. '
            f'Unexpected error during SubstrateInterface instantiation: {str(e)}.'
        )
        log.error(message)
        raise RemoteError('Invalid SubstrateInterface instantiation') from e

    # The following methods are only needed to call once. Their responses are
    # stored in the instance, saving further RPC calls.
    try:
        node_interface.chain  # pylint: disable=pointless-statement
        node_interface.properties  # pylint: disable=pointless-statement
        node_interface.token_decimals  # pylint: disable=pointless-statement
        node_interface.token_symbol  # pylint: disable=pointless-statement
        node_interface.ss58_format  # pylint: disable=pointless-statement
    except (
        requests.exceptions.RequestException,
        SubstrateRequestException,
        WebSocketException,
    ) as e:
        message = (
            f'{location} failed to request the chain properties at endpoint: {url} '
            f'due to: {str(e)}'
        )
        raise RemoteError(message) from e

    return node_interface
