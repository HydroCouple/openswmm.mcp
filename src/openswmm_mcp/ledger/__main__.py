"""Command-line tools for ledger disclosures and tally roots (``python -m openswmm_mcp.ledger``)."""

from __future__ import annotations

import argparse
import json
import sys

from openswmm_mcp.ledger import merkle


def _cmd_verify_proof(args: argparse.Namespace) -> int:
    try:
        with open(args.proof, encoding="utf-8") as fh:
            disclosure = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"FAIL: cannot read proof: {exc}")
        return 1

    # Root and count come only from the command line, never from the file.
    if not merkle.verify_disclosure(disclosure, args.root, args.count):
        print("FAIL")
        return 1

    fact = disclosure["fact"]  # verified: all fields below are bound by the leaf
    print(f"OK {merkle.fact_id_of(fact)} = {fact['value']} {fact['unit']}".rstrip())
    return 0


def _cmd_root(args: argparse.Namespace) -> int:
    try:
        with open(args.tally, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("tally JSON must be an object")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    fact_count = data["fact_count"] if "fact_count" in data else len(data["facts"])
    meta = data.get("meta")
    if isinstance(meta, dict):
        meta = {k: v for k, v in meta.items() if k != "salt"}

    out = {
        "tally_id": data.get("tally_id"),
        "root": data.get("root"),
        "fact_count": fact_count,
        "meta": meta,
        "schema": data.get("schema"),
    }
    print(json.dumps(out, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openswmm_mcp.ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    vp = sub.add_parser("verify-proof", help="verify a fact disclosure against a public root")
    vp.add_argument("--root", required=True, help="expected root digest (hex)")
    vp.add_argument("--count", type=int, required=True, help="expected fact count")
    vp.add_argument("--proof", required=True, help="path to a disclosure JSON file")
    vp.set_defaults(func=_cmd_verify_proof)

    rp = sub.add_parser("root", help="print the public fields of a .tally.json file")
    rp.add_argument("tally", help="path to a .tally.json file")
    rp.set_defaults(func=_cmd_root)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
