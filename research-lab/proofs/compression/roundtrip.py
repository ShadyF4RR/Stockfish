#!/usr/bin/env python3
"""Measure a single deterministic gzip round trip on a bounded regular file."""
import argparse
import contextlib
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import struct
import tempfile
import time
import zlib


def artifact_kind(header, name, size, digest):
    if header.startswith(b"\x7fELF") and len(header) >= 64:
        # ET_EXEC, or ET_DYN with a loader (PIE). Shared objects are ambiguous.
        if header[4] in (1, 2) and header[5] in (1, 2):
            order = "<" if header[5] == 1 else ">"
            is_64 = header[4] == 2
            elf_type = struct.unpack_from(order + "H", header, 16)[0]
            phoff = struct.unpack_from(order + ("Q" if is_64 else "I"), header, 32 if is_64 else 28)[0]
            entsize, count = struct.unpack_from(order + "HH", header, 54 if is_64 else 42)
            if elf_type == 2:
                return "ELF executable"
            if elf_type == 3 and entsize >= 4 and phoff + entsize * count <= len(header):
                if any(struct.unpack_from(order + "I", header, phoff + entsize * i)[0] == 3
                       for i in range(count)):
                    return "ELF executable"
    net = re.fullmatch(r"nn-([a-f0-9]{12})\.nnue", name)
    if net and digest.startswith(net.group(1)) and len(header) >= 12:
        version, _, description_size = struct.unpack_from("<III", header)
        if version == 0x6A448AFA and description_size < size - 12:
            return "Stockfish NNUE network"
    for signature, fmt in ((b"\x1f\x8b\x08", "gzip"), (b"PK\x03\x04", "ZIP"),
                           (b"\xfd7zXZ\x00", "XZ"), (b"\x28\xb5\x2f\xfd", "Zstandard")):
        if header.startswith(signature):
            return fmt + " container"
    if header[257:263] in (b"ustar\0", b"ustar "):
        return "tar container"
    return "unknown"


def transfer(source, destination, limit):
    digest = hashlib.sha256()
    size = 0
    while data := source.read(1024 * 1024):
        size += len(data)
        if size > limit:
            raise ValueError("stream exceeded its byte limit")
        if destination is not None:
            destination.write(data)
        digest.update(data)
    return size, digest.hexdigest()


def measured(operation):
    start, cpu = time.perf_counter(), time.process_time()
    result = operation()
    return result, {"wall_seconds": time.perf_counter() - start,
                    "process_cpu_seconds": time.process_time() - cpu}


def measure(path, directory, level, limit):
    packed, restored = directory / "packed.gz", directory / "restored.bin"
    with open(path, "rb", opener=open_regular) as source:
        initial = os.fstat(source.fileno())
        if initial.st_size > limit:
            raise ValueError("input exceeds configured byte limit")
        header = source.read(65536)
        source.seek(0)

        def compress():
            with packed.open("xb") as disk:
                with gzip.GzipFile(filename="", mode="wb", fileobj=disk, mtime=0, compresslevel=level) as dest:
                    return transfer(source, dest, limit)

        (size, digest), compression = measured(compress)
        final = os.fstat(source.fileno())
        if (size != initial.st_size or final.st_size != size or final.st_mtime_ns != initial.st_mtime_ns):
            raise ValueError("input changed during measurement")

    def decompress():
        with gzip.open(packed, "rb") as source, restored.open("xb") as dest:
            transfer(source, dest, size)

    _, decompression = measured(decompress)
    with restored.open("rb") as source:
        restored_size, restored_hash = transfer(source, None, size)
    if (size, digest) != (restored_size, restored_hash):
        raise ValueError("restored bytes differ")
    with packed.open("rb") as source:
        packed_size, packed_hash = transfer(source, None, packed.stat().st_size)
    return {
        "input_name": path.name, "kind": artifact_kind(header, path.name, size, digest),
        "input_bytes": size, "input_sha256": digest,
        "gzip_bytes": packed_size, "gzip_sha256": packed_hash,
        "restored_bytes": restored_size, "restored_sha256": restored_hash,
        "gzip_to_input_ratio": packed_size / size if size else None,
        "round_trip_verified": True, "compression": compression, "decompression": decompression,
        "gzip_level": level, "max_input_bytes": limit,
        "python_version": platform.python_version(), "zlib_version": zlib.ZLIB_RUNTIME_VERSION,
        "machine": platform.machine(), "kernel": platform.release(),
    }


def open_regular(name, flags):
    fd = os.open(name, flags | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("input must be a regular file")
    return fd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--level", type=int, choices=range(1, 10), default=1)
    parser.add_argument("--max-mib", type=int, default=256)
    parser.add_argument("--keep-dir", type=Path, help="create a new directory for packed.gz and restored.bin")
    args = parser.parse_args()
    if not 1 <= args.max_mib <= 512:
        parser.error("max-mib must be between 1 and 512")
    try:
        with contextlib.ExitStack() as stack:
            if args.keep_dir:
                args.keep_dir.mkdir(mode=0o700)
                directory = args.keep_dir
            else:
                directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            print(json.dumps(measure(args.file, directory, args.level, args.max_mib * 1024**2), indent=2))
    except (OSError, ValueError, EOFError) as exc:
        parser.exit(1, f"round trip failed: {exc}\n")


if __name__ == "__main__":
    main()
