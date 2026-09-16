"""Two repairs to cosyvoice/utils/train_utils.py, both idempotent.

1. num_workers 0 must be a usable setting. torch raises ValueError if
   prefetch_factor is passed at all while num_workers is 0, and CosyVoice passes
   args.prefetch unconditionally. Workers are not optional here for a practical
   reason: this container has Docker's default 64MB /dev/shm, and two workers at
   prefetch 100 die with a Bus error on the first batch. Raising shm means
   recreating the container, which would discard every runtime pip install this
   fine-tune needed.

2. cosyvoice_join reads group_join.options._timeout, and torch 2.8's ProcessGroup
   no longer has .options. The barrier exists to keep ranks from drifting apart on
   learning rate; with WORLD_SIZE 1 there is no second rank to drift from, so
   returning False before the barrier is what the function already means rather
   than a way around it. Multi-rank runs still take the original path and will
   still hit the torch incompatibility, which is upstream's to fix.

Keeps a .orig beside the file, as the other repairs in this engine do.
"""

import shutil
import sys

PATH = "/root/.omnivoice/engines/cosyvoice/CosyVoice/cosyvoice/utils/train_utils.py"

DATALOADER_ANCHOR = (
    "    # do not use persistent_workers=True, as whisper tokenizer opens "
    "tiktoken file each time when the for loop starts\n"
    "    train_data_loader = DataLoader(train_dataset,"
)

DATALOADER_GUARD = (
    "    # torch refuses prefetch_factor unless num_workers > 0, and this\n"
    "    # container's /dev/shm is 64MB, so num_workers 0 must be usable.\n"
    "    prefetch_factor = args.prefetch if args.num_workers > 0 else None\n"
    "\n"
) + DATALOADER_ANCHOR

JOIN_ANCHOR = (
    "    rank = int(os.environ.get('RANK', 0))\n"
    "\n"
    "    if info_dict[\"batch_idx\"] != 0:"
)

JOIN_GUARD = (
    "    rank = int(os.environ.get('RANK', 0))\n"
    "\n"
    "    # Nothing to synchronise on a single rank, and torch 2.8's ProcessGroup\n"
    "    # has no .options for the barrier below to read its timeout from.\n"
    "    if world_size == 1:\n"
    "        return False\n"
    "\n"
    "    if info_dict[\"batch_idx\"] != 0:"
)


def apply(src, name, marker, anchor, guard):
    if marker in src:
        print(f"{name}: already patched")
        return src, False
    if anchor not in src:
        print(f"{name}: anchor not found, refusing to guess", file=sys.stderr)
        raise SystemExit(1)
    print(f"{name}: patched")
    return src.replace(anchor, guard), True


def main():
    src = open(PATH).read()
    original = src

    src, _ = apply(
        src,
        "dataloader",
        "prefetch_factor = args.prefetch if args.num_workers > 0",
        DATALOADER_ANCHOR,
        DATALOADER_GUARD,
    )
    src = src.replace("prefetch_factor=args.prefetch)", "prefetch_factor=prefetch_factor)")

    src, _ = apply(
        src,
        "cosyvoice_join",
        "if world_size == 1:\n        return False",
        JOIN_ANCHOR,
        JOIN_GUARD,
    )

    if src == original:
        print("nothing to do")
        return 0

    shutil.copyfile(PATH, PATH + ".orig")
    open(PATH, "w").write(src)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
