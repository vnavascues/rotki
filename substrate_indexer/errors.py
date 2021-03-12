from enum import Enum
from typing import Union


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


EventError = Union[StartIndexerError, StartIndexingError]
