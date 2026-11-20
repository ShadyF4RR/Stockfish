package main

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
)

const exeSuffix = ""

func privateDirectory(dir string) error {
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	fi, err := os.Lstat(dir)
	if err != nil {
		return err
	}
	if !fi.IsDir() || fi.Mode().Perm()&0077 != 0 || fi.Sys().(*syscall.Stat_t).Uid != uint32(os.Geteuid()) {
		return errors.New("cache directory must be owned by this user with mode 0700")
	}
	return nil
}

func takeLock(dir, digest string) (*os.File, error) {
	f, err := os.OpenFile(filepath.Join(dir, ".lock-"+digest), os.O_RDWR|os.O_CREATE|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, err
	}
	for {
		err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX)
		if err != syscall.EINTR {
			break
		}
	}
	if err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}

func launch(path string, args []string) (int, error) {
	return 1, syscall.Exec(path, append([]string{path}, args...), os.Environ())
}
