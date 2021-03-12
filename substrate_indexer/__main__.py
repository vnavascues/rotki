from gevent import monkey  # isort:skip # noqa
monkey.patch_all()  # isort:skip # noqa

import logging
import os
import signal
from typing import Any, Dict

import flask_socketio
import gevent
from flask import Flask, request
from flask_socketio import SocketIO

from rotkehlchen.errors import DeserializationError, ModuleInitializationFailure, RemoteError
from rotkehlchen.logging import RotkehlchenLogsAdapter
from substrate_indexer.errors import StartIndexerError, StartIndexingError
from substrate_indexer.manager import Manager
from substrate_indexer.typing_events import (
    ClientEvent,
    EventErrorData,
    EventStartIndexerData,
    EventStartIndexingData,
    EventSuccessData,
    ServerEvent,
)

SOCKETIO_SERVER_HOST = 'localhost'
SOCKETIO_SERVER_PORT = 5000

logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%d/%m/%Y %H:%M:%S %Z',
)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)
logging.getLogger('urllib3.connectionpool').setLevel(logging.CRITICAL)
logging.getLogger('substrateinterface.base').setLevel(logging.CRITICAL)
logging.getLogger('geventwebsocket.handler').setLevel(logging.CRITICAL)

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'rotki123'
socketio = SocketIO(app, logger=False, engineio_logger=False)
manager = Manager(socketio=socketio)


def emit_error(
        event: ClientEvent,
        event_error_data: EventErrorData,
        sid: str,
) -> None:
    """
    TODO: PoC for communicating errors to a particular client via a generic
    event on the client side.
    """
    log.info(f'Emitting error to client with SID: {sid}')
    socketio.emit(str(event), event_error_data.serialize(), to=sid)


@socketio.on(str(ServerEvent.CONNECT))  # type: ignore # untyped decorator
def connect() -> None:
    sid = request.sid  # type: ignore # request has sid
    log.info(f'Client connected. Assigned SID: {sid}')


@socketio.on(str(ServerEvent.DISCONNECT))  # type: ignore # untyped decorator
def disconnect() -> None:
    sid = request.sid  # type: ignore # request has sid
    log.info(f'Client with SID {sid} disconnected. Stopping its tasks')
    manager.stop_sid_tasks(sid)
    log.info(f'Stopped tasks of client with SID {sid}')


@socketio.on_error()  # type: ignore # untyped decorator
def error_handler(error: Exception) -> None:
    """This function deals with any unhandled exception

    TODO: decide what to do, currently does a shutdown.
    """
    log.info(f'Unexpected exception: {str(error)}. Disconnecting clients and shutting down')
    flask_socketio.disconnect()
    manager.shutdown()
    socketio.stop()


@socketio.on(str(ServerEvent.START_INDEXER))  # type: ignore # untyped decorator
def start_indexer(data: Dict[str, Any]) -> None:
    sid = request.sid  # type: ignore # request has sid
    try:
        start_indexer_data = EventStartIndexerData.deserialize_from_data(data)
    except DeserializationError as e:
        log.error(
            f'{StartIndexerError.E0001}',
            error=str(e),
            sid=sid,
            data=data,
        )
        event_error_data = EventErrorData(
            error=StartIndexerError.E0001,
            detail=str(e),
        )
        emit_error(
            event=ClientEvent.SERVER_ERROR,
            event_error_data=event_error_data,
            sid=sid,
        )
        return None

    try:
        manager.create_dbwriter(
            sid=sid,
            start_indexer_data=start_indexer_data,
        )
    except (ModuleInitializationFailure, RemoteError) as e:
        log.error(
            f'{StartIndexerError.E0002}',
            error=str(e),
            sid=sid,
            start_indexer_data=start_indexer_data,
        )
        event_error_data = EventErrorData(
            error=StartIndexerError.E0002,
            detail=str(e),
        )
        emit_error(
            event=ClientEvent.SERVER_ERROR,
            event_error_data=event_error_data,
            sid=sid,
        )
        return None

    event_success_data = EventSuccessData(event=ServerEvent.START_INDEXER)
    socketio.emit(str(ClientEvent.SERVER_SUCCESS), event_success_data.serialize(), to=sid)
    return None


@socketio.on(str(ServerEvent.START_INDEXING))  # type: ignore # untyped decorator
def start_indexing(data: Dict[str, Any]) -> None:
    sid = request.sid  # type: ignore # request has sid
    try:
        start_indexing_data = EventStartIndexingData.deserialize_from_data(data)
    except DeserializationError as e:
        log.error(
            f'{StartIndexingError.E0001}',
            error=str(e),
            sid=sid,
            data=data,
        )
        event_error_data = EventErrorData(
            error=StartIndexingError.E0001,
            detail=str(e),
        )
        emit_error(
            event=ClientEvent.SERVER_ERROR,
            event_error_data=event_error_data,
            sid=sid,
        )
        return None

    try:
        manager.create_indexer(
            sid=sid,
            start_indexing_data=start_indexing_data,
        )
    except (ModuleInitializationFailure, RemoteError) as e:
        log.error(
            f'{StartIndexingError.E0002}',
            error=str(e),
            sid=sid,
            start_indexing_data=start_indexing_data,
        )
        event_error_data = EventErrorData(
            error=StartIndexingError.E0002,
            detail=str(e),
        )
        emit_error(
            event=ClientEvent.SERVER_ERROR,
            event_error_data=event_error_data,
            sid=sid,
        )
        return None

    event_success_data = EventSuccessData(event=ServerEvent.START_INDEXING)
    socketio.emit(str(ClientEvent.SERVER_SUCCESS), event_success_data.serialize(), to=sid)
    return None


if __name__ == '__main__':

    def shutdown() -> None:
        manager.shutdown()
        socketio.stop()  # NB: disconnects all clients

    if os.name != 'nt':
        gevent.hub.signal(signal.SIGQUIT, shutdown)
    gevent.hub.signal(signal.SIGINT, shutdown)
    gevent.hub.signal(signal.SIGTERM, shutdown)

    log.info('Run SocketIO server')
    socketio.run(
        app,
        host=SOCKETIO_SERVER_HOST,
        port=SOCKETIO_SERVER_PORT,
        use_reloader=False,
        log_output=True,
    )
