# import time

# import pytest
# from substrateinterface import ExtrinsicReceipt, SubstrateInterface

# TEST_ADDR1 = 'DJXRnqb3aTRpQfZtfZKFB3rXrDcdKjyS7C3BrrB5oWMDrxJ'
# TEST_ADDR1_PK = '0x203066b0a657bdbdbe9974c20a2644881f384f9b206c7c394054c0d411d7bc6e'
# URL_HTTPS = 'https://kusama-rpc.polkadot.io'
# URL_WSS = 'wss://kusama-rpc.polkadot.io'
# # URL = 'wss://cc3-5.kusama.network/'
# # BLOCK_START_AT = 1
# BLOCK_START_AT = 5662970
# """
# TODO:
# - If account is not a nominator or validator... do not check staking.
# - Ideally:
#     - Get account role
#     - Get account Staking activity -> nominators
#     - Filter extrinsics with "payout_stakers" from any of the account nominators
#         !This prevents check block by block

# """
# def test_block_transfer():
#     si = SubstrateInterface(
#         url='wss://kusama-rpc.polkadot.io/',
#         type_registry_preset='kusama',
#         use_remote_preset=True,
#     )
#     block_id = 5662971
#     block_hash = si.get_block_hash(block_id=block_id)
#     extrinsics = si.get_block_extrinsics(block_id=block_id)

#     print("\n*** Extrinsics\n")
#     for extrinsic in extrinsics:
#         print(extrinsic)
#         if extrinsic.contains_transaction:
#             #print(dir(extrinsic))
#             #print("----")
#             print(extrinsic)
#             # print(extrinsic.serialize())
#             # print(extrinsic.address)
#             # # print(dir(extrinsic.address))
#             # print(extrinsic.address.account_id)
#             # print(extrinsic.nonce.value)
#         print("-----")

#     events = si.get_events(block_hash)
#     print("\n*** Events\n")
#     for event in events:
#         print(event)
#         print("----")
#     # 7 events
#     # 1 Transfer event (below)
#     """
#     {
#         'phase': 0,
#         'extrinsic_idx': 1,
#         'event_index': '0402',
#         'module_id': 'Balances',
#         'event_id': 'Transfer',
#         'params': [
#             {
#                 'type': 'AccountId',
#                 'value': '0x203066b0a657bdbdbe9974c20a2644881f384f9b206c7c394054c0d411d7bc6e'
#             },
#             {
#                 'type': 'AccountId',
#                 'value': '0x0cc6c2888a0e296e770c6f7d56db16ec5cb6dfe935ea336d94321ed8ac88cb1a'
#             },
#             {
#                 'type': 'Balance',
#                 'value': 200000000000000
#             }
#         ],
#         'topics': [],
#         'event_idx': 3,
#     }
#     """
#     # for extrinsic in extrinsics:
#     #     print(extrinsic.call_module)  # Staking
#     #     print(extrinsic.call_function)  # payout_stakers
#     #     print("\n")

#     # # Get extrinsic receipt
#     # extrinsic_staking = ExtrinsicReceipt(
#     #     substrate=si,
#     #     extrinsic_hash=extrinsics[1].extrinsic_hash,
#     #     block_hash=block_hash,
#     # )
#     # extrinsic_events = extrinsic_staking.process_events
#     # for ex_event in extrinsic_events:
#     #     print(ex_event)
#     #     print("\n\n\n")
#     # all_bonded_stash = si.query(
#     #     module='Staking',
#     #     storage_function='Nominators',
#     #     params=[TEST_ADDR1],
#     #     # block_hash=block_hash,
#     # )
#     # print("\n\n")
#     # print(all_bonded_stash)
#     # for event in events:
#     # print(events)


# def test_block_reward():
#     si = SubstrateInterface(
#         url='wss://kusama-rpc.polkadot.io/',
#         type_registry_preset='kusama',
#         use_remote_preset=True,
#     )
#     block_id = 6515014
#     block_hash = si.get_block_hash(block_id=block_id)
#     extrinsics = si.get_block_extrinsics(block_id=block_id)

#     print("\n*** Extrinsics\n")
#     for extrinsic in extrinsics:
#         print(extrinsic.serialize())
#         if extrinsic.contains_transaction:
#             print(extrinsic.address)
#             print(dir(extrinsic.address))
#             print(extrinsic.address.account_id)
#         print("-----")

#     events = si.get_events(block_hash)
#     print("\n*** Events\n")
#     # print(len(events))
#     for event in events:
#         event_sz = event.serialize()
#         params = event_sz.get('params', [])
#         for param in params:
#             value = param.get('value')
#             if value == '0x203066b0a657bdbdbe9974c20a2644881f384f9b206c7c394054c0d411d7bc6e':
#                 print(event_sz)


# def test_block_batch_bond():
#     si = SubstrateInterface(
#         url='wss://kusama-rpc.polkadot.io/',
#         type_registry_preset='kusama',
#         use_remote_preset=True,
#     )
#     block_id = 6321708
#     block_hash = si.get_block_hash(block_id=block_id)
#     extrinsics = si.get_block_extrinsics(block_id=block_id)

#     print("\n*** Extrinsics\n")
#     for extrinsic in extrinsics:
#         print(extrinsic.serialize())
#         print("-----")

#     events = si.get_events(block_hash)
#     print("\n*** Events\n")
#     print(len(events))
#     for event in events:
#         print(event)
#         print("----")


# def test_block_account_creation():
#     si = SubstrateInterface(
#         url='wss://kusama-rpc.polkadot.io/',
#         type_registry_preset='kusama',
#         use_remote_preset=True,
#     )
#     block_id = 4569164
#     block_hash = si.get_block_hash(block_id=block_id)
#     extrinsics = si.get_block_extrinsics(block_id=block_id)

#     print("\n*** Extrinsics\n")
#     for extrinsic in extrinsics:
#         print(extrinsic)
#         if extrinsic.contains_transaction:
#             print(extrinsic.address)
#             # print(dir(extrinsic.address))
#             print(extrinsic.address.account_id)
#         print("-----")

#     events = si.get_events(block_hash)
#     print("\n*** Events\n")
#     for event in events:
#         print(event)
#         print("----")


# def test_block_nominate():
#     si = SubstrateInterface(
#         url='wss://kusama-rpc.polkadot.io/',
#         type_registry_preset='kusama',
#         use_remote_preset=True,
#     )
#     block_id = 6321914
#     block_hash = si.get_block_hash(block_id=block_id)
#     extrinsics = si.get_block_extrinsics(block_id=block_id)

#     print("\n*** Extrinsics\n")
#     for extrinsic in extrinsics:
#         print(extrinsic.serialize())
#         print("-----")

#     events = si.get_events(block_hash)
#     print("\n*** Events\n")
#     print(len(events))
#     for event in events:
#         print(event)
#         print("----")


# def test_pull_blocks_performance():
#     si = SubstrateInterface(
#         url=URL_WSS,
#         type_registry_preset='kusama',
#         use_remote_preset=True,
#     )
#     block = 5662970
#     block_hash = si.get_block_hash(block)
#     # block_header = si.get_block_header(block_hash)
#     start = time.time()
#     for b in range(block, block + 500):
#         block_extrinsics = si.get_block_extrinsics(block_id=b)
#         # print(type(block_extrinsics))
#         # print(block_extrinsics)
#     end = time.time()
#     print("\n\n")
#     print(end - start)


# def test_queues():
#     import gevent
#     from gevent.queue import Queue

#     queue = Queue()

#     def producer(queue):
#         n = 0
#         while True:
#             queue.put(n)
#             print(f"*** put: {n}")
#             gevent.sleep(2)
#             n += 1

#     def consumer(queue):
#         while True:
#             if len(queue) > 5:
#                 items = []
#                 for idx, item in enumerate(queue):
#                     items.append(item)
#                     if idx == 2:
#                         break

#                 print(f"*** items: {items}")
#             print("out of loop")
#             gevent.sleep(4)

#     gevent.joinall([
#         gevent.spawn(producer, queue),
#         gevent.spawn(consumer, queue),
#     ])

# def test_pepe():
#     from rotkehlchen.chain.substrate.typing import SubstrateChain, SubstrateChainId
#     from rotkehlchen.chain.substrate.typing_addresses import KusamaAddress, SubstrateAddress
#     from substrate_indexer.event_payloads import EventStartIndexerData, EventStartIndexingData

#     BLOCK_NUMBER_START_AT = 5662971
#     KUSAMA_TEST_ADDRESS = KusamaAddress('DJXRnqb3aTRpQfZtfZKFB3rXrDcdKjyS7C3BrrB5oWMDrxJ')

#     xx = EventStartIndexingData(
#         substrate_chain=SubstrateChain.KUSAMA,
#         block_number_start_at=BLOCK_NUMBER_START_AT,
#         address=KUSAMA_TEST_ADDRESS,
#     )
#     print("\n\n\n")
#     print(xx)
#     xx.address = 'pepe'
