import logging
from pathlib import Path
from typing import Dict, List

import gevent
from gevent.queue import Queue

from rotkehlchen.chain.substrate.typing import SubstrateChain
from rotkehlchen.errors import RemoteError
from rotkehlchen.greenlets import GreenletManager
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.user_messages import MessagesAggregator
from substrate_indexer.dbwriter import DBWriter
from substrate_indexer.indexer import Indexer
from substrate_indexer.typing_events import EventStartIndexerData, EventStartIndexingData

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

# TODO: DB connection
DB_PATH = Path('/home/victor/.local/share/rotki/data')
DB_PASSWORD = '1234'


class Manager():
    def __init__(self) -> None:
        self.indexer_id_counter = 0
        self.dbwriter_id_counter = 0
        self.indexer_id_to_indexer: Dict[int, Indexer] = {}
        self.dbwriter_id_to_dbwriter: Dict[int, DBWriter] = {}
        self.substrate_chain_to_queue: Dict[SubstrateChain, Queue] = {}
        self.substrate_chain_to_dbwriter: Dict[SubstrateChain, DBWriter] = {}
        self.msg_aggregator = MessagesAggregator()
        self.greenlet_manager_indexers = GreenletManager(msg_aggregator=self.msg_aggregator)
        self.greenlet_manager_dbwriters = GreenletManager(msg_aggregator=self.msg_aggregator)
        log.info('Created Manager')

    def create_dbwriter(
            self,
            sid: str,
            start_indexer_data: EventStartIndexerData,
    ) -> None:
        """
        May raise:
        - ModuleInitializationFailure
        - RemoteError
        """
        substrate_chain = start_indexer_data.substrate_chain
        if substrate_chain in self.substrate_chain_to_dbwriter:
            raise RemoteError(
                f'Chain {substrate_chain} already has a DBWriter instance: '
                f'{self.substrate_chain_to_dbwriter[substrate_chain].name}',
            )

        substrate_chain_queue = Queue()
        dbwriter = DBWriter(
            instance_id=self.dbwriter_id_counter,
            queue=substrate_chain_queue,
            sid=sid,
            start_indexer_data=start_indexer_data,
            user_data_dir=DB_PATH,
            password=DB_PASSWORD,
            msg_aggregator=self.msg_aggregator,
        )
        log.debug(
            f'Created {dbwriter.name}',
            sid=sid,
            start_indexer_data=start_indexer_data,
        )
        self.substrate_chain_to_queue[substrate_chain] = substrate_chain_queue
        self.dbwriter_id_to_dbwriter[dbwriter.instance_id] = dbwriter
        self.substrate_chain_to_dbwriter[substrate_chain] = dbwriter
        self.greenlet_manager_dbwriters.spawn_and_track(
            after_seconds=None,
            task_name=dbwriter.name,
            exception_is_error=False,
            method=dbwriter.start,
        )
        self.dbwriter_id_counter += 1

    def create_indexer(
            self,
            sid: str,
            start_indexing_data: EventStartIndexingData,
    ) -> None:
        """
        May raise:
        - ModuleInitializationFailure
        - RemoteError
        """
        substrate_chain = start_indexing_data.substrate_chain
        try:
            queue = self.substrate_chain_to_queue[substrate_chain]
            dbwriter = self.substrate_chain_to_dbwriter[substrate_chain]
        except KeyError as e:
            msg = f'An instance of Queue and DBWriter are required for {substrate_chain}'
            log.error(
                msg,
                error=f'Missing key: {str(e)}.',
                sid=sid,
                start_indexing_data=start_indexing_data,
            )
            raise RemoteError(msg) from e

        indexer = Indexer(
            instance_id=self.indexer_id_counter,
            queue=queue,
            sid=sid,
            url=dbwriter.start_indexer_data.url,
            start_indexing_data=start_indexing_data,
        )
        log.debug(
            f'Created {indexer.name}',
            sid=sid,
            url=dbwriter.start_indexer_data.url,
            start_indexing_data=start_indexing_data,
        )
        self.indexer_id_to_indexer[indexer.instance_id] = indexer
        self.greenlet_manager_indexers.spawn_and_track(
            after_seconds=None,
            task_name=indexer.name,
            exception_is_error=False,
            method=indexer.start,
        )
        self.indexer_id_counter += 1

    def stop_dbwriters(self, instance_ids: List[int]) -> None:
        log.info(f'Stopping dbwriters with IDs: {",".join(str(id_) for id_ in instance_ids)}')
        # Getting the task names
        unique_dbwriter_names = {dbwriter.name for dbwriter in self.dbwriter_id_to_dbwriter.values()}  # noqa: E501
        # Kill related greenlets
        removed_task_names: List[str] = []
        for dbwriter_greenlet in list(self.greenlet_manager_dbwriters.greenlets):
            if dbwriter_greenlet.task_name in unique_dbwriter_names:
                task_name = dbwriter_greenlet.task_name
                gevent.kill(dbwriter_greenlet)
                self.greenlet_manager_dbwriters.greenlets.remove(dbwriter_greenlet)
                removed_task_names.append(task_name)

        log.info(f'Stopped DBWriter greenlets: {",".join(removed_task_names)}')
        # Remove DBWriter instances
        removed_instance_ids: List[int] = []
        for instance_id in instance_ids:
            if instance_id in self.dbwriter_id_to_dbwriter:
                # NB: Disconnect DB
                self.dbwriter_id_to_dbwriter[instance_id].database.disconnect()
                self.dbwriter_id_to_dbwriter.pop(instance_id)
                removed_instance_ids.append(instance_id)

        log.info(f'Removed DBWriter instances: {",".join(str(id_) for id_ in removed_instance_ids)}')  # noqa: E501

    def stop_indexers(self, instance_ids: List[int]) -> None:
        log.info(f'Stopping indexers with IDs: {",".join(str(id_) for id_ in instance_ids)}')
        # Getting the task names
        unique_indexer_names = {indexer.name for indexer in self.indexer_id_to_indexer.values()}  # noqa: E501
        # Kill related greenlets
        removed_task_names: List[str] = []
        for indexer_greenlet in list(self.greenlet_manager_indexers.greenlets):
            if indexer_greenlet.task_name in unique_indexer_names:
                task_name = indexer_greenlet.task_name
                gevent.kill(indexer_greenlet)
                self.greenlet_manager_indexers.greenlets.remove(indexer_greenlet)
                removed_task_names.append(task_name)

        log.info(f'Stopped Indexer greenlets: {",".join(removed_task_names)}')
        # Remove Indexer instances
        removed_instance_ids: List[int] = []
        for instance_id in instance_ids:
            if instance_id in self.indexer_id_to_indexer:
                self.indexer_id_to_indexer.pop(instance_id)
                removed_instance_ids.append(instance_id)

        log.info(f'Removed Indexer instances: {",".join(str(id_) for id_ in removed_instance_ids)}')  # noqa: E501

    def stop_sid_tasks(self, sid: str) -> None:
        """Stop all the Indexer and DBWriter tasks related to the given SID"""
        log.info(f'Stopping tasks with SID: {sid}')
        dbwriter_instance_ids: List[int] = []
        indexer_instance_ids: List[int] = []
        for dbwriter in self.dbwriter_id_to_dbwriter.values():
            if dbwriter.sid == sid:
                dbwriter_instance_ids.append(dbwriter.instance_id)
        for indexer in self.indexer_id_to_indexer.values():
            if indexer.sid == sid:
                indexer_instance_ids.append(indexer.instance_id)

        self.stop_indexers(indexer_instance_ids)
        self.stop_dbwriters(dbwriter_instance_ids)
        log.info(f'Stopped tasks with SID: {sid}')

    def shutdown(self) -> None:
        log.info('Starting Manager shutdown')
        self.greenlet_manager_indexers.clear()
        self.greenlet_manager_dbwriters.clear()
        for dbwriter in self.dbwriter_id_to_dbwriter.values():
            dbwriter.database.disconnect()
        log.info('Finished Manager shutdown')
