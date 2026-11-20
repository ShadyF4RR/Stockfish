package main

import (
	"io"
	"testing"
)

// Benchmark the production decoder on the embedded, pre-verified release CAB.
// The output sink omits disk writes, cache installation and SHA-256 hashing.
func BenchmarkMSZIPDecode(b *testing.B) {
	c, err := parse(archive)
	if err != nil {
		b.Fatal(err)
	}
	b.SetBytes(int64(c.size))
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if err := c.decode(io.Discard); err != nil {
			b.Fatal(err)
		}
	}
}
