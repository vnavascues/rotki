import logging
from functools import wraps
from http import HTTPStatus
from json.decoder import JSONDecodeError
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple, cast
from urllib.parse import urlparse

import requests
from requests.adapters import Response
from substrateinterface import SubstrateInterface
from substrateinterface.exceptions import SubstrateRequestException
from typing_extensions import Literal

from rotkehlchen.assets.asset import Asset
from rotkehlchen.constants.misc import ZERO
from rotkehlchen.errors import RemoteError, UnknownAsset
from rotkehlchen.fval import FVal
from rotkehlchen.greenlets import GreenletManager
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.user_messages import MessagesAggregator
from rotkehlchen.utils.serialization import rlk_jsonloads_dict

from .typing import (
    BlockNumber,
    DictNodeNameNodeAttributes,
    NodeName,
    NodeNameAttributes,
    NodesCallOrder,
    SubstrateAddress,
    SubstrateChain,
    SubstrateChainId,
    SubstrateOwnNodeName,
)

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


class SubstrateChainProperties(NamedTuple):
    """These properties are populated straight from the blockchain.

    Chain properties from `<SubstrateInterface>.properties` response:
    - 'ss58Format' (int): the default Substrate address format of the chain.
    - 'tokenDecimals' (int): the number of decimals of the native token.
    - 'tokenSymbol' (str): the identifier of the native token.

    External Address Format (SS58) documentation:
    https://github.com/paritytech/substrate/wiki/External-Address-Format-(SS58)
    """
    ss58_format: int
    token: Asset  # from instantiating Asset with 'tokenSymbol'
    token_decimals: FVal


def request_available_nodes(func: Callable) -> Callable:
    """Given a function calls it sequentially for each available node. If the
    call is successful returns, otherwise calls it again for the next node.

    How to use: decorate any SubstrateManager instance method that requests the
    chain via `node_interface` (<SubstrateInterface>). Make sure `node_interface`
    is a keyword argument of the decorated instance method.

    NB: every time a new method is decorated with this function or an existing
    one is modified, make sure its exceptions are handled as expected. It may
    require to extend the exceptions tuple of the try...except block that wraps
    the method call.
    """
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not isinstance(args[0], SubstrateManager):
            raise AssertionError(
                f'Unexpected first positional argument: {args[0]}. This function '
                f'must be called with SubstrateManager instance methods',
            )

        manager = args[0]
        if len(manager.available_node_attributes_map) == 0:
            raise RemoteError(f'{manager.chain} has no nodes available')

        args_ = args[1:]
        kwargs_ = kwargs.copy()
        requested_nodes = []
        for node, node_attributes in manager._get_nodes_call_order():
            kwargs_.update({'node_interface': node_attributes.node_interface})
            try:
                result = func(manager, *args_, **kwargs_)
            except RemoteError as e:
                requested_nodes.append(str(node))
                endpoint = node_attributes.node_interface.url
                log.warning(
                    f'{manager.chain} {func.__name__!r} failed to request via '
                    f'{node} node at endpoint {endpoint} due to: {str(e)}.',
                    args=args[1:],
                    kwargs=kwargs_,
                )
                continue

            return result

        raise RemoteError(
            f'{manager.chain} request failed after trying the following nodes: '
            f'{", ".join(requested_nodes)}',
        )
    return wrapper


class SubstrateManager():
    def __init__(
            self,
            chain: SubstrateChain,
            greenlet_manager: GreenletManager,
            msg_aggregator: MessagesAggregator,
            connect_at_start: Sequence[NodeName],
            own_rpc_endpoint: str,
    ) -> None:
        """An interface to any Substrate chain supported by Rotki.

        It uses Polkascan py-substrate-interface for interacting with the
        substrate blockchains and the Subscan API as a chain explorer.

        Official substrate chains documentation:
        https://substrate.dev/rustdocs/v2.0.0/sc_service/index.html
        https://guide.kusama.network/docs/en/kusama-index
        https://wiki.polkadot.network/en/

        External Address Format (SS58) documentation:
        https://github.com/paritytech/substrate/wiki/External-Address-Format-(SS58)

        Polkascan py-scale-codec:
        https://github.com/polkascan/py-scale-codec/tree/master

        Polkascan py-substrate-interface:
        https://github.com/polkascan/py-substrate-interface
        https://polkascan.github.io/py-substrate-interface/base.html

        Subscan API documentation:
        https://docs.api.subscan.io
        """
        if chain not in SubstrateChain:
            raise AttributeError(f'Unexpected SubstrateManager chain: {chain}')

        log.debug(f'Initializing {chain} manager')
        self.chain = chain
        self.greenlet_manager = greenlet_manager
        self.msg_aggregator = msg_aggregator
        self.available_node_attributes_map: DictNodeNameNodeAttributes = {}
        self.chain_properties: SubstrateChainProperties
        if len(connect_at_start) != 0:
            self._attempt_connections(
                node_names=connect_at_start,
                own_rpc_endpoint=own_rpc_endpoint,
            )
        else:
            log.warning(f"{self.chain} manager won't attempt to connect to nodes")

    def _check_chain_id(self, node_interface: SubstrateInterface) -> None:
        """Validate a node connects to the expected chain.

        May raise:
        - RemoteError: the chain ID request fails, or the chain ID is not the
        expected one.
        """
        # Check connection and chain ID
        chain_id = self._get_chain_id(node_interface=node_interface)

        if chain_id != str(self.chain):
            message = (
                f'{self.chain} found unexpected chain {chain_id} when attempted '
                f'to connect to node at endpoint: {node_interface.url}, '
            )
            log.error(message)
            raise RemoteError(message)

    def _check_node_synchronization(self, node_interface: SubstrateInterface) -> BlockNumber:
        """Check the node synchronization comparing the last block obtained via
        the node interface against the last block obtained via Subscan API.
        Return the last block obtained via the node interface.

        May raise:
        - RemoteError: the last block/chain metadata requests fail or
        there is an error deserializing the chain metadata.
        """
        # Last block via node interface
        last_block = self._get_last_block(node_interface=node_interface)

        # Last block via Subscan API
        try:
            chain_metadata = self._request_chain_metadata()
        except RemoteError:
            self.msg_aggregator.add_warning(
                f'Unable to verify that {self.chain} node at endpoint {node_interface.url} '
                f'is synced with the chain. Balances and other queries may be incorrect.',
            )
            return last_block

        # Check node synchronization
        try:
            metadata_last_block = BlockNumber(chain_metadata['data']['blockNum'])
        except KeyError as e:
            message = f'{self.chain} failed to deserialize the chain metadata response: {str(e)}.'
            log.error(message, chain_metadata=chain_metadata)
            raise RemoteError(message) from e

        log.debug(
            f'{self.chain} subscan API metadata last block',
            metadata_last_block=metadata_last_block,
        )
        if metadata_last_block - last_block > self.chain.blocks_threshold():
            self.msg_aggregator.add_warning(
                f'Found that {self.chain} node at endpoint {node_interface.url} '
                f'is not synced with the chain. Node last block is {last_block}, '
                f'expected last block is {metadata_last_block}. '
                f'Balances and other queries may be incorrect.',
            )

        return last_block

    def _attempt_connections(
            self,
            node_names: Sequence[NodeName],
            own_rpc_endpoint: str,
    ) -> None:
        for node in node_names:
            if node == SubstrateOwnNodeName.OWN:
                endpoint = self._format_own_rpc_endpoint(own_rpc_endpoint)
            else:
                endpoint = node.endpoint()

            self.greenlet_manager.spawn_and_track(
                after_seconds=None,
                task_name=f'{self.chain} manager connection to {node} node',
                exception_is_error=True,
                method=self._connect_node,
                node=node,
                endpoint=endpoint,
            )

    def _connect_node(
            self,
            node: NodeName,
            endpoint: str,
    ) -> Tuple[bool, str]:
        """Attempt to connect to a node, check its status and store its
        attributes (e.g. interface, weight) in the available nodes map.

        May raise:
        - RemoteError: connecting to a node fails at any of the steps executed.
        """
        if node in self.available_node_attributes_map:
            message = f'{self.chain} already connected to {node} node at endpoint: {endpoint}.'
            return True, message

        try:
            node_interface = self._get_node_interface(endpoint)
            self._check_chain_id(node_interface)
            last_block = self._check_node_synchronization(node_interface)
            self._set_chain_properties(node_interface)
        except RemoteError as e:
            message = (
                f'{self.chain} failed to connect to {node} at endpoint {endpoint}, '
                f'due to {str(e)}.'
            )
            return False, message

        log.info(f'{self.chain} connected to {node} node at endpoint: {node_interface.url}.')
        self.available_node_attributes_map[node] = NodeNameAttributes(
            node_interface=node_interface,
            weight_block=last_block,
        )
        if node == SubstrateOwnNodeName.OWN:
            self.own_rpc_endpoint = endpoint
        return True, ''

    @staticmethod
    def _format_own_rpc_endpoint(endpoint: str) -> str:
        return f'http://{endpoint}' if not urlparse(endpoint).scheme else endpoint

    def _get_account_balance(
            self,
            account: SubstrateAddress,
            node_interface: SubstrateInterface,
    ) -> FVal:
        """Given an account get its amount of chain native token.

        More information about an account balance in the Substrate AccountData
        documentation.
        """
        log.debug(
            f'{self.chain} querying {self.chain_properties.token.identifier} balance',
            url=node_interface.url,
            account=account,
        )
        try:
            result = node_interface.query(
                module='System',
                storage_function='Account',
                params=[account],
            )
        except (
            requests.exceptions.RequestException,
            SubstrateRequestException,
            ValueError,
        ) as e:
            message = (
                f'{self.chain} failed to request {self.chain_properties.token.identifier} account '
                f'balance at endpoint {node_interface.url} due to: {str(e)}'
            )
            log.error(message, account=account)
            raise RemoteError(message) from e

        log.debug(
            f'{self.chain} account balance',
            account=account,
            result=result,
        )
        balance = ZERO
        if result is not None:
            account_data = result.value['data']
            balance = (
                FVal(account_data['free'] + account_data['reserved']) /
                FVal('10') ** self.chain_properties.token_decimals
            )

        return balance

    def _get_chain_id(self, node_interface: SubstrateInterface) -> SubstrateChainId:
        """Return the chain identifier.
        """
        log.debug(f'{self.chain} querying chain ID', url=node_interface.url)
        try:
            chain_id = node_interface.chain
        except (requests.exceptions.RequestException, SubstrateRequestException) as e:
            message = (
                f'{self.chain} failed to request chain ID '
                f'at endpoint: {node_interface.url} due to: {str(e)}.'
            )
            log.error(message)
            raise RemoteError(message) from e

        log.debug(f'{self.chain} chain ID', chain_id=chain_id)
        return SubstrateChainId(chain_id)

    def _get_chain_properties(
            self,
            node_interface: SubstrateInterface,
    ) -> SubstrateChainProperties:
        """Return the chain properties.
        """
        log.debug(f'{self.chain} querying chain properties', url=node_interface.url)
        try:
            properties = node_interface.properties
        except (requests.exceptions.RequestException, SubstrateRequestException) as e:
            message = (
                f'{self.chain} failed to request chain properties '
                f'at endpoint: {node_interface.url} due to: {str(e)}.'
            )
            log.error(message)
            raise RemoteError(message) from e

        log.debug(f'{self.chain} chain properties', properties=properties)
        try:
            chain_properties = SubstrateChainProperties(
                ss58_format=properties['ss58Format'],
                token=Asset(properties['tokenSymbol']),
                token_decimals=FVal(properties['tokenDecimals']),
            )
        except (KeyError, UnknownAsset) as e:
            message = f'{self.chain} failed to deserialize properties due to: {str(e)}.'
            log.error(message, properties=properties)
            raise RemoteError(message) from e

        return chain_properties

    def _get_last_block(self, node_interface: SubstrateInterface) -> BlockNumber:
        """Return the chain height.
        """
        log.debug(f'{self.chain} querying last block', url=node_interface.url)
        try:
            last_block = node_interface.get_block_number(
                block_hash=node_interface.get_chain_head(),
            )
        except (
            requests.exceptions.RequestException,
            SubstrateRequestException,
            # TODO: remove TypeError once py-susbtrate-interface `get_block_number`
            # handles a None response. Keep ValueError just in case `get_chain_head`
            # returns None.
            # https://github.com/polkascan/py-substrate-interface/issues/68
            TypeError,
            ValueError,
        ) as e:
            message = (
                f'{self.chain} failed to request last block '
                f'at endpoint: {node_interface.url} due to: {str(e)}.'
            )
            log.error(message)
            raise RemoteError(message) from e

        log.debug(f'{self.chain} last block', last_block=last_block)
        return BlockNumber(last_block)

    def _get_nodes_call_order(self) -> NodesCallOrder:
        """Return a list of maps between a node and its attributes sorted by
        preference.

        Own node always has preference, then are ordered depending on how close
        they are to the chain height; the higher 'weight_block' the better.
        """
        nodes_sorted: NodesCallOrder = []
        node_attributes_map = self.available_node_attributes_map.copy()
        if SubstrateOwnNodeName.OWN in node_attributes_map:
            own_node_attrs = node_attributes_map.pop(SubstrateOwnNodeName.OWN)
            nodes_sorted.append((SubstrateOwnNodeName.OWN, own_node_attrs))

        nodes_sorted.extend(
            sorted(
                cast(Iterable, node_attributes_map.items()),
                key=lambda item: -item[1].weight_block,
            ),
        )
        return nodes_sorted

    def _get_node_endpoint(self, node: NodeName) -> str:
        return self.own_rpc_endpoint if node == SubstrateOwnNodeName.OWN else node.endpoint()

    def _get_node_interface(self, endpoint: str) -> SubstrateInterface:
        """Get an instance of SubstrateInterface, a specialized class in
        interfacing with a Substrate node that deals with SCALE encoding/decoding,
        metadata parsing, type registry management and versioning of types.

        May raise (most common):
        - RemoteError: from RequestException, problems requesting the url.
        - FileNotFound: via `load_type_registry_preset()` if it doesn't exist
        a preset file for the given `type_registry_preset` argument.
        - ValueError and TypeError: invalid constructor arguments.
        """
        si_attributes = self.chain.substrate_interface_attributes()
        try:
            node_interface = SubstrateInterface(
                url=endpoint,
                type_registry_preset=si_attributes.type_registry_preset,
            )
        except requests.exceptions.RequestException as e:
            message = (
                f'{self.chain} could not connect to node at endpoint: {endpoint}. '
                f'Connection error: {str(e)}.',
            )
            log.error(message)
            raise RemoteError(message) from e
        except (FileNotFoundError, ValueError, TypeError) as e:
            raise AttributeError('Invalid SubstrateInterface instantiation') from e

        return node_interface

    def _request_explorer_api(self, endpoint: Literal['metadata']) -> Response:
        if endpoint == 'metadata':
            url = f'{self.chain.chain_explorer_api()}/scan/metadata'
        else:
            raise AssertionError(f'Unexpected {self.chain} endpoint type: {endpoint}')

        log.debug(f'{self.chain} subscan API request', request_url=url)
        try:
            response = requests.post(url=url)
        except requests.exceptions.RequestException as e:
            message = f'{self.chain} failed to post request at {url}. Connection error: {str(e)}.'
            log.error(message)
            raise RemoteError(message) from e

        return response

    def _request_chain_metadata(self) -> Dict[str, Any]:
        """Subscan API metadata documentation:
        https://docs.api.subscan.io/#metadata
        """
        response = self._request_explorer_api(endpoint='metadata')
        if response.status_code != HTTPStatus.OK:
            message = (
                f'{self.chain} chain metadata request was not successful. '
                f'Response status code: {response.status_code}. '
                f'Response text: {response.text}.',
            )
            log.error(message)
            raise RemoteError(message)
        try:
            result = rlk_jsonloads_dict(response.text)
        except JSONDecodeError as e:
            message = (
                f'{self.chain} chain metadata request returned invalid JSON '
                f'response: {response.text}.',
            )
            log.error(message)
            raise RemoteError(message) from e

        log.debug(f'{self.chain} subscan API metadata', result=result)
        return result

    def _set_chain_properties(self, node_interface: SubstrateInterface) -> None:
        """Return the properties of the chain connected to (e.g. native token,
        addresses format).

        May raise:
        - RemoteError: the properties request fails or there is an error
        deserializing it.
        """
        if isinstance(getattr(self, 'chain_properties', None), SubstrateChainProperties):
            log.debug(
                f'{self.chain} has already the chain properties',
                chain_properties=self.chain_properties,
            )
            return None

        self.chain_properties = self._get_chain_properties(node_interface)
        return None

    @request_available_nodes
    def get_account_balance(
            self,
            account: SubstrateAddress,
            node_interface: Optional[SubstrateInterface] = None,
    ) -> FVal:
        """Given an account get its amount of chain native token.

        May raise:
        - RemoteError: `request_available_nodes()` fails to request after
        trying with all the available nodes.
        """
        try:
            balance = self._get_account_balance(
                account=account,
                node_interface=node_interface,
            )
        except RemoteError as e:
            self.msg_aggregator.add_error(
                f'Got remote error while querying {self.chain} '
                f'{self.chain_properties.token.identifier} balance for account '
                f'{account}: {str(e)}.',
            )
            raise

        return balance

    def get_accounts_balance(
            self,
            accounts: List[SubstrateAddress],
    ) -> Dict[SubstrateAddress, FVal]:
        """Given a list of accounts get their amount of chain native token.

        This method is not decorated with `request_available_nodes` on purpose,
        so each request can use all available nodes.

        May raise:
        - RemoteError: `request_available_nodes()` fails to request after
        trying with all the available nodes.
        """
        balances: Dict[SubstrateAddress, FVal] = {}
        for account in accounts:
            balances[account] = self.get_account_balance(account)

        return balances

    @request_available_nodes
    def get_chain_id(
            self,
            node_interface: Optional[SubstrateInterface] = None,
    ) -> SubstrateChainId:
        """
        May raise:
        - RemoteError: `request_available_nodes()` fails to request after
        trying with all the available nodes.
        """
        return self._get_chain_id(node_interface)

    @request_available_nodes
    def get_chain_properties(
            self,
            node_interface: Optional[SubstrateInterface] = None,
    ) -> SubstrateChainProperties:
        """
        May raise:
        - RemoteError: `request_available_nodes()` fails to request after
        trying with all the available nodes.
        """
        return self._get_chain_properties(node_interface)

    @request_available_nodes
    def get_last_block(
            self,
            node_interface: Optional[SubstrateInterface] = None,
    ) -> BlockNumber:
        """
        May raise:
        - RemoteError: `request_available_nodes()` fails to request after
        trying with all the available nodes.
        """
        return self._get_last_block(node_interface)

    def set_rpc_endpoint(self, endpoint: str) -> Tuple[bool, str]:
        """Attempt to set the RPC endpoint for the user's own node.
        If connection at endpoint is successful it will set `own_rpc_endpoint`.
        """
        if endpoint == '':
            current_own_rpc_endpoint = getattr(self, 'own_rpc_endpoint', None)
            log.debug(f'{self.chain} removing own node at endpoint: {current_own_rpc_endpoint}')
            self.available_node_attributes_map.pop(SubstrateOwnNodeName.OWN, None)
            self.own_rpc_endpoint = ''
            return True, ''

        log.debug(f'{self.chain} setting own node at endpoint: {endpoint}')
        return self._connect_node(
            node=SubstrateOwnNodeName.OWN,
            endpoint=self._format_own_rpc_endpoint(endpoint),
        )
