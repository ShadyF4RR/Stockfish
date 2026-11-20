#!/usr/bin/env python3
"""Verify one packed engine using the system's independent libarchive CAB reader."""
import argparse
import ctypes as C
from ctypes.util import find_library
import hashlib
import json
import os
from pathlib import Path


def verify(path, size, digest):
    libname = find_library("archive")
    if not libname:
        raise RuntimeError("libarchive with CAB support is required")
    lib = C.CDLL(libname)

    def bind(name, result, *args):
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, result
        return fn

    pointer, integer, string = C.c_void_p, C.c_int, C.c_char_p
    new = bind("archive_read_new", pointer)
    enable_cab = bind("archive_read_support_format_cab", integer, pointer)
    open_file = bind("archive_read_open_filename", integer, pointer, string, C.c_size_t)
    next_header = bind("archive_read_next_header", integer, pointer, C.POINTER(pointer))
    get_name = bind("archive_entry_pathname", string, pointer)
    read = bind("archive_read_data", C.c_ssize_t, pointer, pointer, C.c_size_t)
    error = bind("archive_error_string", string, pointer)
    free = bind("archive_read_free", integer, pointer)
    ar = new()
    if not ar:
        raise MemoryError("libarchive allocation failed")

    def require_ok(status):
        if status != 0:
            raise ValueError(f"libarchive returned {status}: {error(ar)!r}")

    try:
        require_ok(enable_cab(ar))
        require_ok(open_file(ar, os.fsencode(path), 65536))
        entry = pointer()
        require_ok(next_header(ar, C.byref(entry)))
        if get_name(entry) != b"stockfish":
            raise ValueError("unexpected CAB member name")
        buf = C.create_string_buffer(65536)
        total, restored = 0, hashlib.sha256()
        while n := read(ar, buf, len(buf)):
            if n < 0:
                require_ok(n)
            total += n
            if total > size:
                raise ValueError("restored member exceeds expected length")
            restored.update(buf.raw[:n])
        if next_header(ar, C.byref(entry)) != 1:  # ARCHIVE_EOF
            raise ValueError("unexpected extra CAB entries or trailer error")
        if total != size or restored.hexdigest() != digest:
            raise ValueError("restored bytes differ from the release executable")
        version = bind("archive_version_string", string)().decode()
        return {"independent_decoder": version, "restored_bytes": total, "restored_sha256": restored.hexdigest()}
    finally:
        free(ar)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cab", type=Path)
    parser.add_argument("expected_bytes", type=int)
    parser.add_argument("expected_sha256")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.cab, args.expected_bytes, args.expected_sha256), indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"verification failed: {exc}\n")
