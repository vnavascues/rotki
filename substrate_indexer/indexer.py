import logging
from typing import List, Optional

import requests
from flask_socketio import SocketIO
from gevent.queue import Queue
from scalecodec.block import Extrinsic
from substrateinterface.exceptions import SubstrateRequestException
from websocket import WebSocketException

from rotkehlchen.chain.substrate.typing import BlockNumber, SubstrateAddressBlockExtrinsicsData
from rotkehlchen.errors import DeserializationError, ModuleInitializationFailure, RemoteError
from rotkehlchen.logging import RotkehlchenLogsAdapter
from substrate_indexer.deserializations import deserialize_inherent_timestamp
from substrate_indexer.errors import IndexerError
from substrate_indexer.typing_events import ClientEvent, EventErrorData, EventStartIndexingData
from substrate_indexer.utils import get_node_interface

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


REQUEST_BLOCK_RETRY_TIMES = 2
LOG_CURRENT_BLOCK_NUMBER_EVERY = 1000
# Spec 1050, introduces consistent staking events in Kusama
KUSAMA_STAKING_EVENTS_BLOCK_NUMBER_START_AT = 1375086


class Indexer():

    def __init__(
            self,
            instance_id: int,
            queue: Queue,
            sid: str,
            url: str,
            start_indexing_data: EventStartIndexingData,
            socketio: SocketIO,
    ) -> None:
        """
        TODO:
        - Exception handling (greenlets, notify SocketIO client)
        - Polish logs
        - Give a second thought to the retry logic
        - Staking logic

        May raise:
        - ModuleInitializationFailure
        - RemoteError
        """
        self.socketio = socketio
        self.instance_id = instance_id
        self.queue = queue
        self.sid = sid
        self.url = url
        self.start_indexing_data = start_indexing_data
        self.name = f'indexer_{self.instance_id}_{self.start_indexing_data.substrate_chain}'
        substrate_interface_attributes = self.start_indexing_data.substrate_chain.substrate_interface_attributes()  # noqa: E501
        try:
            self.node_interface = get_node_interface(
                url=url,
                type_registry_preset=substrate_interface_attributes.type_registry_preset,
                location=self.name,
            )
        except RemoteError as e:
            log.error(
                f'{self.name} failed to initialise',
                error=str(e),
                sid=self.sid,
                url=url,
                start_indexing_data=self.start_indexing_data,
            )
            raise ModuleInitializationFailure(
                f'{self.name} failed to initialise due to: {str(e)}',
            ) from e

    def _get_address_block_extrinsics_data(
            self,
            block_number: BlockNumber,
    ) -> Optional[SubstrateAddressBlockExtrinsicsData]:
        """
        May raise:
        - DeserializationError
        - RemoteError
        """
        address_block_extrinsics: List[Extrinsic] = []
        retries_left = REQUEST_BLOCK_RETRY_TIMES
        address = self.start_indexing_data.address
        address_public_key = self.start_indexing_data.address_public_key
        # TODO: restore the statement below
        # address_account_id = address_public_key.replace('0x', '')
        while retries_left >= 0:
            try:
                block_extrinsics = self.node_interface.get_block_extrinsics(block_id=block_number)
            except (
                requests.exceptions.RequestException,
                SubstrateRequestException,
                ValueError,
                WebSocketException,
            ) as e:
                if retries_left > 0:
                    retries_left -= 1
                    continue

                msg = (
                    f'{self.name} failed to request block {block_number} extrinsics '
                    f'after retrying {REQUEST_BLOCK_RETRY_TIMES} times'
                )
                log.error(
                    msg,
                    error=str(e),
                    sid=self.sid,
                    url=self.url,
                    start_indexing_data=self.start_indexing_data,
                )
                raise RemoteError(f'{msg} due to: {str(e)}') from e

            break

        for block_extrinsic in block_extrinsics:
            if (
                block_extrinsic.extrinsic_hash is None or
                block_extrinsic.contains_transaction is False
            ):
                continue

            # TODO: restore the statement below
            # if block_extrinsic.address.account_id == address_account_id:
            #     address_block_extrinsics.append(block_extrinsic)
            # TODO: delete the statement below
            address_block_extrinsics.append(block_extrinsic)

        if len(address_block_extrinsics) == 0:
            return None

        block_inherent = block_extrinsics[0]
        try:
            block_timestamp = deserialize_inherent_timestamp(
                value=block_inherent.value,
                location=self.name,
            )
        except DeserializationError as e:
            msg = f'{self.name} failed to deserialize block {block_number} inherent timestamp'
            log.error(
                msg,
                error=str(e),
                block_inherent=block_inherent,
                sid=self.sid,
                url=self.url,
                start_indexing_data=self.start_indexing_data,
            )
            raise DeserializationError(f'{msg} due to: {str(e)}') from e

        # NB: `block_hash` is available after requesting the extrinsics by `block_number`
        # and it does not require an extra RPC call.
        address_block_extrinsics_data = SubstrateAddressBlockExtrinsicsData(
            address=address,
            address_public_key=address_public_key,
            block_number=block_number,
            block_hash=self.node_interface.block_hash,
            block_timestamp=block_timestamp,
            extrinsics=address_block_extrinsics,
        )
        return address_block_extrinsics_data

    def start(self) -> None:
        """May raise RemoteError"""
        block_number = self.start_indexing_data.block_number_start_at
        log.debug(f'{self.name} starting at block {block_number}')
        while True:
            if block_number % LOG_CURRENT_BLOCK_NUMBER_EVERY == 0:
                log.debug(f'{self.name} requesting block {block_number}')

            try:
                address_block_extrinsics_data = self._get_address_block_extrinsics_data(
                    block_number=block_number,
                )
            except (DeserializationError, RemoteError) as e:
                event_error_data = EventErrorData(
                    error=IndexerError.E0001,
                    detail=str(e),
                )
                self.socketio.emit(
                    str(ClientEvent.SERVER_ERROR),
                    event_error_data.serialize(),
                    to=self.sid,
                )
                return None

            if address_block_extrinsics_data is not None:
                log.debug(
                    f'{self.name} put extrinsic data in the queue',
                    address_block_extrinsics_data=address_block_extrinsics_data,
                )
                self.queue.put(address_block_extrinsics_data)

            block_number += BlockNumber(1)  # type: ignore # int expression
