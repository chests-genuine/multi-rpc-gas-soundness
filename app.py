# app.py
import os
import sys
import time
import argparse
import json
from typing import List, Dict, Any, Tuple
from statistics import median
from web3 import Web3

DEFAULT_BLOCKS = int(os.getenv("GAS_SND_BLOCKS", "40"))
DEFAULT_STEP = int(os.getenv("GAS_SND_STEP", "4"))
DEFAULT_TOLERANCE_PCT = float(os.getenv("GAS_SND_TOLERANCE_PCT", "30.0"))
DEFAULT_TIMEOUT = float(os.getenv("GAS_SND_TIMEOUT", "20.0"))

NETWORKS = {
    1: "Ethereum Mainnet",
    11155111: "Sepolia Testnet",
    10: "Optimism",
    137: "Polygon",
    42161: "Arbitrum One",
    8453: "Base",
}


def network_name(chain_id: int) -> str:
    return NETWORKS.get(chain_id, f"Unknown (chainId {chain_id})")


def utc_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def connect(rpc: str, timeout: float) -> Web3:
    t0 = time.time()
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": timeout}))
    if not w3.is_connected():
        print(f"❌ Failed to connect to RPC: {rpc}", file=sys.stderr)
        sys.exit(1)
    try:
        cid = int(w3.eth.chain_id)
        head = int(w3.eth.block_number)
        client = getattr(w3, "clientVersion", lambda: "unknown")()
        dt = (time.time() - t0) * 1000
        print(
            f"🌐 Connected to {network_name(cid)} (chainId {cid}, tip {head}) via {client} in {dt:.0f} ms",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"⚠️ Connected but failed to read chain info: {e}", file=sys.stderr)
    return w3


def pct_diff(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def sample_base_fees(w3: Web3, blocks: int, step: int) -> Tuple[List[float], int, int, int]:
    head = int(w3.eth.block_number)
    start = max(0, head - blocks + 1)
    base_fees: List[float] = []
    sampled = 0

    print(
        f"🔍 Sampling base fees from blocks [{start}, {head}] every {step} block(s)…",
        file=sys.stderr,
    )

    for n in range(head, start - 1, -step):
        try:
            blk = w3.eth.get_block(n)
        except Exception as e:
            print(f"⚠️ Failed to fetch block {n}: {e}", file=sys.stderr)
            continue
        bf_wei = int(blk.get("baseFeePerGas", 0) or 0)
        if bf_wei == 0:
            # some L2 / legacy networks may not have baseFeePerGas
            continue
        base_fees.append(float(Web3.from_wei(bf_wei, "gwei")))
        sampled += 1

    return base_fees, head, start, sampled


def analyze_endpoint(rpc: str, blocks: int, step: int, timeout: float) -> Dict[str, Any]:
    w3 = connect(rpc, timeout=timeout)
    chain_id = int(w3.eth.chain_id)
    client_version = getattr(w3, "clientVersion", lambda: "unknown")()
    base_fees, head, start, sampled = sample_base_fees(w3, blocks, step)

    if sampled == 0:
        print(
            f"⚠️ No baseFeePerGas data found on {rpc} in requested range; "
            f"this network or RPC may not support EIP-1559.",
            file=sys.stderr,
        )

    head_blk = None
    head_bf_gwei = None
    try:
        head_blk = w3.eth.get_block(head)
        head_bf_gwei = float(
            Web3.from_wei(int(head_blk.get("baseFeePerGas", 0) or 0), "gwei")
        )
    except Exception:
        pass

    if base_fees:
        med_bf = median(base_fees)
        min_bf = min(base_fees)
        max_bf = max(base_fees)
    else:
        med_bf = min_bf = max_bf = 0.0

    return {
        "rpcUrl": rpc,
        "chainId": chain_id,
        "network": network_name(chain_id),
        "clientVersion": client_version,
        "head": head,
        "start": start,
        "requestedSpan": blocks,
        "step": step,
        "sampledBlocks": sampled,
        "baseFeeMedianGwei": round(med_bf, 3),
        "baseFeeMinGwei": round(min_bf, 3),
        "baseFeeMaxGwei": round(max_bf, 3),
        "headBaseFeeGwei": round(head_bf_gwei, 3) if head_bf_gwei is not None else None,
    }


def group_by_chain(endpoints: List[Dict[str, Any]], tolerance_pct: float) -> Dict[str, Any]:
    groups: Dict[int, Dict[str, Any]] = {}
    for ep in endpoints:
        cid = ep["chainId"]
        groups.setdefault(cid, {"endpoints": [], "globalMedianBaseFeeGwei": 0.0})
        groups[cid]["endpoints"].append(ep)

    # compute group medians and deviations
    for cid, grp in groups.items():
        med_values = [ep["baseFeeMedianGwei"] for ep in grp["endpoints"] if ep["baseFeeMedianGwei"] > 0]
        if med_values:
            g_med = median(med_values)
        else:
            g_med = 0.0
        grp["globalMedianBaseFeeGwei"] = round(g_med, 3)
        for ep in grp["endpoints"]:
            if g_med > 0 and ep["baseFeeMedianGwei"] > 0:
                dev = pct_diff(ep["baseFeeMedianGwei"], g_med)
            else:
                dev = 0.0
            ep["deviationPct"] = round(dev, 2)
            ep["isOutlier"] = abs(dev) >= tolerance_pct
    return groups


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Multi-RPC gas soundness checker for EVM networks.\n"
            "Connects to multiple RPC endpoints for the same chain, samples recent base fees, "
            "and detects outlier endpoints whose fee view deviates from the group median."
        )
    )
    p.add_argument(
        "--rpc",
        action="append",
        help="RPC URL (can be used multiple times). If omitted, uses RPC_URLS env (comma-separated).",
    )
    p.add_argument(
        "--blocks",
        type=int,
        default=DEFAULT_BLOCKS,
        help=f"How many recent blocks to consider (default {DEFAULT_BLOCKS}).",
    )
    p.add_argument(
        "--step",
        type=int,
        default=DEFAULT_STEP,
        help=f"Sample every Nth block for speed (default {DEFAULT_STEP}).",
    )
    p.add_argument(
        "--tolerance-pct",
        type=float,
        default=DEFAULT_TOLERANCE_PCT,
        help=(
            f"Flag RPCs whose median base fee deviates by this percent or more "
            f"from the per-chain group median (default {DEFAULT_TOLERANCE_PCT}%%)."
        ),
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP RPC timeout in seconds (default {DEFAULT_TIMEOUT}).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON report instead of human-readable text.",
    )
    return p.parse_args()


def load_rpcs_from_env() -> List[str]:
    env_val = os.getenv("RPC_URLS", "").strip()
    if not env_val:
        return []
    return [chunk.strip() for chunk in env_val.split(",") if chunk.strip()]


def main() -> None:
    args = parse_args()

    if args.blocks <= 0 or args.step <= 0:
        print("❌ --blocks and --step must be > 0.", file=sys.stderr)
        sys.exit(1)

    rpc_list: List[str] = []
    if args.rpc:
        rpc_list.extend(args.rpc)
    else:
        rpc_list.extend(load_rpcs_from_env())

    rpc_list = [r.strip() for r in rpc_list if r and r.strip()]

    if not rpc_list:
        print(
            "❌ No RPC endpoints provided. Use --rpc or set RPC_URLS env (comma-separated).",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"🧪 Starting multi-RPC gas soundness check at {utc_now()} UTC.",
        file=sys.stderr,
    )
    print(
        f"   RPC endpoints: {len(rpc_list)}  |  blocks={args.blocks} step={args.step} "
        f"tolerance={args.tolerance_pct:.1f}%",
        file=sys.stderr,
    )

    t0 = time.time()
    endpoints: List[Dict[str, Any]] = []
    for rpc in rpc_list:
        try:
            ep_summary = analyze_endpoint(rpc, args.blocks, args.step, args.timeout)
            endpoints.append(ep_summary)
        except KeyboardInterrupt:
            print("\n🛑 Aborted by user.", file=sys.stderr)
            sys.exit(1)
        except SystemExit:
            raise
        except Exception as e:
            print(f"⚠️ Failed to analyze RPC {rpc}: {e}", file=sys.stderr)

    if not endpoints:
        print("❌ No endpoints could be analyzed successfully.", file=sys.stderr)
        sys.exit(2)

    groups = group_by_chain(endpoints, args.tolerance_pct)
    elapsed = round(time.time() - t0, 2)

    if args.json:
        payload = {
            "mode": "multi_rpc_gas_soundness",
            "generatedAtUtc": utc_now(),
            "timingSec": elapsed,
            "params": {
                "blocks": args.blocks,
                "step": args.step,
                "tolerancePct": args.tolerance_pct,
                "timeoutSec": args.timeout,
            },
            "groups": {
                str(cid): {
                    "chainId": cid,
                    "network": grp["endpoints"][0]["network"]
                    if grp["endpoints"]
                    else network_name(cid),
                    "globalMedianBaseFeeGwei": grp["globalMedianBaseFeeGwei"],
                    "endpoints": grp["endpoints"],
                }
                for cid, grp in groups.items()
            },
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    # Human-readable output
    print("")
    print(f"✅ Multi-RPC gas soundness report (completed in {elapsed:.2f}s)")
    for cid, grp in groups.items():
        cid_int = int(cid)
        eps = grp["endpoints"]
        if not eps:
            continue
        print("")
        print(f"=== {network_name(cid_int)} (chainId {cid_int}) ===")
        print(f"Global median base fee: {grp['globalMedianBaseFeeGwei']:.3f} Gwei")
        for ep in eps:
            flag = "🚨" if ep["isOutlier"] else "✅"
            dev = ep["deviationPct"]
            bf_med = ep["baseFeeMedianGwei"]
            head_bf = ep["headBaseFeeGwei"]
            print(
                f"{flag} RPC: {ep['rpcUrl']}\n"
                f"   client: {ep['clientVersion']}\n"
                f"   sampledBlocks: {ep['sampledBlocks']} "
                f"(range {ep['start']}..{ep['head']} step={ep['step']})\n"
                f"   median baseFee: {bf_med:.3f} Gwei "
                f"(min={ep['baseFeeMinGwei']:.3f}, max={ep['baseFeeMaxGwei']:.3f})\n"
                f"   head baseFee: {head_bf:.3f} Gwei\n"
                f"   deviation from group median: {dev:+.2f}%"
            )

    print("")
    print("Legend:")
    print("  ✅ RPC within tolerance band.")
    print("  🚨 RPC deviates from group median by >= tolerance (possible gas view inconsistency).")
    print("")
    print("Note: significant, systematic deviations between RPC endpoints on the same chain")
    print("may indicate node misconfiguration, different EIP-1559 behavior, or data soundness issues.")
    print(f"\n🕒 Finished at {utc_now()} UTC.")


if __name__ == "__main__":
    main()
