package main

import (
	"bytes"
	"compress/flate"
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func cabinetForTest(t *testing.T, raw []byte) string {
	t.Helper()
	var stream bytes.Buffer
	header := make([]byte, 68)
	copy(header, "MSCF")
	put32 := func(i, v int) { binary.LittleEndian.PutUint32(header[i:], uint32(v)) }
	put16 := func(i, v int) { binary.LittleEndian.PutUint16(header[i:], uint16(v)) }
	put32(16, 44)
	put16(24, 0x0103)
	put16(26, 1)
	put16(28, 1)
	put32(36, len(header))
	put16(40, (len(raw)+blockSize-1)/blockSize)
	put16(42, 1)
	put32(44, len(raw))
	copy(header[60:], "example")
	for i := 0; i < len(raw); i += blockSize {
		plain := raw[i:min(i+blockSize, len(raw))]
		var compressed bytes.Buffer
		compressed.WriteString("CK")
		f, err := flate.NewWriterDict(&compressed, flate.BestCompression, raw[max(0, i-blockSize):i])
		if err != nil {
			t.Fatal(err)
		}
		if _, err = f.Write(plain); err != nil {
			t.Fatal(err)
		}
		if err = f.Close(); err != nil {
			t.Fatal(err)
		}
		h := make([]byte, 8)
		binary.LittleEndian.PutUint16(h[4:], uint16(compressed.Len()))
		binary.LittleEndian.PutUint16(h[6:], uint16(len(plain)))
		binary.LittleEndian.PutUint32(h, checksum(compressed.String())^dword(string(h), 4))
		stream.Write(h)
		stream.Write(compressed.Bytes())
	}
	put32(8, len(header)+stream.Len())
	return string(header) + stream.String()
}

func testPayload() []byte {
	// Repeat a full high-entropy window; later blocks can reference prior blocks.
	p := make([]byte, blockSize)
	var x uint32 = 8121
	for i := range p {
		x = 1664525*x + 1013904223
		p[i] = byte(x >> 24)
	}
	return append(append(append([]byte{}, p...), p...), p[:73]...)
}

func TestMSZIP(t *testing.T) {
	raw := testPayload()
	c, err := parse(cabinetForTest(t, raw))
	if err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	if err = c.decode(&out); err != nil || !bytes.Equal(out.Bytes(), raw) {
		t.Fatalf("round trip: %v", err)
	}
	for _, i := range []int{8, 16, 26, 30, 36, 40, 42, 44, 48, 52, 67, 68, 76, len(c.contents) - 1} {
		corrupt := []byte(c.contents)
		corrupt[i] ^= 0x80
		x, err := parse(string(corrupt))
		if err == nil {
			err = x.decode(&bytes.Buffer{})
		}
		if err == nil {
			t.Errorf("accepted corruption at %d", i)
		}
	}
}

func TestConcurrentInstallAndRepair(t *testing.T) {
	raw := testPayload()
	c, err := parse(cabinetForTest(t, raw))
	if err != nil {
		t.Fatal(err)
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(raw))
	dir := filepath.Join(t.TempDir(), "cache")
	var wg sync.WaitGroup
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, e := install(c, digest, dir); e != nil {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	path := filepath.Join(dir, "stockfish-"+digest+exeSuffix)
	if err = os.WriteFile(path, []byte("partial"), 0700); err != nil {
		t.Fatal(err)
	}
	stale := filepath.Join(dir, ".partial-"+digest+"-killed")
	if err = os.WriteFile(stale, []byte("partial"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = install(c, digest, dir); err != nil {
		t.Fatal(err)
	}
	restored, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(restored, raw) {
		t.Fatal("cached engine not repaired", err)
	}
	if _, err = os.Lstat(stale); !os.IsNotExist(err) {
		t.Fatal("stale temporary file persisted")
	}
	badDigest := fmt.Sprintf("%x", sha256.Sum256([]byte("wrong")))
	if _, err = install(c, badDigest, dir); err == nil {
		t.Fatal("accepted incorrect engine SHA-256")
	}
	if _, err = os.Lstat(filepath.Join(dir, "stockfish-"+badDigest+exeSuffix)); !os.IsNotExist(err) {
		t.Fatal("bad engine published")
	}
}
