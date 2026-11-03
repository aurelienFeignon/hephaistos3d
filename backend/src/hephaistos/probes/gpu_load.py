"""Charge GPU simulée pour G0.10 : un autre processus qui occupe de la VRAM.

Alloue progressivement N Gio par blocs, comme le ferait un entraînement en croissance mémoire, les
garde T secondes, puis rend tout. Rapporte toute allocation refusée (OOM) : le plafond d'Hephaistos
doit laisser à ce processus ce qu'il demande.

Usage : python -m hephaistos.probes.gpu_load --gib 6 --ramp-s 10 --hold-s 60
Code de sortie : 0 toutes les allocations réussies, 2 au moins un OOM.
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gib", type=float, required=True)
    parser.add_argument("--block-mib", type=int, default=256)
    parser.add_argument("--ramp-s", type=float, default=10.0)
    parser.add_argument("--hold-s", type=float, default=60.0)
    args = parser.parse_args(argv)

    import torch

    blocks = max(1, int(args.gib * 1024 / args.block_mib))
    pause = args.ramp_s / blocks
    held = []
    ooms = 0
    start = time.time()
    for index in range(blocks):
        try:
            held.append(torch.empty(args.block_mib * 1024 * 1024, dtype=torch.uint8, device="cuda"))
        except torch.cuda.OutOfMemoryError:
            ooms += 1
            print(json.dumps({"event": "oom", "block": index, "t": time.time() - start}), flush=True)
        time.sleep(pause)
    print(json.dumps({"event": "held", "mib": len(held) * args.block_mib, "ooms": ooms}), flush=True)
    time.sleep(args.hold_s)
    return 2 if ooms else 0


if __name__ == "__main__":
    sys.exit(main())
