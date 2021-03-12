from typing import NewType, Union

KusamaAddress = NewType('KusamaAddress', str)
SubstrateAddress = Union[KusamaAddress]
SubstratePublicKey = NewType('SubstratePublicKey', str)
