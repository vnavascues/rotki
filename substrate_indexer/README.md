# How to run

1. Install the new requirements, e.g. `gevent-websocket`, `flask-socketio`.
2. Run the SocketIO server (do not use the Flask command):

```shell
python -m substrate_indexer
```

3. Run the client to showcase some indexing

```shell
cd substrate_indexer
python -m client
```

**TO BE AWARE**: just for testing purposes the `Indexer` function `_get_address_block_extrinsics_data()` has commented out the right filtering condition. Having it commented out allows to filter-in any extrinsic (not inherent) that contains a transaction, no matter the origin address.

# TODO

## General

- SocketIO server command-line options (via argparse).
- Logging & MessagesAggregator.
- DB connection (currently uses DBHandler).
- Client and server configs for local, test and prod environments.
- Test suite.
- Folder structure, and place within the repo.
- Integration with rotkehlchen server.
- Packaging.

## SQLite and DB schema

[json1](https://www.sqlite.org/json1.html)
[Table-valued functions](https://www.sqlite.org/vtab.html#tabfunc2)
[Run-Time Loadable Extensions](https://www.sqlite.org/loadext.html)

- It is required to store the `params` value (JSON array) of a Substrate extrinsic; it contains essential data and we do the same with the input data of an Ethereum transaction. Some type of extrinsics (e.g. batch) have big and deeply nested JSONs. Currently the `params` column in the `substrate_extrinsics` table has type JSON (which enables some querying capabilities over the column).
- Decide whether `params` should use a JSON column or a TEXT column.
- In case of using a JSON column make sure that the `json1` extension works as expected and can be installed via packaged binaries.
- In case of using a JSON column be aware of the table-valued functions (see docs).

## SocketIO client and server

[Client: python-socketIO](https://python-socketio.readthedocs.io/en/latest/index.html)
[Server: flask-SocketIO](https://flask-socketio.readthedocs.io/en/latest/)

Some of the following topics pending to address have a specific section on the official documentation.

- Logging
- Flask config
- CORS?
- Command-line options (via argparse).
- Production server (the gevent web server seems ok).
- Design pattern: class-based vs decorator-based, blueprints, etc. The server allows to expose as well a RESTful API.
- Error handling: besides the docs tips, consider if implementing **error codes** (within the event payload between the clients and the server) would be useful.
- SID assignment and management:
  - Currently the client SID does not match the server SID assigned to this client (but communication is not affected). Is it possible to have matching SIDs via cookies/sessions? Do we want/need that?
  - Currently when a client connects to the server, its server SID is used to instantiate any instance of DBWriter and Indexer. The SID can help with the control of the lifecycle of these instances and their greenlets (e.g. execute cleaning tasks when the client disconnects). Be aware that this control logic via SID is a PoC and it must be adapted to the client-server/feature design.
- Namespaces, rooms, broadcasting.

## Indexer

- Exception handling. When an exception happens decide what to do. Also send the error to the SocketIO client.

## DBWriter

- Exception handling. When an exception happens decide what to do. Also send the error to the SocketIO client.

## Milestone 1: history of transactions

### Starting block number

It could be useful to know whether the account creation block can be requested on-chain (we would automatically start indexing at this block number).

### Block number checkpoint

It is required to store block checkpoints (e.g. per DB table and per address, like "used query ranges"). We can't re-start the indexing process just a block after the one from the last extrinsic saved in DB (especially at the beginning of the syncing). It can be addressed in multiple ways (an example below), however prioritise the Indexer speed over a very-accurate checkpoint is a must:

- Heartbeat queue: every N blocks, the Indexer greenlet puts in this queue an object that contains the last block number (plus other data). A specific consumer (or the DBWriter adapted) gets these heartbeats and store them in the DB.

### Extrinsics endpoint

- Implement SELECT and DELETE methods.
- GET history of extrinsics by addresses and timestamp range. Implement it in `SubstrateManager`.
- Define the `SubstrateExtrinsic` serialization method. Consider if there are types of extrinsics that require a particular serialization (e.g. balance transfers).
- Enable logic that allows to delete all the Substrate extrinsics stored in DB.

### Indexer endpoint

- Controls the SocketIO server and client processes. Also the client connection/disconnection. A single client should be enough.
- Controls (e.g. start, stop, resume) the `Indexer` and `DBWriter` instances and greenlets using the client (remember to adapt the SIDs control logic to the requirements).
- Gets the status of an indexer; monitoring capabilities.

## Milestone 2: staking

### Staking-related extrinsics

- Research which Substrate extrinsics (via `call_module`, `call_module_function` and their events) rotki is interested in and how to filter them in. Also define which data of their events has to be processed and stored in the DB.
- Take into account the differences between Kusama and Polkadot!, e.g. modules, block number when the staking events where standardised, eras, etc.
- Define the DB schema (e.g. `substrate_staking_events`) and implement the SELECT/INSERT/DELETE methods.

### Indexer and DBWriter

- The `Indexer` logic has to filter-in the staking-related extrinsics that fulfill the criteria. Try to make the minimum RPC calls as possible (inspect `py-substrate-interface` implementation).
- Two filtering logics/cases will coexist: address extrinsics, staking-related extrinsics. Consider the pros and cons (e.g. performance, control) of these approaches:
  - Both filtering conditions are executed on each block extrinsic. Slower but a simpler implementation and control.
  - Each filtering condition works independently, for instance a greenlet may be running only for transactions, another one for transactions + staking events, etc. Under some circumstances it can be faster (e.g. omitting staking events), but it requires a more complex implementation.
- The strategy chosen above will determine things like new queues by topic, either extending `SubstrateAddressBlockExtrinsicsData` to support events or creating a new structure, how to extend DBWriter, etc.

### Staking events endpoint

Create it.

### Indexer endpoint

Extend it to support the staking events.

# Substrate

## Tools

[py-scale-codec API](https://polkascan.github.io/py-scale-codec/)

[py-substrate-interface API](https://polkascan.github.io/py-substrate-interface/base.html)

[Polkadot.js](https://polkadot.js.org/docs/)

[Substrate JS utilities](https://www.shawntabrizi.com/substrate-js-utilities/)

## Runtime modules

[Kusama runtime modules](https://polkascan.io/kusama/runtime-module)

[Polkadot runtime modules](https://polkascan.io/polkadot/runtime-module)

## Extrinsics & Event

[Source 1](https://substrate.dev/docs/en/knowledgebase/learn-substrate/extrinsics)

[Source 2](https://wiki.polkadot.network/docs/en/build-protocol-info#extrinsics-and-events)

**Uniqueness**

The way to uniquely identify an extrinsic on a Substrate-based chain is to use the **block ID (height or hash) and the extrinsic's index**. The extrinsic hash is not unique.

**Account format**

Substrate extrinsics do not contain addresses in ss58 format (specific per chain), but the **Substrate public key (aka. account ID)**. When it comes to filter/process extrinsics, be aware to do not compare apples and oranges:

- Format an account ID (PK) to a ss58 address.
- Obtain the account ID (PK) from a ss58 address.

Also be aware that the account ID may sometimes start with a leading `0x`.

**Fees**

[Fee calculation](https://wiki.polkadot.network/docs/en/learn-transaction-fees)

The `py-substrate-interface` [ExtrinsicReceipt.total_fee_amount()](https://polkascan.github.io/py-substrate-interface/base.html#substrateinterface.base.ExtrinsicReceipt.total_fee_amount) can calculate the total fee amount (per-byte fee + weight fee + tip) per transaction.

Otherwise it can be manually calculated by processing all the `Balancer.Deposit` and `Treasury.Deposit` events of a particular extrinsic.

**Amounts**

Amounts must be divided by `10 ** native token decimals**.

For instance in Kusama `value: 56754728805` (from a staking payout event) is `0.056754728805` KSM.

### Inherents

**Description**: first extrinsic in a block (index 0), inserted by the block author, not signed, contains timestamp.

**Rotki usage**: obtain the timestamp of the block, store it on each entry of the `substrate_extrinsics` DB table.

**BE AWARE**: an inherent MAY contain a timestamp. Check if a Kusama/Polkadot block could not
have an inherent, making impossible to set the timestamp of a DB substrate extrinsic.

```json
{
  "extrinsic_length": 10,
  "version_info": "04",
  "call_index": "0200",
  "call_function": "set",
  "call_module": "Timestamp",
  "params": [
    { "name": "now", "type": "Compact<Moment>", "value": "2020-10-21T08:56:54" }
  ]
}
```

### Account creation

**Rotki usage**: speed up the indexing process for the imported addresses. Instead of starting to index transactions (except staking ones) at the genesis block, start at the block when the account was created.

**BE AWARE**: if the block when the account was created can't be obtained, allow the user to
introduce it.

**Extrinsic**

```json
{
  "extrinsic_length": 143,
  "version_info": "84",
  "account_length": "ff",
  "account_id": "a6659e4c3f22c2aa97d54a36e31ab57a617af62bd43ec62ed570771492069270",
  "account_index": None,
  "account_idx": None,
  "signature_version": 1,
  "signature": "0cc5e23ac9363c55b7b72bce2705f01719efab99be0cf9eaf314252d94d3340ece1aceb0139f48815aa65364639c67aa556beb7fbc1d133e8e22da782a7cd380",
  "extrinsic_hash": "ee7fee68484f88c981e98ae3c568dbabae6cf46b0f9d689c6c8aa7a58a53e3c6",
  "call_index": "0403",
  "call_function": "transfer_keep_alive",
  "call_module": "Balances",
  "nonce": 446,
  "era": (64, 8),
  "tip": 0,
  "params": [
    {
      "name": "dest",
      "type": "LookupSource",
      "value": "0x203066b0a657bdbdbe9974c20a2644881f384f9b206c7c394054c0d411d7bc6e"
    },
    { "name": "value", "type": "Compact<Balance>", "value": 100000000000 }
  ]
}
```

**Event**

```json
{
  "phase": 0,
  "extrinsic_idx": 1,
  "event_index": "0003",
  "module_id": "System",
  "event_id": "NewAccount",
  "params": [
    {
      "type": "AccountId",
      "value": "0x203066b0a657bdbdbe9974c20a2644881f384f9b206c7c394054c0d411d7bc6e"
    }
  ],
  "topics": [],
  "event_idx": 1
}
```

### Balance Transfer

**Rotki usage**: the amount of native token transferred is in `params`. This location also applies to extrinsics stored in the DB.

**BE AWARE**: DB "balance transfer" extrinsics should be deserialized in a way that include the amount of native token transferred.

**Extrinsic**

```json
{
  "extrinsic_length": 143,
  "version_info": "84",
  "account_length": "ff",
  "account_id": "203066b0a657bdbdbe9974c20a2644881f384f9b206c7c394054c0d411d7bc6e",
  "account_index": None,
  "account_idx": None,
  "signature_version": 1,
  "signature": "2642f99fe03d48cd45ca847f2628f1e9c9f4159d77427af3896ce38d54519930d8a8e2d9ba4a48b50d5eaa8f548e034e8735f790dd8af78722cc5782d4367585",
  "extrinsic_hash": "39dd0b8520d4163ae7640e80fef21d6099e34bd1aec5359c78e3eec2356b5bad",
  "call_index": "0403",
  "call_function": "transfer_keep_alive",
  "call_module": "Balances",
  "nonce": 3,
  "era": (64, 52),
  "tip": 0,
  "params": [
    {
      "name": "dest",
      "type": "LookupSource",
      "value": "0x0cc6c2888a0e296e770c6f7d56db16ec5cb6dfe935ea336d94321ed8ac88cb1a"
    },
    { "name": "value", "type": "Compact<Balance>", "value": 200000000000000 }
  ]
}
```

**Event**

```json
{
  "phase": 0,
  "extrinsic_idx": 1,
  "event_index": "0402",
  "module_id": "Balances",
  "event_id": "Transfer",
  "params": [
    {
      "type": "AccountId",
      "value": "0x203066b0a657bdbdbe9974c20a2644881f384f9b206c7c394054c0d411d7bc6e"
    },
    {
      "type": "AccountId",
      "value": "0x0cc6c2888a0e296e770c6f7d56db16ec5cb6dfe935ea336d94321ed8ac88cb1a"
    },
    { "type": "Balance", "value": 200000000000000 }
  ],
  "topics": [],
  "event_idx": 3
}
```

### Payout Stakers

[Staking](https://wiki.polkadot.network/docs/en/learn-staking)

**Rotki usage**: extending the indexer capabilities to track the staking events of an account adds some
overhead compared to track any extrinsic signed by the account. For instance the staking payouts from a validator to a nominator are not signed by the imported account. It requires to check all the events of a
staking-related extrinsic looking for the imported account, which does extra RPC calls.

**BE AWARE**:

- Payouts may not be the only kind of staking-related transaction rotki wants to track per imported address (e.g. bond, unbond, nominate).

- Filter in staking payouts via the indexer requires to check the events of any staking-related extrinsic per block. It is unclear which `call_function` and `call_module` look at beyond "batch" and "Utility".

- Filter in staking-related extrinsics by the signer account is not a good idea. Staking payouts can be triggered by anyone and an account may have nominated different validators across time.

- Given an account, it is possible to request its current validators. It is unclear whether is possible to request its validators at a **particular era**.

**Extrinsic**

```json
{
  "extrinsic_length": 867,
  "version_info": "84",
  "account_length": 0,
  "account_id": "e8e0a4bce889b5d71d9c9dbcd6687dfda6458cf22bca0a342f5db49d8258ca6a",
  "account_index": None,
  "account_idx": None,
  "signature_version": 1,
  "signature": "ee959303090f4548ef56d24b5108538be6c4a0721d341c7d695695b433a2ee1b7b4c2c432e185dbeff98b103ef69c47a415014f9eb6834ac6bae112c14928188",
  "extrinsic_hash": "9f027f58e6942a4ae3e58d853778ae98164fa001bf33fad2bddbc10f6f907dac",
  "call_index": "1800",
  "call_function": "batch",
  "call_module": "Utility",
  "nonce": 1088,
  "era": (64, 2),
  "tip": 0,
  "params": [
    {
      "name": "calls",
      "type": "Vec<Call>",
      "value": [
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x2e8036bee650826ea445368d4643b0ad2341924bb357c8ee1596fd1235ecf326"
            },
            { "name": "era", "type": "EraIndex", "value": 1978 }
          ],
          "call_hash": "0x370d2e787127656d511b6e35cfc71b65910a20b9bcc04ff822b845a3010d5946"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x54f79360caefa910ba4bc6e26760fbd44a07a9fd4244c6fc7f58b0ce620fe15c"
            },
            { "name": "era", "type": "EraIndex", "value": 1978 }
          ],
          "call_hash": "0x594f0d90e171ae4fdab47f4047adf6c8113ac2dfe4a5ca769746f63089225296"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x54ba6fba820d02b4f0def9908ed99ed988d415d9b7c272a1d9394fd670ea8950"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x547a3bd6479fddf83876d588eb40748b25dee9bffa1bf0425cbecdfcbd18455f"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0xa057612349296f2777068dd47c499f36c5caa498c22b48f26c09b9498ade826f"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0xb740a3f7372bd1513813240099ad165dab1821d9568b58af2754cf9a492779ec"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x9cbecddcf7044de2e7f8aff6dddd8660e28ef5214c59623dd37a62d950da795f"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x8a8b76c5d43874ac0b803fd05804f3147e12ba6436767d7186e30c9d513ee1c3"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x16bc1a5fbe6783b4c4fa8be371150435d5cace22115338b33a9966b4de2ef82d"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x0f91ce983fec404998018430ce0922d8836e5ae0b2df5c512dd7d27f362a2696"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0xca2ecbecab066ed29eb6f04bc145a5fe6ee36cc0144f46a722862cf28dba2c67"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x5e9874cf2d2ae0096b2a5a9c88994d5363eb4d99f9a8b7efc2a94b2040180e74"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0xc20f540f6c1dc4dba60d21936788a5bc5628f26333e27b14cd091145d92d8f25"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0xa9bfa2c2ff88912e6115eb43b4505b050623a6df4da5cf7f83bda9a5d30eb271"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x282272e3e8b07aa02117d8a80fee926dde3a9417a2809b971623dcad89445a3f"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0xdd2c67cbd77b655f937e9d946ad04754445457722a1ee248b3103bd32b7d3053"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x2ad8cd53e45f24d5e7c2cacb60dfc3a4dc6260682150104828e62d3a0142c008"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0xb8ab3edd663bd8291aea65415905355267e124b5a989a117b8776a99ba139299"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x1a7938fede32e1275281b3eee5708706d88444a6dc898a4dec463f1eb298463f"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x3894d58bd272f5f6683c384859118e4603a454487a3e563caddce2c19eb30e1e"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x886a977a6d8063db1b9c58daf3906841f3e2577b07cee9595c5c7e98f7b3aa66"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x644b162369a8b09aa586b6dd23ec19bfa84b1a939747731d5a2d15741e8bcbbd"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0xcaa6c46edcc1d2a38bbfc4e200c3851762178268d7a3e565ab99728c18ae0376"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x15a59eb08a23e2513de989d60a5cdf18e548c5679f5e887de50ec766bc1e1a8f"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x7e437bd1edbcf02649eb8a4103c8668f5903ceffd5b2a4215c0d211affbe5f6c"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x592acc0244ff982cfde01b85e5fc26d23b5e514ed59c50d89cc4d0fe6cf21104"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x68ef8bec8b0e59e7c456392b9f560a48c0abc26faeb75715b1ec4a804f469f10"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x59aa7951d7e942dd391c54d9d8d60a7339c8913d7d995aa3329ce244f810faf0"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x12c7ad0576988c680601696eba2ec9b035e6f99fc5579637089d06a24d3ff650"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x80055e56f651938e8d39d6e49aaf923647f9c7d33d9f86966175fa10a1525f48"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0xacf39cf23dd3530b4078b43141f0a0ce5c19549909b0ce4bc163984e045d9b65"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0xfb8f986957d4aaf12030756ca498a98f4881849cea9efcee4bed83f3f83fc78c"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x0cb5554f54c346c7996d2f6ad6c5bedcdc094b6ce0bb9a4afc7db6ae865cd378"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0xece018427d7fcb70837571fb30b02c781193540e96e70baa8b50b6a93876c4aa"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0x086f2422947fdbebd39a68f8708064bd5d9caab70d1d6a51abff895db91f5655"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x23f76388377efecfd5a7776d58b81ec4d8b59bc7b5815b4fc0ff80d1e156f97a"
        },
        {
          "call_index": "0x0612",
          "call_function": "payout_stakers",
          "call_module": "Staking",
          "call_args": [
            {
              "name": "validator_stash",
              "type": "AccountId",
              "value": "0xd618111c1eb2afe48d95bf0501243748e399e23a538bad0db61fe474f4f2b90f"
            },
            { "name": "era", "type": "EraIndex", "value": 1979 }
          ],
          "call_hash": "0x047001c3c864a45fad0584722cb05225cd6b70be398e72685553d64919321a20"
        }
      ]
    }
  ]
}
```

**Event**

```json
{
  "phase": 0,
  "extrinsic_idx": 1,
  "event_index": "0601",
  "module_id": "Staking",
  "event_id": "Reward",
  "params": [
    {
      "type": "AccountId",
      "value": "0x203066b0a657bdbdbe9974c20a2644881f384f9b206c7c394054c0d411d7bc6e"
    },
    { "type": "Balance", "value": 56754728805 }
  ],
  "topics": [],
  "event_idx": 27
}
```

## Staking Nominate

**Extrinsic**

```json
{
  "extrinsic_length": 634,
  "version_info": "84",
  "account_length": 0,
  "account_id": "203066b0a657bdbdbe9974c20a2644881f384f9b206c7c394054c0d411d7bc6e",
  "account_index": None,
  "account_idx": None,
  "signature_version": 1,
  "signature": "44987b49008a8518a53c118e2ccfd141cf4976996ab5a4dbf10fe54bc1c11648b98b307ba707ab3c83ade721d705039becf896d310d3f9c2cd51d38ad272888a",
  "extrinsic_hash": "ba0e925582f6515652fd2f22b4005600a55fd831f59bd4aa4e28924491519aa8",
  "call_index": "0605",
  "call_function": "nominate",
  "call_module": "Staking",
  "nonce": 6,
  "era": (64, 53),
  "tip": 0,
  "params": [
    {
      "name": "targets",
      "type": "Vec<<Lookup as StaticLookup>::Source>",
      "value": [
        "0xc20f540f6c1dc4dba60d21936788a5bc5628f26333e27b14cd091145d92d8f25",
        "0x086f2422947fdbebd39a68f8708064bd5d9caab70d1d6a51abff895db91f5655",
        "0x9cbecddcf7044de2e7f8aff6dddd8660e28ef5214c59623dd37a62d950da795f",
        "0x3245a2e5ac185dedd4f63f9899990dc2d467a359dd573a7adf0817e06948451d",
        "0x14ededef8a62c8642099e5b094ef95519a5e2a1898ec9783c2bac2c71f71cd16",
        "0x16bc1a5fbe6783b4c4fa8be371150435d5cace22115338b33a9966b4de2ef82d",
        "0xde89b93418a2be5e997eebd4b815754518fc3b7db32b2c31bf97c8297f8ff752",
        "0x0cb5554f54c346c7996d2f6ad6c5bedcdc094b6ce0bb9a4afc7db6ae865cd378",
        "0x282272e3e8b07aa02117d8a80fee926dde3a9417a2809b971623dcad89445a3f",
        "0x5055807b7c54a1143ebefe17d711170a5971227a8a8b593c769fdce803812028",
        "0xbc921beb233fc0c3ebb3f98676aad4aea89f81a08f57251e24e2c02f0dc0a901",
        "0x886a977a6d8063db1b9c58daf3906841f3e2577b07cee9595c5c7e98f7b3aa66",
        "0x2ad8cd53e45f24d5e7c2cacb60dfc3a4dc6260682150104828e62d3a0142c008",
        "0x92f63201ab158aa8283fe585a2bf19ceefb00c45eb7b9eb6063b2ee66a108532",
        "0x2e8036bee650826ea445368d4643b0ad2341924bb357c8ee1596fd1235ecf326",
        "0xca2ecbecab066ed29eb6f04bc145a5fe6ee36cc0144f46a722862cf28dba2c67"
      ]
    }
  ]
}
```

**Event**

```json
{
  "phase": 0,
  "extrinsic_idx": 1,
  "event_index": "0404",
  "module_id": "Balances",
  "event_id": "Deposit",
  "params": [
    {
      "type": "AccountId",
      "value": "0x9a27c1a584abb640ba535eb35d0fff7d986e679b83f322bace608748457b515a"
    },
    { "type": "Balance", "value": 10766662427 }
  ],
  "topics": [],
  "event_idx": 1
}
```
