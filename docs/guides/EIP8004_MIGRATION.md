# EIP-8004 Migration Guide

## What Changed

All `agent_id` parameters and fields have been renamed to `agent_did` to prepare for EIP-8004 integration.

### Modified Files

**Core SDK (Open-source Package):**
- `packages/x402-secure/src/x402_secure_client/risk.py` - RiskClient.create_session()
- `packages/x402-secure/src/x402_secure_client/agent.py` - agent execution flow
- `packages/x402-secure/src/x402_secure_client/buyer.py` - BuyerClient with trace generation

**Backend (Private):**
- `risk_engine/ap2/types.py` - SessionRequest, SessionData models
- `risk_engine/ap2/session.py` - SessionStore
- `risk_engine/server.py` - RiskSessionRequest and API endpoints
- `sdk/secure_x402/src/secure_x402/risk_public.py` - public risk API (local proxy mode)

**Examples & Tests:**
- `packages/x402-secure/examples/buyer_agent_openai.py`
- `packages/x402-secure/examples/buyer_basic.py`
- `packages/x402-secure/examples/README.md`
- `scripts/test_ap2_flow.py`
- `tests/test_proxy_risk_public.py`

## Current Behavior

The system currently accepts wallet addresses (e.g., `0x1234...5678`) as `agent_did` values. This maintains backward compatibility while preparing for future enhancements.

## What is EIP-8004?

EIP-8004 is an Ethereum standard (finalized October 2025) that enables decentralized AI agent identity management through:

1. **Portable On-chain Identity**: Each agent is assigned an ERC-721 token (NFT) as their identity
2. **Decentralized Identifiers (DIDs)**: Uses standard DID format instead of simple addresses
3. **Three On-chain Registries**:
   - Identity Registry
   - Reputation Registry
   - Verification Registry

## EIP-8004 DID Format

```
did:eip8004:{chain_id}:{contract_address}:{token_id}
```

**Example:**
```
did:eip8004:8453:0x1234567890123456789012345678901234567890:123
```

Where:
- `did:eip8004:` - DID method prefix
- `8453` - Chain ID (e.g., Base mainnet)
- `0x123...` - ERC-721 contract address (agent identity registry)
- `123` - Token ID of the agent NFT

## Future Integration Steps

### 1. Add DID Validation

Add format validation in `risk_engine/ap2/types.py`:

```python
from pydantic import validator

class SessionRequest(BaseModel):
    trace_context: Dict[str, Any]
    agent_did: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @validator('agent_did')
    def validate_agent_did(cls, v):
        if not v:
            return v

        # Support EIP-8004 DID format
        if v.startswith('did:eip8004:'):
            parts = v.split(':')
            if len(parts) < 5:
                raise ValueError(f"Invalid EIP-8004 DID format: {v}")
            chain_id = parts[2]
            contract = parts[3]
            token_id = parts[4]
            # Validate format
            if not contract.startswith('0x') or len(contract) != 42:
                raise ValueError(f"Invalid contract address in DID: {contract}")
            return v

        # Support legacy wallet address
        elif v.startswith('0x') and len(v) == 42:
            return v

        else:
            raise ValueError(f"Invalid agent_did: must be EIP-8004 DID or Ethereum address")
```

### 2. Add On-chain Identity Resolution

Create `risk_engine/eip8004/resolver.py`:

```python
from web3 import Web3
from typing import Optional, Dict, Any

class EIP8004Resolver:
    """Resolve EIP-8004 DIDs to on-chain agent identity."""

    def __init__(self, web3_provider: str):
        self.w3 = Web3(Web3.HTTPProvider(web3_provider))

    def resolve_did(self, did: str) -> Optional[Dict[str, Any]]:
        """
        Resolve EIP-8004 DID to agent identity metadata.

        Args:
            did: EIP-8004 DID (did:eip8004:chain:contract:tokenId)

        Returns:
            Dict with owner, metadata, reputation score, etc.
        """
        if not did.startswith('did:eip8004:'):
            return None

        parts = did.split(':')
        chain_id = parts[2]
        contract = parts[3]
        token_id = int(parts[4])

        # Load ERC-721 contract
        # TODO: Use actual EIP-8004 identity registry ABI
        contract_abi = [...] # ERC-721 + EIP-8004 extensions

        agent_nft = self.w3.eth.contract(
            address=Web3.to_checksum_address(contract),
            abi=contract_abi
        )

        # Get owner
        owner = agent_nft.functions.ownerOf(token_id).call()

        # Get metadata URI
        metadata_uri = agent_nft.functions.tokenURI(token_id).call()

        # Fetch metadata (IPFS or HTTP)
        # metadata = fetch_metadata(metadata_uri)

        return {
            'did': did,
            'owner': owner,
            'contract': contract,
            'token_id': token_id,
            'chain_id': chain_id,
            # 'metadata': metadata,
            # 'reputation_score': ...,
        }
```

### 3. Integrate with Session Creation

Update `risk_engine/server.py`:

```python
from .eip8004.resolver import EIP8004Resolver

# Initialize resolver
eip8004_resolver = EIP8004Resolver(
    web3_provider=os.getenv("WEB3_PROVIDER_URL")
)

@app.post("/risk/session", tags=["Risk"])
async def risk_create_session(req: RiskSessionRequest):
    if not req.agent_did:
        raise HTTPException(status_code=400, detail="agent_did required")

    # Resolve EIP-8004 DID if provided
    agent_identity = None
    if req.agent_did.startswith('did:eip8004:'):
        agent_identity = eip8004_resolver.resolve_did(req.agent_did)
        if not agent_identity:
            raise HTTPException(status_code=400, detail="Invalid EIP-8004 DID")

        logger.info(f"Resolved EIP-8004 identity: {agent_identity['owner']}")

    # ... rest of session creation
```

### 4. Add Reputation Integration

Query on-chain reputation registry for risk scoring:

```python
def get_agent_reputation(did: str) -> float:
    """Query EIP-8004 reputation registry for agent score."""
    # TODO: Implement based on EIP-8004 reputation contract
    pass
```

### 5. Environment Variables

Add to `.env`:

```bash
# EIP-8004 Integration
WEB3_PROVIDER_URL=https://mainnet.base.org
EIP8004_IDENTITY_REGISTRY=0x... # ERC-721 contract address
EIP8004_REPUTATION_REGISTRY=0x...
EIP8004_VERIFICATION_REGISTRY=0x...
```

## Migration for Users

Users can continue using wallet addresses as before:

```python
# Current usage (still works)
sid = await rc.create_session(
    agent_did="0x1234567890123456789012345678901234567890",
    app_id=None,
    device={"ua": "my-app"}
)
```

Once EIP-8004 is integrated:

```python
# Future usage with EIP-8004
sid = await rc.create_session(
    agent_did="did:eip8004:8453:0xRegistryContract:123",
    app_id=None,
    device={"ua": "my-app"}
)
```

## Benefits of EIP-8004 Integration

1. **Portable Identity**: Agent identity persists across platforms
2. **On-chain Reputation**: Verifiable track record of agent behavior
3. **Ownership Transfer**: Agents can be traded/transferred as NFTs
4. **Decentralized Verification**: No central authority required
5. **Interoperability**: Compatible with existing ERC-721 infrastructure

## References

- EIP-8004 Specification: (October 2025)
- On-chain registries: Identity, Reputation, Verification
- Based on ERC-721 standard for agent identity tokens

## TODO Comments in Code

Search for `TODO.*EIP-8004` or `TODO.*eip8004` in the codebase to find all locations marked for future enhancement.
