import logging
from typing import Any, Dict

import gevent
from flask_socketio import socketio

from rotkehlchen.chain.substrate.typing import BlockNumber, SubstrateChain
from rotkehlchen.chain.substrate.typing_addresses import KusamaAddress
from rotkehlchen.logging import RotkehlchenLogsAdapter
from substrate_indexer.typing_events import (
    ClientEvent,
    EventStartIndexerData,
    EventStartIndexingData,
    ServerEvent,
)

SOCKETIO_CLIENT_HOST = 'localhost'
SOCKETIO_CLIENT_PORT = 5000

ARCHIVE_NODE_URL = 'wss://kusama-rpc.polkadot.io'  # NB: make sure it uses websocket
# BLOCK_NUMBER_START_AT = 1
BLOCK_NUMBER_START_AT = BlockNumber(5662971)
KUSAMA_TEST_ADDRESS = KusamaAddress('DJXRnqb3aTRpQfZtfZKFB3rXrDcdKjyS7C3BrrB5oWMDrxJ')


logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%d/%m/%Y %H:%M:%S %Z',
)
logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

log.debug('Run SocketIO client')
sio = socketio.Client(logger=True)
sio.connect(f'http://{SOCKETIO_CLIENT_HOST}:{SOCKETIO_CLIENT_PORT}')

while not sio.namespaces:
    log.debug('Waiting for SocketIO namespaces')
    gevent.sleep(1)

# NB: currently this SID does not equal to the one the SocketIO server assigns
# to this client once it stablishes connection
log.debug(f'Client sid: {sio.sid}')


@sio.on(str(ClientEvent.CONNECT_ERROR))  # type: ignore # untyped decorator
def connect_error(error: str) -> None:
    log.warning(f'Client with SID {sio.sid} got a connection error due to: {error}.')
    sio.disconnect()


@sio.on(str(ClientEvent.DISCONNECT))  # type: ignore # untyped decorator
def disconnect() -> None:
    log.warning(f'Client with SID {sio.sid} has been disconnected by the server')
    sio.disconnect()


@sio.on(str(ClientEvent.SERVER_ERROR))  # type: ignore # untyped decorator
def server_error(data: Dict[str, Any]) -> None:
    log.error(f'Client with SID {sio.sid} got a server error: {data}')


@sio.on(str(ClientEvent.SERVER_SUCCESS))  # type: ignore # untyped decorator
def server_success(data: Dict[str, Any]) -> None:
    log.info(f'Client with SID {sio.sid} successfully called event: {data}')


# NB: this sequence of events is just to showcase a client independently calling
# server events. An alternative to `gevent.sleep()` could be starting the
# indexing after successfully call `start_indexer`.
start_indexer_data = EventStartIndexerData(
    url=ARCHIVE_NODE_URL,
    substrate_chain=SubstrateChain.KUSAMA,
)
sio.emit(
    str(ServerEvent.START_INDEXER),
    start_indexer_data.serialize(),
)

gevent.sleep(3)

start_indexing_data = EventStartIndexingData(
    substrate_chain=SubstrateChain.KUSAMA,
    block_number_start_at=BLOCK_NUMBER_START_AT,
    address=KUSAMA_TEST_ADDRESS,
)
sio.emit(
    str(ServerEvent.START_INDEXING),
    start_indexing_data.serialize(),
)
