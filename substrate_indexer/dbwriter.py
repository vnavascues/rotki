import logging
from pathlib import Path
from typing import List

import gevent
import requests
from gevent.queue import Queue
from substrateinterface import ExtrinsicReceipt
from substrateinterface.exceptions import ExtrinsicNotFound, SubstrateRequestException
from websocket import WebSocketException

from rotkehlchen.chain.substrate.typing import (
    SubstrateAddressBlockExtrinsicsData,
    SubstrateExtrinsic,
)
from rotkehlchen.db.dbhandler import DBHandler
from rotkehlchen.errors import ModuleInitializationFailure, RemoteError, SystemPermissionError
from rotkehlchen.fval import FVal
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.user_messages import MessagesAggregator
from substrate_indexer.typing_events import EventStartIndexerData
from substrate_indexer.utils import get_node_interface

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

REQUEST_RECEIPT_DATA_TIMES = 2
DBWRITER_SLEEP_SECONDS = 5
MIN_NUMBER_QUEUE_ITEMS_BEFORE_PROCESS = 10
MAX_NUMBER_QUEUE_ITEMS_TO_PROCESS = 10


"""
TODO:
- Exception handling: decide what to do with the greenlet, and notify the
SocketIO client.
"""


class DBWriter():

    def __init__(
            self,
            instance_id: int,
            queue: Queue,
            sid: str,
            start_indexer_data: EventStartIndexerData,
            user_data_dir: Path,
            password: str,
            msg_aggregator: MessagesAggregator,
    ) -> None:
        """
        TODO:
        - DB connection.
        - Exception handling (greenlets, notify SocketIO client)
        - Polish logs
        - Give a second thought to the retry logic
        - Staking logic

        May raise:
        - ModuleInitializationFailure
        - RemoteError
        """
        self.instance_id = instance_id
        self.queue = queue
        self.sid = sid
        self.start_indexer_data = start_indexer_data
        self.name = f'dbwriter_{self.instance_id}_{self.start_indexer_data.substrate_chain}'
        substrate_interface_attributes = self.start_indexer_data.substrate_chain.substrate_interface_attributes()  # noqa: E501
        try:
            self.database = self._connect_to_db(
                user_data_dir=user_data_dir,
                password=password,
                msg_aggregator=msg_aggregator,
            )
            self.node_interface = get_node_interface(
                url=self.start_indexer_data.url,
                type_registry_preset=substrate_interface_attributes.type_registry_preset,
                location=self.name,
            )
        except (RemoteError, SystemPermissionError) as e:
            log.error(
                f'{self.name} failed to initialise',
                error=str(e),
                sid=self.sid,
                start_indexer_data=self.start_indexer_data,
            )
            raise ModuleInitializationFailure(
                f'{self.name} failed to initialise due to: {str(e)}',
            ) from e

    @staticmethod
    def _connect_to_db(
            user_data_dir: Path,
            password: str,
            msg_aggregator: MessagesAggregator,
    ) -> DBHandler:
        database = DBHandler(
            user_data_dir=user_data_dir,
            password=password,
            msg_aggregator=msg_aggregator,
            initial_settings=None,
        )
        return database

    def _deserialize_substrate_extrinsics(
            self,
            address_block_extrinsics_data: SubstrateAddressBlockExtrinsicsData,
    ) -> List[ExtrinsicReceipt]:
        """May raise RemoteError"""
        extrinsics: List[SubstrateExtrinsic] = []
        block_number = address_block_extrinsics_data.block_number
        block_hash = address_block_extrinsics_data.block_hash
        for extrinsic in address_block_extrinsics_data.extrinsics:
            extrinsic_receipt = ExtrinsicReceipt(
                substrate=self.node_interface,
                extrinsic_hash=extrinsic.extrinsic_hash,
                block_hash=block_hash,
            )
            retries_left = REQUEST_RECEIPT_DATA_TIMES
            while retries_left >= 0:
                # `extrinsic_idx` makes RPC call via `get_block_extrinsics()`
                # `total_fee_amount` makes RPC call via `get_events()`
                try:
                    extrinsic_index = extrinsic_receipt.extrinsic_idx
                    total_fee_amount = extrinsic_receipt.total_fee_amount
                except (
                    requests.exceptions.RequestException,
                    ExtrinsicNotFound,
                    SubstrateRequestException,
                    ValueError,
                    WebSocketException,
                ) as e:
                    if retries_left > 0:
                        retries_left -= 1

                    msg = (
                        f'{self.name} failed to request extrinsic data after retrying '
                        f'{REQUEST_RECEIPT_DATA_TIMES} times'
                    )
                    log.error(
                        msg,
                        error=str(e),
                        sid=self.sid,
                        start_indexer_data=self.start_indexer_data,
                        address_block_extrinsics_data=address_block_extrinsics_data,
                        extrinsic=extrinsic,
                    )
                    raise RemoteError(f'{self.name} failed to request extrinsic data') from e

                break

            # NB: `total_fee_amount` should not be None in the kind of extrinsics
            # we are processing
            if total_fee_amount is None:
                log.error(
                    f'{self.name} got an unexpected fee amount. A value is required',
                    sid=self.sid,
                    start_indexer_data=self.start_indexer_data,
                    address_block_extrinsics_data=address_block_extrinsics_data,
                    extrinsic=extrinsic,
                )
                raise RemoteError(f'{self.name} failed to calculate fee amount')

            fee = total_fee_amount / FVal('10') ** self.node_interface.token_decimals
            extrinsic = SubstrateExtrinsic(
                chain_id=self.node_interface.chain,
                block_timestamp=address_block_extrinsics_data.block_timestamp,
                block_number=block_number,
                block_hash=block_hash,
                extrinsic_index=extrinsic_index,
                extrinsic_hash=extrinsic.extrinsic_hash,
                call_module=extrinsic.call_module.name,
                call_module_function=extrinsic.call.name,
                params=extrinsic.params,
                account_id=address_block_extrinsics_data.address_public_key,
                address=address_block_extrinsics_data.address,
                nonce=extrinsic.nonce.value,
                fee=fee,
            )
            extrinsics.append(extrinsic)

        return extrinsics

    def start(self) -> None:
        """May raise RemoteError"""
        while True:
            if len(self.queue) < MIN_NUMBER_QUEUE_ITEMS_BEFORE_PROCESS:
                log.debug(f'{self.name} will wait {DBWRITER_SLEEP_SECONDS}s')
                gevent.sleep(DBWRITER_SLEEP_SECONDS)
                continue

            all_substrate_extrinsics = []
            for count, address_block_extrinsics_data in enumerate(self.queue, start=1):
                log.debug(
                    f'{self.name} got extrinsic data from the queue',
                    address_block_extrinsics_data=address_block_extrinsics_data,
                )
                substrate_extrinsics = self._deserialize_substrate_extrinsics(
                    address_block_extrinsics_data=address_block_extrinsics_data,
                )
                all_substrate_extrinsics.extend(substrate_extrinsics)
                if count == MAX_NUMBER_QUEUE_ITEMS_TO_PROCESS:
                    break

            if len(all_substrate_extrinsics) != 0:
                log.debug(f'{self.name} adding {len(all_substrate_extrinsics)} extrinsics in the DB')  # noqa: E501
                self.database.add_substrate_extrinsics(all_substrate_extrinsics)
