#!/usr/bin/env python3
"""Build an HK-47 CESP sound pack from a local KOTOR 1 install.

Two things make this less trivial than copying files:

  * KOTOR's streamwaves/*.wav are not WAV. They carry a stub RIFF header whose
    data chunk is declared zero-length, immediately followed by raw MP3 frames.
    Playing them directly gives silence. We find the first MP3 sync word and
    hand everything from there to ffmpeg.

  * The filenames are dialogue node IDs, so nothing on disk says what a clip
    says. dialog.tlk carries, per string, a SoundResRef naming the clip. Joining
    the two recovers the exact script text, which beats transcribing 291 clips
    and guessing at the punctuation HK-47 is entirely defined by.

Audio stays local: it is extracted from the game you own, into your own config
dir, and never leaves this machine.
"""

import json
import os
import struct
import subprocess
import sys

KOTOR = os.environ.get(
    "KOTOR_DIR", "~/.steam/steam/steamapps/common/swkotor"
)
PACK_NAME = "hk47"

# Chosen by hand from the 291 HK-47 clips that have both audio and script text.
# Bias is toward short lines: a bark that outlasts the event it announces stops
# being a notification and becomes a podcast.
WIRING = {
    "session.start": [
        "nm35aahhkd07000_",  # HK-47 is ready to serve, master.
        "nm35aahhkd07360_",  # HK-47 exists only to serve, master.
        "nm35aahhkd07428_",  # Simulation initiating.
    ],
    "task.acknowledge": [
        "nm35aahhkd07114_",  # I will endeavour to do so, master.
        "nm35aahhkd07387_",  # If you say so, master.
        "nm35aahhkd07197_",  # It was you who programmed me thus, master.
    ],
    "task.complete": [
        "nglobehhkd07556_",  # Well done, master. You're my kind of owner!
        "nm35aahhkd07183_",  # Now that is the master I remember.
        "nm35aahhkd07408_",  # You are a very harsh master, master. I like you.
        "nm35aahhkd07431_",  # As you desire, master. Signing off.
    ],
    # Kept deliberately short. A permission prompt is the one event you are
    # actually waiting on, so a nine-second monologue is worse than silence.
    "input.required": [
        "nm35aahhkd07426_",  # Are you ready to begin the training sequence, master?
        "nm35aahhkd07212_",  # Yes, master. Of course, master. Could we begin?
        "nm35aahhkd07328_",  # A rather suitable occupation, would you not agree?
    ],
    "task.error": [
        "nglobehhkd07558_",  # Oh, master. I'm so very disappointed in you.
        "nm35aahhkd07266_",  # That hurts, master. This is my life you are talking about.
        "nm35aahhkd07102_",  # I am afraid I cannot comply with your command, master.
    ],
    "resource.limit": [
        "nm35aahhkd07433_",  # I cannot be of assistance on that, master.
        "nm35aahhkd07432_",  # I have little knowledge of that to impart, master.
    ],
    "user.spam": [
        "nm35aahhkd07065_",  # Organics have no sense of persistence.
        "nm35aahhkd07077_",  # Neither are you, master. For an organic meatbag.
        "nm35aahhkd07181_",  # I apologize, master. It is a force of habit.
        "nm35aahhkd07134_",  # I mean... nice human, goo-oood human...
    ],
}


def read_tlk(path):
    """Map SoundResRef -> displayed text. TLK V3.0: 20-byte header, 40-byte entries."""
    raw = open(path, "rb").read()
    if raw[:8] != b"TLK V3.0":
        raise SystemExit(f"{path}: not a TLK V3.0 file")
    _lang, count, str_off = struct.unpack_from("<III", raw, 8)
    out = {}
    for i in range(count):
        base = 20 + i * 40
        resref = raw[base + 4 : base + 20].split(b"\0")[0].decode("ascii", "replace")
        if not resref:
            continue
        off, size = struct.unpack_from("<II", raw, base + 28)
        text = raw[str_off + off : str_off + off + size].decode("cp1252", "replace")
        text = " ".join(text.split())
        if text:
            out[resref.lower()] = text
    return out


def index_streamwaves(root):
    out = {}
    for dirpath, _dirs, names in os.walk(os.path.join(root, "streamwaves")):
        for n in names:
            if n.lower().endswith(".wav"):
                out[n[:-4].lower()] = os.path.join(dirpath, n)
    return out


def mp3_offset(blob):
    """First MP3 frame sync. KOTOR prepends a stub RIFF header with a zero-length
    data chunk; everything before the sync word is that decoy."""
    for i in range(len(blob) - 1):
        if blob[i] == 0xFF and (blob[i + 1] & 0xE0) == 0xE0:
            return i
    return -1


def extract(src, dst):
    blob = open(src, "rb").read()
    off = mp3_offset(blob)
    if off < 0:
        return False
    tmp = dst + ".mp3"
    with open(tmp, "wb") as fh:
        fh.write(blob[off:])
    # -ac 1 -ar 44100: peon-ping plays through pw-play, which is happier with a
    # boring uniform format than with whatever the 2003 encoder felt like.
    rc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp,
         "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", dst],
        check=False,
    ).returncode
    os.unlink(tmp)
    return rc == 0 and os.path.getsize(dst) > 1024


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build(target_packs_dir, tlk, waves):
    import shutil

    out = os.path.join(target_packs_dir, PACK_NAME)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "sounds"))

    categories = {}
    missing = []
    for cat, refs in WIRING.items():
        entries = []
        for ref in refs:
            src = waves.get(ref)
            if not src:
                missing.append(f"{ref} (no audio)")
                continue
            dst = os.path.join(out, "sounds", f"{ref}.wav")
            if not extract(src, dst):
                missing.append(f"{ref} (extract failed)")
                continue
            entries.append(
                {
                    "file": f"sounds/{ref}.wav",
                    "label": tlk.get(ref, ref),
                    "sha256": sha256(dst),
                }
            )
        if entries:
            categories[cat] = {"sounds": entries}

    manifest = {
        "cesp_version": "1.0",
        "name": PACK_NAME,
        "display_name": "HK-47 (KOTOR)",
        "version": "1.0.0",
        "author": {"name": "hk47", "github": "local"},
        # Extracted from a locally owned copy for personal use. Not for redistribution.
        "license": "proprietary-local",
        "language": "en",
        "categories": categories,
    }
    with open(os.path.join(out, "openpeon.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    total = sum(len(c["sounds"]) for c in categories.values())
    print(f"{PACK_NAME}: {total} clips across {len(categories)} categories -> {out}")
    for cat, data in categories.items():
        print(f"  {cat:18} {len(data['sounds'])}")
    if missing:
        print("  MISSING: " + ", ".join(missing), file=sys.stderr)


def main():
    if not os.path.isdir(KOTOR):
        raise SystemExit(f"KOTOR install not found: {KOTOR} (set KOTOR_DIR)")
    tlk = read_tlk(os.path.join(KOTOR, "dialog.tlk"))
    waves = index_streamwaves(KOTOR)
    print(f"tlk strings with audio ref: {len(tlk)}; streamwaves indexed: {len(waves)}")

    home = os.path.expanduser("~")
    for cfg in (".claude-personal", ".claude-work"):
        packs = os.path.join(home, cfg, "hooks", "peon-ping", "packs")
        if os.path.isdir(packs):
            build(packs, tlk, waves)
        else:
            print(f"skip {cfg}: no peon-ping install")


if __name__ == "__main__":
    main()
