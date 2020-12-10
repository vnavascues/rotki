import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any, DefaultDict, Dict, List, Optional, Union, overload

from eth_typing.evm import ChecksumAddress
from eth_utils import to_checksum_address
from eth_utils.typing import HexAddress, HexStr
from gevent.lock import Semaphore
from typing_extensions import Literal
from web3 import Web3

from rotkehlchen.accounting.structures import Balance
from rotkehlchen.assets.asset import EthereumToken
from rotkehlchen.chain.ethereum.graph import GRAPH_QUERY_LIMIT, Graph, format_query_indentation
from rotkehlchen.chain.ethereum.utils import generate_address_via_create2
from rotkehlchen.errors import RemoteError
from rotkehlchen.fval import FVal
from rotkehlchen.inquirer import Inquirer
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.premium.premium import Premium
from rotkehlchen.serialization.deserialize import (
    deserialize_ethereum_address,
    deserialize_timestamp,
)
from rotkehlchen.typing import ChecksumEthAddress, Price, Timestamp
from rotkehlchen.user_messages import MessagesAggregator
from rotkehlchen.utils.interfaces import EthereumModule

from .graph import BONDS_QUERY, UNBOND_REQUESTS_QUERY, UNBONDS_QUERY
from .typing import (
    ADXStakingBalance,
    Bond,
    DeserializationMethod,
    EventCoreData,
    Unbond,
    UnbondRequest,
)
from .utils import (
    ADEX_EVENTS_PREFIX,
    ADX_AMOUNT_MANTISSA,
    CREATE2_SALT,
    IDENTITY_FACTORY_ADDR,
    IDENTITY_PROXY_INIT_CODE,
    POOL_ID_POOL_NAME,
    STAKING_ADDR,
)

if TYPE_CHECKING:
    from rotkehlchen.chain.ethereum.manager import EthereumManager
    from rotkehlchen.db.dbhandler import DBHandler

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


class Adex(EthereumModule):
    """AdEx integration module

    AdEx subgraph:
    https://github.com/samparsky/adex_subgraph
    """
    def __init__(
            self,
            ethereum_manager: 'EthereumManager',
            database: 'DBHandler',
            premium: Optional[Premium],
            msg_aggregator: MessagesAggregator,
    ) -> None:
        self.ethereum = ethereum_manager
        self.database = database
        self.premium = premium
        self.msg_aggregator = msg_aggregator
        self.trades_lock = Semaphore()
        try:
            self.graph: Optional[Graph] = Graph(
                'https://api.thegraph.com/subgraphs/name/adexnetwork/adex-protocol',
            )
        except RemoteError as e:
            self.graph = None
            self.msg_aggregator.add_error(
                f'Could not initialize the AdEx subgraph due to {str(e)}. '
                f'All AdEx balances and historical queries are not functioning until this is fixed. '  # noqa: E501
                f'Probably will get fixed with time. If not report it to Rotki\'s support channel.',  # noqa: E501
            )

    @staticmethod
    def _calculate_adex_balances(
            bonds: List[Bond],
            unbonds: List[Unbond],
            unbond_requests: List[UnbondRequest],
            adx_usd_price: Price,
    ) -> Dict[ChecksumAddress, List[ADXStakingBalance]]:
        """Given a list of bonds, unbonds and unbond requests returns per
        address the staked amounts per pool.

        Given an address, its staked amount per pool is computed by deducting
        the unbonds and the unbond requests from its bonds.
        """
        address_bonds = defaultdict(list)
        address_unbonds_set = {(unbond.address, unbond.bond_id) for unbond in unbonds}
        address_unbond_requests_set = {
            (unbond_request.address, unbond_request.bond_id) for unbond_request in unbond_requests
        }
        # Get bonds whose `bond_id` is not in unbonds or unbond_requests
        for bond in bonds:
            if (
                (bond.address, bond.bond_id) in
                address_unbonds_set.union(address_unbond_requests_set)
            ):
                continue
            address_bonds[bond.address].append(bond)

        # Get per address staked balances in pools
        adex_balances: DefaultDict[ChecksumAddress, List[ADXStakingBalance]] = defaultdict(list)
        for address, bonds in address_bonds.items():
            pool_ids = {bond.pool_id for bond in bonds}
            for pool_id in pool_ids:
                amount = FVal(sum(bond.amount for bond in bonds if bond.pool_id == pool_id))
                pool_name = POOL_ID_POOL_NAME.get(pool_id, None)
                if pool_name is None:
                    log.error(
                        f'Error getting name for AdEx pool: {pool_id}. '
                        'Please, update the map of pools and names.',
                    )
                pool_balance = ADXStakingBalance(
                    pool_id=pool_id,
                    pool_name=pool_name,
                    balance=Balance(
                        amount=amount,
                        usd_value=amount * adx_usd_price,
                    ),
                    address=to_checksum_address(STAKING_ADDR),
                )
                adex_balances[address].append(pool_balance)

        return dict(adex_balances)

    def _deserialize_bond(
            self,
            raw_event: Dict[str, Any],
            identity_address_map: Dict[ChecksumAddress, ChecksumAddress],
    ) -> Bond:
        """Deserialize a bond event.

        It may raise KeyError.
        """
        event_core_data = self._deserialize_event_core_data(
            raw_event=raw_event,
            identity_address_map=identity_address_map,
        )
        amount_int = int(raw_event['amount'])
        amount = FVal(raw_event['amount']) / ADX_AMOUNT_MANTISSA
        pool_id = HexStr(raw_event['poolId'])
        nonce = int(raw_event['nonce'])
        bond_id = self._get_bond_id(
            identity_address=event_core_data.identity_address,
            amount=amount_int,
            pool_id=pool_id,
            nonce=nonce,
        )
        return Bond(
            tx_hash=event_core_data.tx_hash,
            address=event_core_data.address,
            identity_address=event_core_data.identity_address,
            timestamp=event_core_data.timestamp,
            bond_id=bond_id,
            amount=amount,
            pool_id=pool_id,
            nonce=nonce,
        )

    @staticmethod
    def _deserialize_event_core_data(
            raw_event: Dict[str, Any],
            identity_address_map: Dict[ChecksumAddress, ChecksumAddress],
    ) -> EventCoreData:
        """Deserialize the common event attributes.

        It may raise KeyError.
        Id for unbond and unbond request events is 'tx_hash:address'.
        """
        identity_address = to_checksum_address(raw_event['owner'])
        return EventCoreData(
            tx_hash=HexStr(raw_event['id'].split(':')[0]),
            address=identity_address_map[identity_address],
            identity_address=identity_address,
            timestamp=Timestamp(raw_event['timestamp']),
        )

    def _deserialize_unbond(
            self,
            raw_event: Dict[str, Any],
            identity_address_map: Dict[ChecksumAddress, ChecksumAddress],
    ) -> Unbond:
        """Deserialize an unbond event.

        It may raise KeyError.
        """
        event_core_data = self._deserialize_event_core_data(
            raw_event=raw_event,
            identity_address_map=identity_address_map,
        )
        return Unbond(
            tx_hash=event_core_data.tx_hash,
            address=event_core_data.address,
            identity_address=event_core_data.identity_address,
            timestamp=event_core_data.timestamp,
            bond_id=HexStr(raw_event['bondId']),
        )

    def _deserialize_unbond_request(
            self,
            raw_event: Dict[str, Any],
            identity_address_map: Dict[ChecksumAddress, ChecksumAddress],
    ) -> UnbondRequest:
        """Deserialize an unbond request event.

        It may raise KeyError.
        """
        event_core_data = self._deserialize_event_core_data(
            raw_event=raw_event,
            identity_address_map=identity_address_map,
        )
        return UnbondRequest(
            tx_hash=event_core_data.tx_hash,
            address=event_core_data.address,
            identity_address=event_core_data.identity_address,
            timestamp=event_core_data.timestamp,
            bond_id=HexStr(raw_event['bondId']),
        )

    def _get_events(
            self,
            addresses: List[ChecksumEthAddress],
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ):
        """TODO
        """
        new_addresses: List[ChecksumEthAddress] = []
        existing_addresses: List[ChecksumEthAddress] = []
        min_end_ts: Timestamp = to_timestamp

        # Get addresses' last used query range for AdEx events
        for address in addresses:
            entry_name = f'{ADEX_EVENTS_PREFIX}_{address}'
            events_range = self.database.get_used_query_range(name=entry_name)

            if not events_range:
                new_addresses.append(address)
            else:
                existing_addresses.append(address)
                min_end_ts = min(min_end_ts, events_range[1])

        # Request new addresses' events
        all_new_events = []
        if new_addresses:
            new_events = self._get_new_events(
                addresses=addresses,
                start_ts=Timestamp(0),
                end_ts=to_timestamp,
            )
            all_new_events.extend(new_events)

        # Request existing DB addresses' events
        if existing_addresses and min_end_ts <= to_timestamp:
            new_events = self._get_new_events(
                addresses=addresses,
                start_ts=min_end_ts,
                end_ts=to_timestamp,
            )
        # ! TODO

        return {}

    @overload  # noqa: F811
    def _get_balances_graph(  # pylint: disable=no-self-use
            self,
            addresses: List[ChecksumEthAddress],
            case: Literal['bonds'],
            start_ts: Optional[Timestamp],
            end_ts: Optional[Timestamp],
    ) -> List[Bond]:
        ...

    @overload  # noqa: F811
    def _get_balances_graph(  # pylint: disable=no-self-use
            self,
            addresses: List[ChecksumEthAddress],
            case: Literal['unbonds'],
            start_ts: Optional[Timestamp],
            end_ts: Optional[Timestamp],
    ) -> List[Unbond]:
        ...

    @overload  # noqa: F811
    def _get_balances_graph(  # pylint: disable=no-self-use
            self,
            addresses: List[ChecksumEthAddress],
            case: Literal['unbond_requests'],
            start_ts: Optional[Timestamp],
            end_ts: Optional[Timestamp],
    ) -> List[UnbondRequest]:
        ...

    def _get_balances_graph(
            self,
            addresses: List[ChecksumEthAddress],
            case: Literal['bonds', 'unbonds', 'unbond_requests'],
            start_ts: Optional[Timestamp],
            end_ts: Optional[Timestamp],
    ) -> Union[List[Bond], List[Unbond], List[UnbondRequest]]:
        """Get the addresses' events data querying the AdEx subgraph.
        """
        identity_address_map = (
            self._get_identity_address_map(addresses)
        )
        deserialization_method: DeserializationMethod
        querystr: str
        schema: Literal['bonds', 'unbonds', 'unbondRequests']
        if case == 'bonds':
            deserialization_method = self._deserialize_bond
            querystr = format_query_indentation(BONDS_QUERY.format())
            schema = 'bonds'
            case_pretty = 'bond'
        elif case == 'unbonds':
            deserialization_method = self._deserialize_unbond
            querystr = format_query_indentation(UNBONDS_QUERY.format())
            schema = 'unbonds'
            case_pretty = 'unbond'
        elif case == 'unbond_requests':
            deserialization_method = self._deserialize_unbond_request
            querystr = format_query_indentation(UNBOND_REQUESTS_QUERY.format())
            schema = 'unbondRequests'
            case_pretty = 'unbond request'
        else:
            raise AssertionError(f'Unexpected AdEx case: {case}.')

        from_ts = start_ts or 0
        to_ts =
        user_identities = [str(identity).lower() for identity in identity_address_map.keys()]
        param_types = {
            '$limit': 'Int!',
            '$offset': 'Int!',
            '$user_identities': '[Bytes!]',
        }
        param_values = {
            'limit': GRAPH_QUERY_LIMIT,
            'offset': 0,
            'user_identities': user_identities,
        }
        events: Union[List[Bond], List[Unbond], List[UnbondRequest]] = []  # type: ignore
        while True:
            result = self.graph.query(  # type: ignore # caller already checks
                querystr=querystr,
                param_types=param_types,
                param_values=param_values,
            )
            result_data = result[schema]

            for raw_event in result_data:
                try:
                    event = deserialization_method(
                        raw_event=raw_event,
                        identity_address_map=identity_address_map,
                    )
                except KeyError as e:
                    msg = str(e)
                    log.error(
                        f'Error processing an AdEx {case_pretty}.',
                        raw_event=raw_event,
                        error=msg,
                    )
                    self.msg_aggregator.add_error(
                        f'Failed to deserialize an AdEx {case_pretty}. '
                        f'Check logs for details. Ignoring it.',
                    )
                    continue

                events.append(event)  # type: ignore

            if len(result_data) < GRAPH_QUERY_LIMIT:
                break

            param_values = {
                **param_values,
                'offset': param_values['offset'] + GRAPH_QUERY_LIMIT,  # type: ignore # is int
            }

        return events

    @staticmethod
    def _get_bond_id(
            identity_address: ChecksumAddress,
            amount: int,
            pool_id: HexStr,
            nonce: int,
    ) -> HexStr:
        """Given a LogBond event data, return its `bondId`.
        """
        arg_types = ['address', 'address', 'uint', 'bytes32', 'uint']
        args = [STAKING_ADDR, identity_address, amount, pool_id, nonce]
        return HexStr(Web3.keccak(Web3().codec.encode_abi(arg_types, args)).hex())

    def _get_identity_address_map(
            self,
            addresses: List[ChecksumEthAddress],
    ) -> Dict[ChecksumAddress, ChecksumAddress]:
        """Returns a map between the user identity address in the protocol and
        the EOA/contract address.
        """
        return {self._get_user_identity(address): address for address in addresses}

    def _get_new_events(
            self,
            addresses: List[ChecksumEthAddress],
            start_ts: Timestamp,
            end_ts: Timestamp,
    ) -> Union[List[Bond], List[Unbond], List[UnbondRequest]]:
        """Returns events of the addresses within the time range and inserts/updates
        the used query range of the addresses as well.
        """
        all_events = []
        for event_type in ('bonds', 'unbonds', 'unbond_requests'):
            events = self._get_balances_graph(
                addresses=addresses,
                case=event_type,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            all_events.extend(events)

        for address in addresses:
            self.database.update_used_query_range(
                name=f'{ADEX_EVENTS_PREFIX}_{address}',
                start_ts=start_ts,
                end_ts=end_ts,
            )
        return all_events


    @staticmethod
    def _get_user_identity(address: ChecksumAddress) -> ChecksumEthAddress:
        """Given an address (signer) returns its protocol user identity.
        """
        return generate_address_via_create2(
            address=HexAddress(HexStr(IDENTITY_FACTORY_ADDR)),
            salt=HexStr(CREATE2_SALT),
            init_code=HexStr(IDENTITY_PROXY_INIT_CODE.format(signer_address=address)),
        )

    def get_balances(
            self,
            addresses: List[ChecksumAddress],
    ) -> Dict[ChecksumAddress, List[ADXStakingBalance]]:
        """Return the addresses' balances (staked amount per pool) in the AdEx
        protocol.

        TODO: route non-premium users through on-chain query.
        """
        is_graph_mode = self.graph and self.premium

        adex_balances: Dict[ChecksumAddress, List[ADXStakingBalance]] = {}
        if is_graph_mode:
            bonds = self._get_balances_graph(addresses=addresses, case='bonds')

            # NB: there shouldn't be unbonds and unbond_requests without bonds
            if bonds:
                unbonds = self._get_balances_graph(addresses=addresses, case='unbonds')
                unbond_requests = self._get_balances_graph(
                    addresses=addresses,
                    case='unbond_requests',
                )
                adx_usd_price = Inquirer().find_usd_price(EthereumToken('ADX'))
                adex_balances = self._calculate_adex_balances(
                    bonds=bonds,
                    unbonds=unbonds,
                    unbond_requests=unbond_requests,
                    adx_usd_price=adx_usd_price,
                )
        else:
            raise NotImplementedError(
                "Get AdEx balances for non premium user is not implemented.",
            )
        return adex_balances

    def get_events_history(
            self,
            addresses: List[ChecksumEthAddress],
            reset_db_data: bool,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ):
        """TODO
        """
        if self.graph is None:  # could not initialize graph
            return {}

        with self.trades_lock:
            if reset_db_data is True:
                self.database.delete_adex_events_data()

        adex_events = {}
        adex_events = self._get_events(
            addresses=addresses,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )
        return adex_events

    # -- Methods following the EthereumModule interface -- #
    def on_startup(self) -> None:
        pass

    def on_account_addition(self, address: ChecksumEthAddress) -> None:
        pass

    def on_account_removal(self, address: ChecksumEthAddress) -> None:
        pass
