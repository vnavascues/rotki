import logging
from collections import defaultdict
from datetime import datetime, time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from eth_utils import to_checksum_address
from gevent.lock import Semaphore

from rotkehlchen.accounting.structures import Balance
from rotkehlchen.assets.asset import EthereumToken
from rotkehlchen.assets.unknown_asset import UnknownEthereumToken
from rotkehlchen.assets.utils import get_ethereum_token
from rotkehlchen.chain.ethereum.graph import GRAPH_QUERY_LIMIT, Graph, format_query_indentation
from rotkehlchen.chain.ethereum.trades import AMMSwap, AMMTrade
from rotkehlchen.constants import ZERO
from rotkehlchen.errors import RemoteError
from rotkehlchen.fval import FVal
from rotkehlchen.inquirer import Inquirer
from rotkehlchen.premium.premium import Premium
from rotkehlchen.typing import (
    AssetAmount,
    ChecksumEthAddress,
    Location,
    Price,
    Timestamp,
    TradeType,
)
from rotkehlchen.user_messages import MessagesAggregator
from rotkehlchen.utils.interfaces import EthereumModule

from .graph import (
    BURNS_QUERY,
    LIQUIDITY_POSITIONS_QUERY,
    MINTS_QUERY,
    SWAPS_QUERY,
    TOKEN_DAY_DATAS_QUERY,
)
from .typing import (
    UNISWAP_EVENTS_PREFIX,
    UNISWAP_TRADES_PREFIX,
    AddressBalances,
    AddressEvents,
    AddressEventsBalances,
    AddressTrades,
    AssetPrice,
    DDAddressBalances,
    DDAddressEvents,
    EventType,
    LiquidityPool,
    LiquidityPoolAsset,
    LiquidityPoolEvent,
    LiquidityPoolEventsBalance,
    ProtocolBalance,
)

if TYPE_CHECKING:
    from rotkehlchen.chain.ethereum.manager import EthereumManager
    from rotkehlchen.db.dbhandler import DBHandler

log = logging.getLogger(__name__)


def add_trades_from_swaps(
        swaps: List[AMMSwap],
        trades: List[AMMTrade],
        both_in: bool,
        quote_assets: Sequence[Tuple[Any, ...]],
        token_amount: AssetAmount,
        token: Union[EthereumToken, UnknownEthereumToken],
        trade_index: int,
) -> List[AMMTrade]:
    bought_amount = AssetAmount(token_amount / 2) if both_in else token_amount
    for entry in quote_assets:
        quote_asset = entry[0]
        sold_amount = entry[1]
        rate = bought_amount / sold_amount
        trade = AMMTrade(
            trade_type=TradeType.BUY,
            base_asset=token,
            quote_asset=quote_asset,
            amount=bought_amount,
            rate=rate,
            swaps=swaps,
            trade_index=trade_index,
        )
        trades.append(trade)
        trade_index += 1

    return trades


class Uniswap(EthereumModule):
    """Uniswap integration module

    * Uniswap subgraph:
    https://github.com/Uniswap/uniswap-v2-subgraph
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
                'https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v2',
            )
        except RemoteError as e:
            self.graph = None
            self.msg_aggregator.add_error(
                f'Could not initialize the Uniswap subgraph due to {str(e)}. '
                f'All uniswap historical queries are not functioning until this is fixed. '
                f'Probably will get fixed with time. If not report it to Rotkis support channel ',
            )

    @staticmethod
    def _get_balances_graph(
        addresses: List[ChecksumEthAddress],
        graph_query: Callable,
    ) -> ProtocolBalance:
        """Get the addresses' pools data querying the Uniswap subgraph

        Each liquidity position is converted into a <LiquidityPool>.
        """
        address_balances: DDAddressBalances = defaultdict(list)
        known_assets: Set[EthereumToken] = set()
        unknown_assets: Set[UnknownEthereumToken] = set()

        addresses_lower = [address.lower() for address in addresses]
        querystr = format_query_indentation(LIQUIDITY_POSITIONS_QUERY.format())
        param_types = {
            '$limit': 'Int!',
            '$offset': 'Int!',
            '$addresses': '[String!]',
            '$balance': 'BigDecimal!',
        }
        param_values = {
            'limit': GRAPH_QUERY_LIMIT,
            'offset': 0,
            'addresses': addresses_lower,
            'balance': '0',
        }
        while True:
            result = graph_query(
                querystr=querystr,
                param_types=param_types,
                param_values=param_values,
            )
            result_data = result['liquidityPositions']

            for lp in result_data:
                user_address = to_checksum_address(lp['user']['id'])
                user_lp_balance = FVal(lp['liquidityTokenBalance'])
                lp_pair = lp['pair']
                lp_address = to_checksum_address(lp_pair['id'])
                lp_total_supply = FVal(lp_pair['totalSupply'])

                # Insert LP tokens reserves within tokens dicts
                token0 = lp_pair['token0']
                token0['total_amount'] = lp_pair['reserve0']
                token1 = lp_pair['token1']
                token1['total_amount'] = lp_pair['reserve1']

                liquidity_pool_assets = []

                for token in token0, token1:
                    # Get the token <EthereumToken> or <UnknownEthereumToken>
                    asset = get_ethereum_token(
                        symbol=token['symbol'],
                        ethereum_address=to_checksum_address(token['id']),
                        name=token['name'],
                        decimals=int(token['decimals']),
                    )

                    # Classify the asset either as known or unknown
                    if isinstance(asset, EthereumToken):
                        known_assets.add(asset)
                    elif isinstance(asset, UnknownEthereumToken):
                        unknown_assets.add(asset)

                    # Estimate the underlying asset total_amount
                    asset_total_amount = FVal(token['total_amount'])
                    user_asset_balance = (
                        user_lp_balance / lp_total_supply * asset_total_amount
                    )

                    liquidity_pool_asset = LiquidityPoolAsset(
                        asset=asset,
                        total_amount=asset_total_amount,
                        user_balance=Balance(amount=user_asset_balance),
                    )
                    liquidity_pool_assets.append(liquidity_pool_asset)

                liquidity_pool = LiquidityPool(
                    address=lp_address,
                    assets=liquidity_pool_assets,
                    total_supply=lp_total_supply,
                    user_balance=Balance(amount=user_lp_balance),
                )
                address_balances[user_address].append(liquidity_pool)

            # Check whether an extra request is needed
            if len(result_data) < GRAPH_QUERY_LIMIT:
                break

            # Update pagination step
            param_values = {
                **param_values,
                'offset': param_values['offset'] + GRAPH_QUERY_LIMIT,  # type: ignore
            }

        protocol_balance = ProtocolBalance(
            address_balances=dict(address_balances),
            known_assets=known_assets,
            unknown_assets=unknown_assets,
        )
        return protocol_balance

    @staticmethod
    def _get_balances_chain(addresses: List[ChecksumEthAddress]) -> ProtocolBalance:
        """Get the addresses' pools data via Zerion SDK.
        """
        address_balances: AddressBalances = {address: [] for address in addresses}
        known_assets: Set[EthereumToken] = set()
        unknown_assets: Set[UnknownEthereumToken] = set()

        protocol_balance = ProtocolBalance(
            address_balances=address_balances,
            known_assets=known_assets,
            unknown_assets=unknown_assets,
        )
        return protocol_balance

    @staticmethod
    def _get_known_asset_price(
            known_assets: Set[EthereumToken],
            unknown_assets: Set[UnknownEthereumToken],
            price_query: Callable,
    ) -> AssetPrice:
        """Get the tokens prices via Inquirer

        Given an asset, if `find_usd_price()` returns zero, it will be added
        into `unknown_assets`.
        """
        asset_price: AssetPrice = {}

        for known_asset in known_assets:
            asset_usd_price = price_query(known_asset)

            if asset_usd_price != Price(ZERO):
                asset_price[known_asset.ethereum_address] = asset_usd_price
            else:
                unknown_asset = UnknownEthereumToken(
                    ethereum_address=known_asset.ethereum_address,
                    symbol=known_asset.identifier,
                    name=known_asset.name,
                    decimals=known_asset.decimals,
                )
                unknown_assets.add(unknown_asset)

        return asset_price

    @staticmethod
    def _tx_swaps_to_trades(swaps: List[AMMSwap]) -> List[AMMTrade]:
        """
        Turns a list of a transaction's swaps into a list of trades, taking into account
        the first and last swaps only for use with the rest of the rotki accounting.

        TODO: This is not nice, but we are constrained by the 1 token in
        1 token out concept of a trade we have right now. So if in a swap
        we have both tokens in we will create two trades, with the final
        amount being divided between the 2 trades. This is only so that
        the AMM trade can be processed easily in our current trades
        accounting.
        Make issue to process this properly as multitrades when we change
        the trade format
        """
        trades: List[AMMTrade] = []
        both_in = False
        both_out = False
        if swaps[0].amount0_in > ZERO and swaps[0].amount1_in > ZERO:
            both_in = True
        if swaps[-1].amount0_out > ZERO and swaps[-1].amount1_out > ZERO:
            both_out = True

        if both_in:
            quote_assets = [
                (swaps[0].token0, swaps[0].amount0_in if not both_out else swaps[0].amount0_in / 2),  # noqa: E501
                (swaps[0].token1, swaps[0].amount1_in if not both_out else swaps[0].amount1_in / 2),  # noqa: E501
            ]
        elif swaps[0].amount0_in > ZERO:
            quote_assets = [(swaps[0].token0, swaps[0].amount0_in)]
        else:
            quote_assets = [(swaps[0].token1, swaps[0].amount1_in)]

        trade_index = 0
        if swaps[-1].amount0_out > ZERO:
            trades = add_trades_from_swaps(
                swaps=swaps,
                trades=trades,
                both_in=both_in,
                quote_assets=quote_assets,
                token_amount=swaps[-1].amount0_out,
                token=swaps[-1].token0,
                trade_index=trade_index,
            )
            trade_index += len(trades)
        if swaps[-1].amount1_out > ZERO:
            trades = add_trades_from_swaps(
                swaps=swaps,
                trades=trades,
                both_in=both_in,
                quote_assets=quote_assets,
                token_amount=swaps[-1].amount1_out,
                token=swaps[-1].token1,
                trade_index=trade_index,
            )

        return trades

    @staticmethod
    def _calculate_events_balances(
            address: ChecksumEthAddress,
            events: List[LiquidityPoolEvent],
            balances: List[LiquidityPool],
    ) -> List[LiquidityPoolEventsBalance]:
        """Given an address and its LP events and LPs, process each LP event
        (grouped by pool) aggregating the token0, token1, usd and LP token
        amounts. Factorise in the aggregation the current protocol balances
        (if `balances` != [], all events case). Finally return the profit/loss
        totals and the LP events (grouped by pool) in
        <LiquidityPoolEventsBalance>.
        """
        events_balances: List[LiquidityPoolEventsBalance] = []
        pool_balance: Dict[ChecksumEthAddress, LiquidityPool] = (
            {pool.address: pool for pool in balances}
        )
        # quick lookup, `agg` from aggregated
        pool_events_agg_balance: Dict[ChecksumEthAddress, Dict[str, Any]] = {}
        # Populate `pool_events_agg_balance` dict, being the keys the pools'
        # addresses and the values their aggregated balances from their events
        for event in events:
            pool = event.pool_address

            if pool not in pool_events_agg_balance:
                # Default dictionary for amounts aggregation
                pool_events_agg_balance[pool] = {
                    'events': [],
                    'profit_loss0': ZERO,
                    'profit_loss1': ZERO,
                    'usd_profit_loss': ZERO,
                    'lp_profit_loss': ZERO,
                }

            pool_events_agg_balance[pool]['events'].append(event)

            if event.event_type == EventType.MINT:
                pool_events_agg_balance[pool]['profit_loss0'] += FVal(event.amount0)
                pool_events_agg_balance[pool]['profit_loss1'] += FVal(event.amount1)
                pool_events_agg_balance[pool]['usd_profit_loss'] += FVal(event.usd_price)
                pool_events_agg_balance[pool]['lp_profit_loss'] += FVal(event.lp_amount)
            else:  # event_type == EventType.BURN
                pool_events_agg_balance[pool]['profit_loss0'] -= FVal(event.amount0)
                pool_events_agg_balance[pool]['profit_loss1'] -= FVal(event.amount1)
                pool_events_agg_balance[pool]['usd_profit_loss'] -= FVal(event.usd_price)
                pool_events_agg_balance[pool]['lp_profit_loss'] -= FVal(event.lp_amount)

        # Instantiate `LiquidityPoolEventsBalance` per pool using
        # `pool_events_agg_balance`. If `pool_balance` exist (all events case),
        # factorise in the current pool balances in the totals.
        for pool, events_agg_balance in pool_events_agg_balance.items():
            profit_loss0 = events_agg_balance['profit_loss0']
            profit_loss1 = events_agg_balance['profit_loss1']
            usd_profit_loss = events_agg_balance['usd_profit_loss']
            lp_profit_loss = events_agg_balance['lp_profit_loss']

            # Aggregate current pool balances looking up the pool
            if pool in pool_balance:
                token0 = pool_balance[pool].assets[0].asset
                token1 = pool_balance[pool].assets[1].asset
                profit_loss0 -= FVal(pool_balance[pool].assets[0].user_balance.amount)
                profit_loss1 -= FVal(pool_balance[pool].assets[1].user_balance.amount)
                usd_profit_loss -= FVal(pool_balance[pool].user_balance.usd_value)
                lp_profit_loss -= FVal(pool_balance[pool].user_balance.amount)
            else:
                # NB: get `token0` and `token1` from any pool event
                token0 = events_agg_balance['events'][0].token0
                token1 = events_agg_balance['events'][0].token1

            events_balance = LiquidityPoolEventsBalance(
                address=address,
                pool_address=pool,
                token0=token0,
                token1=token1,
                events=events_agg_balance['events'],
                profit_loss0=profit_loss0,
                profit_loss1=profit_loss1,
                usd_profit_loss=usd_profit_loss,
                lp_profit_loss=lp_profit_loss,
            )
            events_balances.append(events_balance)

        return events_balances

    def _get_events_balances(
            self,
            addresses: List[ChecksumEthAddress],
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> AddressEventsBalances:
        """Request via graph all events for new addresses and the latest ones
        for already existing addresses. Then the requested events are written
        in DB and finally all DB events are read, and processed for calculating
        total profit/loss per LP (stored within <LiquidityPoolEventsBalance>).
        """
        print("\n\n")
        print("*** GET TRADES ***")
        print("*** from_ts:", from_timestamp)
        print("*** to_ts:", to_timestamp)
        print("\n\n")
        address_events_balances: AddressEventsBalances = {}
        address_events: DDAddressEvents = defaultdict(list)
        db_address_events: AddressEvents = {}
        new_addresses: List[ChecksumEthAddress] = []
        existing_addresses: List[ChecksumEthAddress] = []
        min_end_ts: Timestamp = to_timestamp

        # Get addresses' last used query range for Uniswap events
        for address in addresses:
            entry_name = f'{UNISWAP_EVENTS_PREFIX}_{address}'
            events_range = self.database.get_used_query_range(name=entry_name)

            if not events_range:
                new_addresses.append(address)
            else:
                existing_addresses.append(address)
                min_end_ts = min(min_end_ts, events_range[1])

        # Request new addresses' events
        if new_addresses:
            start_ts = Timestamp(0)
            for address in new_addresses:
                for event_type in EventType:
                    new_address_events = self._get_events_graph(
                        address=address,
                        start_ts=start_ts,
                        end_ts=to_timestamp,
                        event_type=event_type,
                    )
                    if new_address_events:
                        address_events[address].extend(new_address_events)

                # Insert new address' last used query range
                self.database.update_used_query_range(
                    name=f'{UNISWAP_EVENTS_PREFIX}_{address}',
                    start_ts=start_ts,
                    end_ts=to_timestamp,
                )

        # Request existing DB addresses' events
        if existing_addresses and min_end_ts <= to_timestamp:
            for address in new_addresses:
                for event_type in EventType:
                    address_new_events = self._get_events_graph(
                        address=address,
                        start_ts=min_end_ts,
                        end_ts=to_timestamp,
                        event_type=event_type,
                    )
                    if address_new_events:
                        address_events[address].extend(address_new_events)

                # Update existing address' last used query range
                self.database.update_used_query_range(
                    name=f'{UNISWAP_EVENTS_PREFIX}_{address}',
                    start_ts=min_end_ts,
                    end_ts=to_timestamp,
                )

        # Insert requested events in DB
        for address in filter(lambda address: address in address_events, addresses):
            self.database.add_uniswap_events(address_events[address])

        # Fetch all DB events within the time range
        for address in addresses:
            db_events = self.database.get_uniswap_events(
                from_ts=from_timestamp,
                to_ts=to_timestamp,
                address=address,
            )
            if db_events:
                # return events with the oldest first
                db_events.sort(key=lambda trade: trade.timestamp)
                db_address_events[address] = db_events

        # Request addresses' current balances (UNI-V2s and underlying tokens)
        # if there is no specific time range in this endpoint call (i.e. all
        # events). Current balances in the protocol are needed for an accurate
        # profit/loss calculation.
        # TODO: when this endpoint is called with a specific time range,
        # getting the balances and underlying tokens within that time range
        # requires an archive node. Feature pending to be developed.
        address_balances: AddressBalances = {}  # Empty when specific time range
        if from_timestamp == Timestamp(0):
            address_balances = self.get_balances(addresses)

        # Calculate addresses' event balances (i.e. profit/loss per pool)
        for address, events in db_address_events.items():
            balances = address_balances.get(address, [])  # Empty when specific time range
            events_balances = self._calculate_events_balances(
                address=address,
                events=events,
                balances=balances,
            )
            address_events_balances[address] = events_balances

        return address_events_balances

    def _get_trades(
            self,
            addresses: List[ChecksumEthAddress],
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> AddressTrades:
        """Request via graph all trades for new addresses and the latest ones
        for already existing addresses. Then the requested trade are written in
        DB and finally all DB trades are read and returned.
        """
        address_amm_trades: AddressTrades = {}
        db_address_trades: AddressTrades = {}
        new_addresses: List[ChecksumEthAddress] = []
        existing_addresses: List[ChecksumEthAddress] = []
        min_end_ts: Timestamp = to_timestamp

        # Get addresses' last used query range for Uniswap trades
        for address in addresses:
            entry_name = f'{UNISWAP_TRADES_PREFIX}_{address}'
            trades_range = self.database.get_used_query_range(name=entry_name)

            if not trades_range:
                new_addresses.append(address)
            else:
                existing_addresses.append(address)
                min_end_ts = min(min_end_ts, trades_range[1])

        # Request new addresses' trades
        if new_addresses:
            start_ts = Timestamp(0)
            new_address_trades = self._get_trades_graph(
                addresses=new_addresses,
                start_ts=start_ts,
                end_ts=to_timestamp,
            )
            address_amm_trades.update(new_address_trades)

            # Insert last used query range for new addresses
            for address in new_addresses:
                entry_name = f'{UNISWAP_TRADES_PREFIX}_{address}'
                self.database.update_used_query_range(
                    name=entry_name,
                    start_ts=start_ts,
                    end_ts=to_timestamp,
                )

        # Request existing DB addresses' trades
        if existing_addresses and min_end_ts <= to_timestamp:
            address_new_trades = self._get_trades_graph(
                addresses=existing_addresses,
                start_ts=min_end_ts,
                end_ts=to_timestamp,
            )
            address_amm_trades.update(address_new_trades)

            # Update last used query range for existing addresses
            for address in existing_addresses:
                entry_name = f'{UNISWAP_TRADES_PREFIX}_{address}'
                self.database.update_used_query_range(
                    name=entry_name,
                    start_ts=min_end_ts,
                    end_ts=to_timestamp,
                )

        # Insert all unique swaps to the D
        all_swaps = set()
        for address in filter(lambda address: address in address_amm_trades, addresses):
            for trade in address_amm_trades[address]:
                for swap in trade.swaps:
                    all_swaps.add(swap)

        self.database.add_amm_swaps(list(all_swaps))

        # Fetch all DB Uniswap trades within the time range
        for address in addresses:
            db_swaps = self.database.get_amm_swaps(
                from_ts=from_timestamp,
                to_ts=to_timestamp,
                location=Location.UNISWAP,
                address=address,
            )
            db_trades = self.swaps_to_trades(db_swaps)
            if db_trades:
                db_address_trades[address] = db_trades

        return db_address_trades

    @staticmethod
    def swaps_to_trades(swaps: List[AMMSwap]) -> List[AMMTrade]:
        trades = []
        # sort by timestamp and then by log index
        swaps.sort(key=lambda trade: (trade.timestamp, -trade.log_index), reverse=True)
        last_tx_hash = swaps[0].tx_hash
        current_swaps: List[AMMSwap] = []
        for swap in swaps:
            if swap.tx_hash != last_tx_hash:
                trades.extend(Uniswap._tx_swaps_to_trades(current_swaps))
                current_swaps = []

            current_swaps.append(swap)
            last_tx_hash = swap.tx_hash

        if len(current_swaps) != 0:
            trades.extend(Uniswap._tx_swaps_to_trades(current_swaps))
        return trades

    def _get_events_graph(
            self,
            address: ChecksumEthAddress,
            start_ts: Timestamp,
            end_ts: Timestamp,
            event_type: EventType,
    ) -> List[LiquidityPoolEvent]:
        """Get the address' events (mints & burns) querying the Uniswap subgraph

        Each event data is stored in a <LiquidityPoolEvent>.
        """
        address_events = []
        param_types = {
            '$limit': 'Int!',
            '$offset': 'Int!',
            '$address': 'Bytes!',
            '$start_ts': 'BigInt!',
            '$end_ts': 'BigInt!',
        }
        param_values = {
            'limit': GRAPH_QUERY_LIMIT,
            'offset': 0,
            'address': address.lower(),
            'start_ts': str(start_ts),
            'end_ts': str(end_ts),
        }
        query = MINTS_QUERY if event_type == EventType.MINT else BURNS_QUERY
        querystr = format_query_indentation(query.format())
        query_schema = 'mints' if event_type == EventType.MINT else 'burns'

        while True:
            result = self.graph.query(  # type: ignore # caller already checks
                querystr=querystr,
                param_types=param_types,
                param_values=param_values,
            )
            result_data = result[query_schema]

            for event in result_data:
                token0_ = event['pair']['token0']
                token1_ = event['pair']['token1']
                token0 = get_ethereum_token(
                    symbol=token0_['symbol'],
                    ethereum_address=to_checksum_address(token0_['id']),
                    name=token0_['name'],
                    decimals=token0_['decimals'],
                )
                token1 = get_ethereum_token(
                    symbol=token1_['symbol'],
                    ethereum_address=to_checksum_address(token1_['id']),
                    name=token1_['name'],
                    decimals=int(token1_['decimals']),
                )
                lp_event = LiquidityPoolEvent(
                    tx_hash=event['transaction']['id'],
                    log_index=int(event['logIndex']),
                    address=address,
                    timestamp=Timestamp(int(event['timestamp'])),
                    event_type=event_type,
                    pool_address=to_checksum_address(event['pair']['id']),
                    token0=token0,
                    token1=token1,
                    amount0=AssetAmount(event['amount0']),
                    amount1=AssetAmount(event['amount1']),
                    usd_price=Price(event['amountUSD']),
                    lp_amount=AssetAmount(event['liquidity']),
                )
                address_events.append(lp_event)

            # Check whether an extra request is needed
            if len(result_data) < GRAPH_QUERY_LIMIT:
                break

            # Update pagination step
            param_values = {
                **param_values,
                'offset': param_values['offset'] + GRAPH_QUERY_LIMIT,  # type: ignore
            }

        print("\n\n\n\n")
        return address_events

    def _get_trades_graph(
            self,
            addresses: List[ChecksumEthAddress],
            start_ts: Timestamp,
            end_ts: Timestamp,
    ) -> AddressTrades:
        address_trades = {}
        for address in addresses:
            trades = self._get_trades_graph_for_address(address, start_ts, end_ts)
            if len(trades) != 0:
                address_trades[address] = trades

        return address_trades

    def _get_trades_graph_for_address(
            self,
            address: ChecksumEthAddress,
            start_ts: Timestamp,
            end_ts: Timestamp,
    ) -> List[AMMTrade]:
        """Get the address' trades data querying the Uniswap subgraph

        Each trade (swap) instantiates an <AMMTrade>.

        The trade pair (i.e. BASE_QUOTE) is determined by `reserve0_reserve1`.
        Translated to Uniswap lingo:

        Trade type BUY:
        - `asset1In` (QUOTE, reserve1) is gt 0.
        - `asset0Out` (BASE, reserve0) is gt 0.

        Trade type SELL:
        - `asset0In` (BASE, reserve0) is gt 0.
        - `asset1Out` (QUOTE, reserve1) is gt 0.
        """
        trades: List[AMMTrade] = []
        param_types = {
            '$limit': 'Int!',
            '$offset': 'Int!',
            '$address': 'Bytes!',
            '$start_ts': 'BigInt!',
            '$end_ts': 'BigInt!',
        }
        param_values = {
            'limit': GRAPH_QUERY_LIMIT,
            'offset': 0,
            'address': address.lower(),
            'start_ts': str(start_ts),
            'end_ts': str(end_ts),
        }
        querystr = format_query_indentation(SWAPS_QUERY.format())

        while True:
            result = self.graph.query(  # type: ignore # caller already checks
                querystr=querystr,
                param_types=param_types,
                param_values=param_values,
            )
            result_data = result['swaps']
            for entry in result_data:
                swaps = []
                for swap in entry['transaction']['swaps']:
                    timestamp = swap['timestamp']
                    swap_token0 = swap['pair']['token0']
                    swap_token1 = swap['pair']['token1']
                    token0 = get_ethereum_token(
                        symbol=swap_token0['symbol'],
                        ethereum_address=to_checksum_address(swap_token0['id']),
                        name=swap_token0['name'],
                        decimals=swap_token0['decimals'],
                    )
                    token1 = get_ethereum_token(
                        symbol=swap_token1['symbol'],
                        ethereum_address=to_checksum_address(swap_token1['id']),
                        name=swap_token1['name'],
                        decimals=int(swap_token1['decimals']),
                    )
                    amount0_in = FVal(swap['amount0In'])
                    amount1_in = FVal(swap['amount1In'])
                    amount0_out = FVal(swap['amount0Out'])
                    amount1_out = FVal(swap['amount1Out'])
                    swaps.append(AMMSwap(
                        tx_hash=swap['id'].split('-')[0],
                        log_index=int(swap['logIndex']),
                        address=address,
                        from_address=to_checksum_address(swap['sender']),
                        to_address=to_checksum_address(swap['to']),
                        timestamp=Timestamp(int(timestamp)),
                        location=Location.UNISWAP,
                        token0=token0,
                        token1=token1,
                        amount0_in=AssetAmount(amount0_in),
                        amount1_in=AssetAmount(amount1_in),
                        amount0_out=AssetAmount(amount0_out),
                        amount1_out=AssetAmount(amount1_out),
                    ))

                # Now that we got all swaps for a transaction, create the trade object
                trades.extend(self._tx_swaps_to_trades(swaps))

            # Check whether an extra request is needed
            if len(result_data) < GRAPH_QUERY_LIMIT:
                break

            # Update pagination step
            param_values = {
                **param_values,
                'offset': param_values['offset'] + GRAPH_QUERY_LIMIT,  # type: ignore
            }
        return trades

    @staticmethod
    def _get_unknown_asset_price_graph(
            unknown_assets: Set[UnknownEthereumToken],
            graph_query: Callable,
    ) -> AssetPrice:
        """Get today's tokens prices via the Uniswap subgraph

        Uniswap provides a token price every day at 00:00:00 UTC
        """
        asset_price: AssetPrice = {}

        unknown_assets_addresses = (
            [asset.ethereum_address for asset in unknown_assets]
        )
        unknown_assets_addresses_lower = (
            [address.lower() for address in unknown_assets_addresses]
        )

        querystr = format_query_indentation(TOKEN_DAY_DATAS_QUERY.format())
        today_epoch = int(
            datetime.combine(datetime.utcnow().date(), time.min).timestamp(),
        )
        param_types = {
            '$limit': 'Int!',
            '$offset': 'Int!',
            '$token_ids': '[String!]',
            '$datetime': 'Int!',
        }
        param_values = {
            'limit': GRAPH_QUERY_LIMIT,
            'offset': 0,
            'token_ids': unknown_assets_addresses_lower,
            'datetime': today_epoch,
        }
        while True:
            result = graph_query(
                querystr=querystr,
                param_types=param_types,
                param_values=param_values,
            )
            result_data = result['tokenDayDatas']

            for tdd in result_data:
                token_address = to_checksum_address(tdd['token']['id'])
                asset_price[token_address] = Price(FVal(tdd['priceUSD']))

            # Check whether an extra request is needed
            if len(result_data) < GRAPH_QUERY_LIMIT:
                break

            # Update pagination step
            param_values = {
                **param_values,
                'offset': param_values['offset'] + GRAPH_QUERY_LIMIT,  # type: ignore
            }

        return asset_price

    @staticmethod
    def _update_assets_prices_in_address_balances(
            address_balances: AddressBalances,
            known_asset_price: AssetPrice,
            unknown_asset_price: AssetPrice,
    ) -> None:
        """Update the pools underlying assets prices in USD (prices obtained
        via Inquirer and the Uniswap subgraph)
        """
        for lps in address_balances.values():
            for lp in lps:
                # Try to get price from either known or unknown asset price.
                # Otherwise keep existing price (zero)
                total_user_balance = FVal(0)
                for asset in lp.assets:
                    asset_ethereum_address = asset.asset.ethereum_address
                    asset_usd_price = known_asset_price.get(
                        asset_ethereum_address,
                        unknown_asset_price.get(asset_ethereum_address, Price(ZERO)),
                    )
                    # Update <LiquidityPoolAsset> if asset USD price exists
                    if asset_usd_price != Price(ZERO):
                        asset.usd_price = asset_usd_price
                        asset.user_balance.usd_value = FVal(
                            asset.user_balance.amount * asset_usd_price,
                        )

                    total_user_balance += asset.user_balance.usd_value

                # Update <LiquidityPool> total balance in USD
                lp.user_balance.usd_value = total_user_balance

    def get_balances(
        self,
        addresses: List[ChecksumEthAddress],
    ) -> AddressBalances:
        """Get the addresses' balances in the Uniswap protocol

        Premium users can request balances either via the Uniswap subgraph or
        on-chain.
        """
        is_graph_mode = self.graph and self.premium

        if is_graph_mode:
            protocol_balance = self._get_balances_graph(
                addresses=addresses,
                graph_query=self.graph.query,  # type: ignore # caller already checks
            )
        else:
            protocol_balance = self._get_balances_chain(addresses)

        known_assets = protocol_balance.known_assets
        unknown_assets = protocol_balance.unknown_assets

        known_asset_price = self._get_known_asset_price(
            known_assets=known_assets,
            unknown_assets=unknown_assets,
            price_query=Inquirer().find_usd_price,
        )

        unknown_asset_price: AssetPrice = {}
        if is_graph_mode:
            unknown_asset_price = self._get_unknown_asset_price_graph(
                unknown_assets=unknown_assets,
                graph_query=self.graph.query,  # type: ignore # caller already checks
            )

        self._update_assets_prices_in_address_balances(
            address_balances=protocol_balance.address_balances,
            known_asset_price=known_asset_price,
            unknown_asset_price=unknown_asset_price,
        )

        return protocol_balance.address_balances

    def get_events_history(
        self,
        addresses: List[ChecksumEthAddress],
        reset_db_data: bool,
        from_timestamp: Timestamp,
        to_timestamp: Timestamp,
    ) -> AddressEventsBalances:
        """Get the addresses' events balances history in the Uniswap protocol
        """
        if self.graph is None:  # could not initialize graph
            return {}

        with self.trades_lock:
            if reset_db_data is True:
                self.database.delete_uniswap_events_data()

            address_events_balances = self._get_events_balances(
                addresses=addresses,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
            )

        return address_events_balances

    def get_trades(
            self,
            addresses: List[ChecksumEthAddress],
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> List[AMMTrade]:
        with self.trades_lock:
            all_trades = []
            trade_mapping = self._get_trades(
                addresses=addresses,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
            )
            for _, trades in trade_mapping.items():
                all_trades.extend(trades)

            return all_trades

    def get_trades_history(
        self,
        addresses: List[ChecksumEthAddress],
        reset_db_data: bool,
        from_timestamp: Timestamp,
        to_timestamp: Timestamp,
    ) -> AddressTrades:
        """Get the addresses' trades history in the Uniswap protocol
        """
        if self.graph is None:  # could not initialize graph
            return {}

        with self.trades_lock:
            if reset_db_data is True:
                self.database.delete_uniswap_trades_data()

            trades = self._get_trades(
                addresses=addresses,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
            )

        return trades

    # -- Methods following the EthereumModule interface -- #
    def on_startup(self) -> None:
        pass

    def on_account_addition(self, address: ChecksumEthAddress) -> None:
        pass

    def on_account_removal(self, address: ChecksumEthAddress) -> None:
        pass
