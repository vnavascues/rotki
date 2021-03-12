import json
from enum import Enum
from typing import Any, Dict, List, NamedTuple, NewType, Tuple, Type, Union

from scalecodec.block import Extrinsic
from substrateinterface import SubstrateInterface

from rotkehlchen.chain.substrate.typing_addresses import SubstrateAddress, SubstratePublicKey
from rotkehlchen.fval import FVal
from rotkehlchen.typing import Timestamp

SubstrateChainId = NewType('SubstrateChainId', str)
BlockNumber = NewType('BlockNumber', int)


class KusamaNodeName(Enum):
    """Public nodes for Kusama.
    """
    OWN = 0
    PARITY = 1

    def __str__(self) -> str:
        if self == KusamaNodeName.OWN:
            return 'own node'
        if self == KusamaNodeName.PARITY:
            return 'parity'
        raise AssertionError(f'Unexpected KusamaNodeName: {self}')

    def endpoint(self) -> str:
        if self == KusamaNodeName.OWN:
            raise NotImplementedError(
                'The endpoint url for a substrate own node must be got either '
                'via "own_rpc_endpoint" or the specific db setting',
            )
        if self == KusamaNodeName.PARITY:
            return 'https://kusama-rpc.polkadot.io/'
        raise AssertionError(f'Unexpected KusamaNodeName: {self}')


NodeName = Union[KusamaNodeName]


class SubstrateInterfaceAttributes(NamedTuple):
    type_registry_preset: str


class SubstrateChain(Enum):
    """Supported Substrate chains.
    """
    KUSAMA = 1

    def __str__(self) -> SubstrateChainId:
        """Return the official chain identifier/name.
        """
        if self == SubstrateChain.KUSAMA:
            return SubstrateChainId('Kusama')
        raise AssertionError(f'Unexpected Chain: {self}')

    @classmethod
    def get_substrate_chain_from_chain_id(
            cls,
            chain_id: Union[SubstrateChainId, int],
    ) -> 'SubstrateChain':
        if chain_id in (SubstrateChainId('Kusama'), 1):
            return cls.KUSAMA
        raise AssertionError(f'Unexpected chain ID: {chain_id}')

    def chain_explorer_api(self) -> str:
        """Return the explorer API.

        NB: this simplified implementation relies on Subscan API supporting all
        the chains we introduce.
        """
        if self == SubstrateChain.KUSAMA:
            return 'https://kusama.subscan.io/api'
        raise AssertionError(f'Unexpected Chain: {self}')

    def substrate_interface_attributes(self) -> SubstrateInterfaceAttributes:
        """Return the attributes for instantiating SubstrateInterface.
        """
        if self == SubstrateChain.KUSAMA:
            return SubstrateInterfaceAttributes(type_registry_preset='kusama')
        raise AssertionError(f'Unexpected Chain: {self}')

    def blocks_threshold(self) -> BlockNumber:
        """Return the blocks difference that marks a node as unsynced.
        """
        if self == SubstrateChain.KUSAMA:
            return BlockNumber(10)
        raise AssertionError(f'Unexpected Chain: {self}')

    def node_name_type(self) -> Union[Type[KusamaNodeName]]:
        """Return the NodeName enum.
        """
        if self == SubstrateChain.KUSAMA:
            return KusamaNodeName
        raise AssertionError(f'Unexpected Chain: {self}')


class NodeNameAttributes(NamedTuple):
    node_interface: SubstrateInterface
    weight_block: BlockNumber


DictNodeNameNodeAttributes = Dict[NodeName, NodeNameAttributes]
NodesCallOrder = List[Tuple[NodeName, NodeNameAttributes]]


class SubstrateAddressBlockExtrinsicsData(NamedTuple):
    address: SubstrateAddress
    address_public_key: SubstratePublicKey
    block_number: BlockNumber
    block_hash: str
    block_timestamp: Timestamp
    extrinsics: List[Extrinsic]


SubstrateExtrinsicDBTuple = (
    Tuple[
        str,  # chain_id
        int,  # block_number
        str,  # block_hash
        int,  # block_timestamp
        int,  # extrinsic_index
        str,  # extrinsic_hash
        str,  # call_module
        str,  # call_module_function
        str,  # params
        str,  # account_id
        str,  # address
        int,  # nonce
        str,  # fee
    ]
)


class SubstrateExtrinsic(NamedTuple):
    chain_id: SubstrateChainId
    block_number: BlockNumber
    block_hash: str
    block_timestamp: Timestamp
    extrinsic_index: int
    extrinsic_hash: str
    call_module: str
    call_module_function: str
    params: List[Dict[str, Any]]
    account_id: SubstratePublicKey
    address: SubstrateAddress
    nonce: int
    fee: FVal

    def to_db_tuple(self) -> SubstrateExtrinsicDBTuple:
        params = json.dumps(self.params)
        db_tuple = (
            str(self.chain_id),
            int(self.block_number),
            self.block_hash,
            self.block_timestamp,
            self.extrinsic_index,
            self.extrinsic_hash,
            self.call_module,
            self.call_module_function,
            params,
            str(self.account_id),
            str(self.address),
            self.nonce,
            str(self.fee),
        )
        return db_tuple
