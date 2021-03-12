from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, NamedTuple

from rotkehlchen.chain.substrate.typing import BlockNumber, SubstrateChain
from rotkehlchen.chain.substrate.typing_addresses import SubstrateAddress, SubstratePublicKey
from rotkehlchen.chain.substrate.utils import (
    get_substrate_public_key_from_substrate_address,
    is_valid_substrate_address,
)
from rotkehlchen.errors import DeserializationError
from substrate_indexer.errors import EventError


class ClientEvent(Enum):
    CONNECT = 1
    CONNECT_ERROR = 2
    DISCONNECT = 3
    SERVER_ERROR = 4
    SERVER_SUCCESS = 5

    def __str__(self) -> str:
        if self == ClientEvent.CONNECT:
            return 'connect'
        if self == ClientEvent.CONNECT_ERROR:
            return 'connect_error'
        if self == ClientEvent.DISCONNECT:
            return 'disconnect'
        if self == ClientEvent.SERVER_ERROR:
            return 'server_error'
        if self == ClientEvent.SERVER_SUCCESS:
            return 'server_success'
        raise AssertionError(f'Unexpected ClientEvent: {type(self)}.')


class ServerEvent(Enum):
    CONNECT = 1
    DISCONNECT = 2
    START_INDEXER = 3
    START_INDEXING = 4

    def __str__(self) -> str:
        if self == ServerEvent.CONNECT:
            return 'connect'
        if self == ServerEvent.DISCONNECT:
            return 'disconnect'
        if self == ServerEvent.START_INDEXER:
            return 'start_indexer'
        if self == ServerEvent.START_INDEXING:
            return 'start_indexing'
        raise AssertionError(f'Unexpected ServerEvent: {type(self)}.')


class EventStartIndexerData(NamedTuple):
    substrate_chain: SubstrateChain
    url: str

    @classmethod
    def deserialize_from_data(cls, data: Dict[str, Any]) -> 'EventStartIndexerData':
        try:
            chain_id = data['chain_id']
            url = data['url']
        except KeyError as e:
            msg = f'Missing key in data: {str(e)}.'
            raise DeserializationError(msg) from e

        substrate_chain = SubstrateChain.get_substrate_chain_from_chain_id(chain_id)

        return cls(
            substrate_chain=substrate_chain,
            url=url,
        )

    def serialize(self) -> Dict[str, Any]:
        return {
            'chain_id': self.substrate_chain.value,
            'url': self.url,
        }


@dataclass(init=True, repr=True, frozen=True)
class EventStartIndexingData:
    substrate_chain: SubstrateChain
    block_number_start_at: BlockNumber
    address: SubstrateAddress
    address_public_key: SubstratePublicKey = field(init=False)  # starts with `0x`

    def __post_init__(self) -> None:
        try:
            address_public_key_ = get_substrate_public_key_from_substrate_address(
                chain=self.substrate_chain,
                address=self.address,
            )
        except ValueError as e:
            raise DeserializationError(
                f'Failed to obtain the public key of this {self.substrate_chain} '
                f'address: {self.address}',
            ) from e

        object.__setattr__(self, 'address_public_key', address_public_key_)

    @classmethod
    def deserialize_from_data(cls, data: Dict[str, Any]) -> 'EventStartIndexingData':
        try:
            chain_id = data['chain_id']
            block_number_start_at = data['block_number_start_at']
            address = data['address']
        except KeyError as e:
            msg = f'Missing key in data: {str(e)}.'
            raise DeserializationError(msg) from e

        if not isinstance(block_number_start_at, int) or block_number_start_at <= 0:
            raise DeserializationError('Invalid block number. Expected integer greater than zero')

        substrate_chain = SubstrateChain.get_substrate_chain_from_chain_id(chain_id)
        is_valid_address = is_valid_substrate_address(
            chain=substrate_chain,
            value=address,
        )
        if is_valid_address is False:
            raise DeserializationError(f'Invalid {substrate_chain} address: {address}')

        return cls(
            substrate_chain=substrate_chain,
            block_number_start_at=BlockNumber(block_number_start_at),
            address=SubstrateAddress(address),
        )

    def serialize(self) -> Dict[str, Any]:
        return {
            'chain_id': self.substrate_chain.value,
            'block_number_start_at': self.block_number_start_at,
            'address': self.address,
        }


class EventErrorData(NamedTuple):
    error: EventError
    detail: str = ''

    def serialize(self) -> Dict[str, Any]:
        return {
            'error': str(self.error),
            'message': self.error.message(),
            'detail': self.detail,
        }


class EventSuccessData(NamedTuple):
    event: ServerEvent

    def serialize(self) -> Dict[str, Any]:
        return {
            'event': str(self.event),
        }
