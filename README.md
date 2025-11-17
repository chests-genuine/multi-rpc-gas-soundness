# README.md

multi-rpc-gas-soundness

Overview
This repository contains a single CLI tool that checks gas fee “soundness” across multiple RPC endpoints for the same EVM chain.

The script connects to several RPC URLs, samples recent base fee values (EIP-1559) from each endpoint, and compares their median base fee to a per-chain group median. Endpoints that deviate too much from the group median are flagged as potential outliers, which can indicate misconfiguration, synchronization problems, or data quality issues.

This is especially useful when you rely on multiple providers or rollup gateways and want a quick sanity check that they all “see” roughly the same gas environment.

Files
- app.py      Main CLI script.
- README.md   This documentation.

Requirements
- Python 3.9 or newer.
- The web3 Python library.

You can install web3 via:

pip install web3

No other third-party dependencies are required.

Concept and model
Many gas-sensitive systems (bridges, relayers, proof systems, rollups) implicitly trust a single RPC endpoint to provide fee information. If that endpoint is misconfigured or lagging behind, your gas-related decisions may be unsound, even if the chain itself is fine.

This tool tries to measure a simple “soundness” signal for gas data by:
1) Connecting to multiple RPC endpoints for the same network.
2) Sampling recent blocks and extracting baseFeePerGas.
3) Computing median base fee per endpoint.
4) Comparing each endpoint’s median to the per-chain group median.
5) Flagging endpoints whose deviation exceeds a configurable tolerance.

It does not prove anything cryptographic; it gives a fast sanity check that your gas fee view is consistent across providers.

Configuration and environment
RPC endpoints

The tool accepts RPC URLs in two ways:

1) Command line flags:
   --rpc https://mainnet.infura.io/v3/KEY --rpc https://eth.llamarpc.com

2) Environment variable:
   RPC_URLS=https://mainnet.infura.io/v3/KEY,https://eth.llamarpc.com

If both are present, CLI flags are used and the environment variable is ignored.

Block sampling configuration

The script samples baseFeePerGas from recent blocks. You can control:
- --blocks        How many recent blocks to consider (default 40).
- --step          Sample every Nth block for speed (default 4).

For example:
- --blocks 40 --step 4 will sample approximately 10 blocks.
- --blocks 120 --step 6 will sample approximately 20 blocks.

Tolerance configuration

You define what counts as “too different” via:
- --tolerance-pct  Deviation threshold in percent (default 30.0).

If an endpoint’s median base fee deviates from the chain-level median by more than this percentage, it is flagged as an outlier.

Timeout configuration

You can control the HTTP timeout per RPC call with:
- --timeout SECONDS  (default 20.0).

Environment defaults

You can also configure defaults via environment variables:
- GAS_SND_BLOCKS          default for --blocks
- GAS_SND_STEP            default for --step
- GAS_SND_TOLERANCE_PCT   default for --tolerance-pct
- GAS_SND_TIMEOUT         default for --timeout

If the corresponding CLI flag is provided, it overrides the environment.

Installation
1) Clone the repo:

   git clone https://github.com/your-user/multi-rpc-gas-soundness.git
   cd multi-rpc-gas-soundness

2) Create a virtual environment (optional but recommended):

   python -m venv .venv
   source .venv/bin/activate   (on Windows: .venv\Scripts\activate)

3) Install dependencies:

   pip install web3

4) Make sure app.py is executable or call it via python directly.

Basic usage examples
Example 1: Two mainnet RPCs, human-readable output

   python app.py \
     --rpc https://mainnet.infura.io/v3/YOUR_KEY \
     --rpc https://eth.llamarpc.com \
     --blocks 60 \
     --step 3 \
