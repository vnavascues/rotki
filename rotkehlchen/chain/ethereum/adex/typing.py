from enum import Enum
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple, Union

from eth_typing.evm import ChecksumAddress
from eth_utils.typing import HexStr

from rotkehlchen.accounting.structures import Balance
from rotkehlchen.errors import DeserializationError
from rotkehlchen.fval import FVal
from rotkehlchen.serialization.deserialize import (
    deserialize_asset_amount,
    deserialize_ethereum_address,
    deserialize_timestamp,
)
from rotkehlchen.typing import Timestamp


class EventType(Enum):
    """Supported events"""
    BOND = 1
    UNBOND = 2
    UNBOND_REQUEST = 3

    def __str__(self) -> str:
        if self == EventType.BOND:
            return 'bond'
        if self == EventType.UNBOND:
            return 'unbond'
        if self == EventType.UNBOND_REQUEST:
            return 'unbond_request'
        raise RuntimeError(f'Corrupt value {self} for EventType -- Should never happen')


class Bond(NamedTuple):
    tx_hash: HexStr  # from bond.id
    address: ChecksumAddress
    identity_address: ChecksumAddress
    timestamp: Timestamp
    bond_id: HexStr
    amount: FVal
    pool_id: HexStr
    nonce: int


class Unbond(NamedTuple):
    tx_hash: HexStr  # from unbond.id
    address: ChecksumAddress
    identity_address: ChecksumAddress
    timestamp: Timestamp
    bond_id: HexStr


class UnbondRequest(NamedTuple):
    tx_hash: HexStr  # from unbond.id
    address: ChecksumAddress
    identity_address: ChecksumAddress
    timestamp: Timestamp
    bond_id: HexStr


# Contains the events' (e.g. bond, unbond) common attributes
class EventCoreData(NamedTuple):
    tx_hash: HexStr
    address: ChecksumAddress
    identity_address: ChecksumAddress
    timestamp: Timestamp


class ADXStakingBalance(NamedTuple):
    pool_id: HexStr
    pool_name: Optional[str]
    balance: Balance
    address: ChecksumAddress

    def serialize(self) -> Dict[str, Any]:
        return {
            'pool_id': self.pool_id,
            'pool_name': self.pool_name,
            'balance': self.balance.serialize(),
            'address': self.address,
        }

ADXStakingEventDBTuple = (
    Tuple[
        str,  # tx_hash
        str,  # address
        str,  # identity_address
        int,  # timestamp
        str,  # bond_id
        str,  # type
        str,  # pool_id
        str,  # amount
    ]
)


class ADXStakingEvent(NamedTuple):
    tx_hash: HexStr  # from unbond.id
    address: ChecksumAddress
    identity_address: ChecksumAddress
    timestamp: Timestamp
    bond_id: HexStr
    event_type: EventType
    pool_id: Optional[HexStr]
    amount: Optional[FVal]

    @classmethod
    def deserialize_from_db(
            cls,
            event_tuple: ADXStakingEventDBTuple,
    ) -> 'ADXStakingEvent':
        """Turns a tuple read from DB into an appropriate ADXStakingEvent.
        May raise a DeserializationError if something is wrong with the DB data.

        Event_tuple index - Schema columns
        ----------------------------------
        0 - tx_hash
        1 - address
        2 - identity_address
        3 - timestamp
        4 - bond_id
        5 - type
        6 - pool_id
        7 - amount
        """
        db_event_type = event_tuple[5]
        if db_event_type not in {str(event_type) for event_type in EventType}:
            raise DeserializationError(
                f'Failed to deserialize event type. Unknown event: {db_event_type}.',
            )

        if db_event_type == str(EventType.BOND):
            event_type = EventType.BOND
        elif db_event_type == str(EventType.UNBOND):
            event_type = EventType.UNBOND
        elif db_event_type == str(EventType.UNBOND_REQUEST):
            event_type = EventType.UNBOND_REQUEST
        else:
            raise AssertionError(f'Unexpected event type case: {db_event_type}.')

        return cls(
            tx_hash=event_tuple[0],
            address=deserialize_ethereum_address(event_tuple[1]),
            identity_address=deserialize_ethereum_address(event_tuple[2]),
            timestamp=deserialize_timestamp(event_tuple[3]),
            bond_id=HexStr(event_tuple[4]),
            event_type=event_type,
            pool_id=HexStr(event_tuple[6]),
            amount=deserialize_asset_amount(event_tuple[7]),
        )

    @staticmethod
    def serialize_event_to_db_tuple(
            event: Union[Bond, Unbond, UnbondRequest],
    ) -> ADXStakingEventDBTuple:
        """Given a <Bond>, <Unbond> or <UnbondRequest> serialize it to the
        standard db event tuple.
        """
        if isinstance(event, Bond):
            event_type = EventType.BOND
            amount = event.amount
        elif isinstance(event, Unbond):
            event_type = EventType.UNBOND
            amount = ''
        elif isinstance(event, UnbondRequest):
            event_type = EventType.UNBOND_REQUEST
            amount = ''
        else:
            raise AssertionError(f'Unexpected event type: {type(event)}.')

        return (
            str(event.tx_hash),
            str(event.address),
            str(event.identity_address),
            int(event.timestamp),
            str(event.bond_id),
            str(event_type),
            amount,
        )


DeserializationMethod = Callable[..., Union[Bond, Unbond, UnbondRequest]]
