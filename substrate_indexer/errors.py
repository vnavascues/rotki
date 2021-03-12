from enum import Enum
from typing import Union


class DBWriterError(Enum):
    E0001 = 1

    def __str__(self) -> str:
        if self == DBWriterError.E0001:
            return 'deserialize_substrate_extrinsics_0001'
        raise AssertionError(f'Unexpected DBWriterError: {type(self)}.')

    def message(self) -> str:
        if self == DBWriterError.E0001:
            return 'Failed to deserialize block substrate extrinsics'
        raise AssertionError(f'Unexpected DBWriterError: {type(self)}.')


class IndexerError(Enum):
    E0001 = 1

    def __str__(self) -> str:
        if self == IndexerError.E0001:
            return 'get_address_block_extrinsics_data_0001'
        raise AssertionError(f'Unexpected IndexerError: {type(self)}.')

    def message(self) -> str:
        if self == IndexerError.E0001:
            return 'Failed to get the block extrinsics data'
        raise AssertionError(f'Unexpected IndexerError: {type(self)}.')


class StartIndexerError(Enum):
    E0001 = 1
    E0002 = 2

    def __str__(self) -> str:
        if self == StartIndexerError.E0001:
            return 'start_indexer_0001'
        if self == StartIndexerError.E0002:
            return 'start_indexer_0002'
        raise AssertionError(f'Unexpected StartIndexerError: {type(self)}.')

    def message(self) -> str:
        if self == StartIndexerError.E0001:
            return 'Failed to deserialize data'
        if self == StartIndexerError.E0002:
            return 'Failed to create dbwriter'
        raise AssertionError(f'Unexpected StartIndexerError: {type(self)}.')


class StartIndexingError(Enum):
    E0001 = 1
    E0002 = 2

    def __str__(self) -> str:
        if self == StartIndexingError.E0001:
            return 'start_indexing_0001'
        if self == StartIndexingError.E0002:
            return 'start_indexing_0002'
        raise AssertionError(f'Unexpected StartIndexingError: {type(self)}.')

    def message(self) -> str:
        if self == StartIndexingError.E0001:
            return 'Failed to deserialize data'
        if self == StartIndexingError.E0002:
            return 'Failed to create indexer'
        raise AssertionError(f'Unexpected StartIndexingError: {type(self)}.')


EventError = Union[
    DBWriterError,
    IndexerError,
    StartIndexerError,
    StartIndexingError,
]
