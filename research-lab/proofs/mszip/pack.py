#!/usr/bin/env python3
"""Build one deterministic, single-folder, single-file MSZIP Cabinet."""
import argparse
from array import array
from functools import reduce
import hashlib
import json
from operator import xor
from pathlib import Path
import struct
import sys
import zlib

BLOCK = 32768
LIMIT = 512 * 1024 * 1024
HEADER = struct.Struct("<4sIIIIIBBHHHHH")
FOLDER = struct.Struct("<IHH")
FILE = struct.Struct("<IIHHHH")
DATA = struct.Struct("<IHH")


def cab_checksum(payload, size):
    # [MS-CAB] Cabinet Checksum Algorithm. The last partial word has the
    # opposite byte order to the full words. The CFDATA size fields form a word.
    whole = len(payload) & ~3
    words = array("I")
    words.frombytes(payload[:whole])
    if sys.byteorder == "big":
        words.byteswap()
    return reduce(xor, words, len(payload) | (size << 16)) ^ int.from_bytes(payload[whole:], "big")


def pack(source_path, output_path, level, mem_level):
    name = b"stockfish\0"
    files_offset = HEADER.size + FOLDER.size
    data_offset = files_offset + FILE.size + len(name)
    size = source_path.stat().st_size
    if not source_path.is_file() or not 0 < size <= LIMIT:
        raise ValueError("input must be a nonempty regular file of at most 512 MiB")
    blocks = (size + BLOCK - 1) // BLOCK
    original_hash = hashlib.sha256()
    with source_path.open("rb") as source, output_path.open("xb+") as out:
        out.write(bytes(HEADER.size))
        out.write(FOLDER.pack(data_offset, blocks, 1))  # typeCompress = MSZIP
        out.write(FILE.pack(size, 0, 0, 0x0021, 0, 0x20))  # Jan 1, 1980, archive attribute
        out.write(name)
        previous = b""
        for i in range(blocks):
            count = min(BLOCK, size - i * BLOCK)
            raw = source.read(count)
            if len(raw) != count:
                raise ValueError("input truncated during compression")
            original_hash.update(raw)
            # Each block is an independent raw DEFLATE stream. Its initial
            # dictionary is the previous uncompressed 32 KiB in the folder.
            stream = zlib.compressobj(level, zlib.DEFLATED, -15, memLevel=mem_level, zdict=previous)
            payload = b"CK" + stream.compress(raw) + stream.flush(zlib.Z_FINISH)
            out.write(DATA.pack(cab_checksum(payload, count), len(payload), count))
            out.write(payload)
            previous = raw
        if source.read(1):
            raise ValueError("input grew during compression")
        cabinet_size = out.tell()
        out.seek(0)
        out.write(HEADER.pack(b"MSCF", 0, cabinet_size, 0, files_offset, 0, 3, 1, 1, 1, 0, 0, 0))
        out.seek(0)
        cabinet_hash = hashlib.sha256()
        while block := out.read(1024 * 1024):
            cabinet_hash.update(block)
    return {"input_bytes": size, "input_sha256": original_hash.hexdigest(),
            "cab_bytes": cabinet_size, "cab_sha256": cabinet_hash.hexdigest(),
            "blocks": blocks, "block_bytes": BLOCK, "codec": "MSZIP",
            "zlib_version": zlib.ZLIB_RUNTIME_VERSION, "level": level, "mem_level": mem_level}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("new_cab", type=Path)
    parser.add_argument("--level", type=int, choices=range(1, 10), default=1)
    parser.add_argument("--mem-level", type=int, choices=range(1, 10), default=4)
    args = parser.parse_args()
    try:
        print(json.dumps(pack(args.input, args.new_cab, args.level, args.mem_level), indent=2))
    except (OSError, ValueError, zlib.error) as exc:
        parser.exit(1, f"packing failed: {exc}\n")
