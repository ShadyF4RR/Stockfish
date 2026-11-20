// This launcher reads one CAB v1.3 folder containing one regular MSZIP member.
package main

import (
	"compress/flate"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

const maxPlain = 512 << 20
const blockSize = 32768

//go:embed payload.cab
var archive string

//go:embed payload.sha256
var expected string

func word(s string, i int) uint16  { return uint16(s[i]) | uint16(s[i+1])<<8 }
func dword(s string, i int) uint32 { return uint32(word(s, i)) | uint32(word(s, i+2))<<16 }

func checksum(s string) uint32 {
	var sum uint32
	for len(s) >= 4 {
		sum ^= dword(s, 0)
		s = s[4:]
	}
	var tail uint32
	for i := 0; i < len(s); i++ {
		tail = tail<<8 | uint32(s[i])
	}
	return sum ^ tail
}

type cabinet struct {
	contents           string
	size, blocks, data int
}

func parse(s string) (cabinet, error) {
	bad := errors.New("invalid or unsupported single-file MSZIP cabinet")
	if len(s) < 61 || len(s) > maxPlain+2<<20 || s[:4] != "MSCF" || int(dword(s, 8)) != len(s) ||
		dword(s, 4) != 0 || dword(s, 12) != 0 || dword(s, 20) != 0 || dword(s, 16) != 44 ||
		word(s, 24) != 0x0103 || word(s, 26) != 1 || word(s, 28) != 1 || word(s, 30) != 0 ||
		word(s, 34) != 0 || word(s, 42) != 1 || dword(s, 48) != 0 || word(s, 52) != 0 {
		return cabinet{}, bad
	}
	c := cabinet{s, int(dword(s, 44)), int(word(s, 40)), int(dword(s, 36))}
	if c.size < 1 || c.size > maxPlain || c.blocks != (c.size+blockSize-1)/blockSize ||
		c.data <= 61 || c.data >= len(s) || strings.IndexByte(s[60:c.data], 0) != c.data-61 {
		return cabinet{}, bad
	}
	return c, nil
}

func (c cabinet) decode(out io.Writer) error {
	var raw [blockSize]byte
	var dict []byte
	pos, remaining := c.data, c.size
	for i := 0; i < c.blocks; i++ {
		if len(c.contents)-pos < 8 {
			return io.ErrUnexpectedEOF
		}
		h := c.contents[pos : pos+8]
		pos += 8
		compressed, plain := int(word(h, 4)), int(word(h, 6))
		if compressed < 3 || compressed > blockSize+6144 || compressed > len(c.contents)-pos ||
			plain != min(remaining, blockSize) {
			return errors.New("invalid MSZIP block lengths")
		}
		data := c.contents[pos : pos+compressed]
		pos += compressed
		if data[:2] != "CK" || checksum(data)^dword(h, 4) != dword(h, 0) {
			return errors.New("MSZIP block header or cabinet checksum mismatch")
		}
		input := strings.NewReader(data[2:])
		// NewReaderDict copies the prior window before the output buffer is reused.
		reader := flate.NewReaderDict(input, dict)
		_, err := io.ReadFull(reader, raw[:plain])
		var extra [1]byte
		_, end := io.ReadFull(reader, extra[:])
		closeErr := reader.Close()
		if err != nil || end != io.EOF || closeErr != nil || input.Len() != 0 {
			return errors.New("MSZIP stream differs from declared block")
		}
		if _, err = out.Write(raw[:plain]); err != nil {
			return err
		}
		dict = raw[:plain]
		remaining -= plain
	}
	if pos != len(c.contents) || remaining != 0 {
		return errors.New("unexpected CAB data at end")
	}
	return nil
}

func digestOK(path, want string, size int) (bool, error) {
	stat, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if !stat.Mode().IsRegular() {
		return false, errors.New("cache entry is not a regular file")
	}
	if stat.Size() != int64(size) {
		return false, nil
	}
	f, err := os.Open(path)
	if err != nil {
		return false, err
	}
	defer f.Close()
	h := sha256.New()
	if _, err = io.Copy(h, f); err != nil {
		return false, err
	}
	return hex.EncodeToString(h.Sum(nil)) == want, nil
}

func install(c cabinet, want, dir string) (string, error) {
	digest, err := hex.DecodeString(want)
	if err != nil || len(digest) != sha256.Size || hex.EncodeToString(digest) != want {
		return "", errors.New("invalid build-time engine digest")
	}
	if err = privateDirectory(dir); err != nil {
		return "", err
	}
	path := filepath.Join(dir, "stockfish-"+want+exeSuffix)
	lock, err := takeLock(dir, want)
	if err != nil {
		return "", err
	}
	defer lock.Close()
	// Only this digest's installer can own these temporary files under the lock.
	prefix := ".partial-" + want + "-"
	entries, err := os.ReadDir(dir)
	if err != nil {
		return "", err
	}
	for _, file := range entries {
		if strings.HasPrefix(file.Name(), prefix) {
			if err := os.Remove(filepath.Join(dir, file.Name())); err != nil {
				return "", err
			}
		}
	}
	valid, err := digestOK(path, want, c.size)
	if err != nil || valid {
		return path, err
	}
	tmp, err := os.CreateTemp(dir, prefix)
	if err != nil {
		return "", err
	}
	defer func() { tmp.Close(); os.Remove(tmp.Name()) }()
	h := sha256.New()
	if err = c.decode(io.MultiWriter(tmp, h)); err != nil {
		return "", err
	}
	if hex.EncodeToString(h.Sum(nil)) != want {
		return "", errors.New("restored engine SHA-256 mismatch")
	}
	if err = tmp.Chmod(0700); err != nil {
		return "", err
	}
	if err = tmp.Sync(); err != nil {
		return "", err
	}
	if err = tmp.Close(); err != nil {
		return "", err
	}
	if err = os.Rename(tmp.Name(), path); err != nil {
		return "", err
	}
	return path, nil
}

func run() (int, error) {
	c, err := parse(archive)
	if err != nil {
		return 1, err
	}
	dir := os.Getenv("STOCKFISH_MSZIP_CACHE")
	if dir == "" {
		base, e := os.UserCacheDir()
		if e != nil {
			return 1, e
		}
		dir = filepath.Join(base, "stockfish-mszip")
	}
	if !filepath.IsAbs(dir) {
		return 1, errors.New("STOCKFISH_MSZIP_CACHE requires an absolute private directory")
	}
	path, err := install(c, strings.TrimSpace(expected), dir)
	if err != nil {
		return 1, err
	}
	return launch(path, os.Args[1:])
}

func main() {
	code, err := run()
	if err != nil {
		fmt.Fprintln(os.Stderr, "Stockfish MSZIP:", err)
	}
	os.Exit(code)
}
