// Windows 10+ x64. Attach the engine to a kill-on-close job during process
// creation, so terminating the GUI's launcher also terminates the engine.
package main

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"unsafe"
)

const exeSuffix = ".exe"

var kernel = syscall.NewLazyDLL("kernel32.dll")
var advapi = syscall.NewLazyDLL("advapi32.dll")

func privateDirectory(dir string) error {
	// Protect the directory at creation, before creating any executable in it.
	if err := os.MkdirAll(filepath.Dir(dir), 0700); err != nil {
		return err
	}
	token, err := syscall.OpenCurrentProcessToken()
	if err != nil {
		return err
	}
	defer token.Close()
	user, err := token.GetTokenUser()
	if err != nil {
		return err
	}
	sid, err := user.User.Sid.String()
	if err != nil {
		return err
	}
	sddl, _ := syscall.UTF16PtrFromString("O:" + sid + "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;" + sid + ")")
	var expectedSD uintptr
	ok, _, e := advapi.NewProc("ConvertStringSecurityDescriptorToSecurityDescriptorW").Call(
		uintptr(unsafe.Pointer(sddl)), 1, uintptr(unsafe.Pointer(&expectedSD)), 0)
	if ok == 0 {
		return e
	}
	defer syscall.LocalFree(syscall.Handle(expectedSD))
	name, err := syscall.UTF16PtrFromString(dir)
	if err != nil {
		return err
	}
	sa := syscall.SecurityAttributes{SecurityDescriptor: expectedSD}
	sa.Length = uint32(unsafe.Sizeof(sa))
	if err = syscall.CreateDirectory(name, &sa); err != nil && err != syscall.ERROR_ALREADY_EXISTS {
		return err
	}
	attr, err := syscall.GetFileAttributes(name)
	if err != nil {
		return err
	}
	if attr&syscall.FILE_ATTRIBUTE_REPARSE_POINT != 0 || attr&syscall.FILE_ATTRIBUTE_DIRECTORY == 0 {
		return errors.New("unsafe Windows cache directory")
	}
	var owner *syscall.SID
	var actualSD, actualACL, expectedACL uintptr
	code, _, _ := advapi.NewProc("GetNamedSecurityInfoW").Call(uintptr(unsafe.Pointer(name)), 1, 5,
		uintptr(unsafe.Pointer(&owner)), 0, uintptr(unsafe.Pointer(&actualACL)), 0, uintptr(unsafe.Pointer(&actualSD)))
	if code != 0 {
		return syscall.Errno(code)
	}
	defer syscall.LocalFree(syscall.Handle(actualSD))
	ownerName, err := owner.String()
	if err != nil {
		return err
	}
	var present, defaulted uint32
	ok, _, e = advapi.NewProc("GetSecurityDescriptorDacl").Call(expectedSD, uintptr(unsafe.Pointer(&present)),
		uintptr(unsafe.Pointer(&expectedACL)), uintptr(unsafe.Pointer(&defaulted)))
	if ok == 0 {
		return e
	}
	var control uint16
	var revision uint32
	ok, _, e = advapi.NewProc("GetSecurityDescriptorControl").Call(actualSD, uintptr(unsafe.Pointer(&control)), uintptr(unsafe.Pointer(&revision)))
	if ok == 0 {
		return e
	}
	if ownerName != sid || actualACL == 0 || expectedACL == 0 || control&0x1000 == 0 {
		return errors.New("cache must use the launcher's private Windows ACL; choose a new cache directory")
	}
	size := func(acl uintptr) int { return int(*(*uint16)(unsafe.Pointer(acl + 2))) }
	if size(actualACL) != size(expectedACL) || !bytes.Equal(unsafe.Slice((*byte)(unsafe.Pointer(actualACL)), size(actualACL)),
		unsafe.Slice((*byte)(unsafe.Pointer(expectedACL)), size(expectedACL))) {
		return errors.New("cache ACL differs from the private launcher ACL")
	}
	return nil
}

func takeLock(dir, digest string) (*os.File, error) {
	f, err := os.OpenFile(filepath.Join(dir, ".lock-"+digest), os.O_RDWR|os.O_CREATE, 0600)
	if err != nil {
		return nil, err
	}
	var offset syscall.Overlapped
	ok, _, e := kernel.NewProc("LockFileEx").Call(f.Fd(), 2, 0, 1, 0, uintptr(unsafe.Pointer(&offset)))
	if ok == 0 {
		f.Close()
		return nil, e
	}
	return f, nil
}

type basicLimits struct {
	ProcessTime, JobTime int64
	Flags                uint32
	MinSet, MaxSet       uintptr
	Processes            uint32
	Affinity             uintptr
	Priority, Scheduling uint32
}
type extendedLimits struct {
	Basic                                          basicLimits
	IO                                             [6]uint64
	ProcessMemory, JobMemory, PeakProcess, PeakJob uintptr
}

func launch(path string, args []string) (int, error) {
	job, _, e := kernel.NewProc("CreateJobObjectW").Call(0, 0)
	if job == 0 {
		return 1, e
	}
	defer syscall.CloseHandle(syscall.Handle(job))
	limits := extendedLimits{}
	limits.Basic.Flags = 0x2000 // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	ok, _, e := kernel.NewProc("SetInformationJobObject").Call(job, 9, uintptr(unsafe.Pointer(&limits)), unsafe.Sizeof(limits))
	if ok == 0 {
		return 1, e
	}
	var size uintptr
	init := kernel.NewProc("InitializeProcThreadAttributeList")
	init.Call(0, 2, 0, uintptr(unsafe.Pointer(&size)))
	if size == 0 {
		return 1, errors.New("cannot size process attribute list")
	}
	buffer := make([]byte, size)
	list := uintptr(unsafe.Pointer(&buffer[0]))
	ok, _, e = init.Call(list, 2, 0, uintptr(unsafe.Pointer(&size)))
	if ok == 0 {
		return 1, e
	}
	defer runtime.KeepAlive(buffer)
	defer kernel.NewProc("DeleteProcThreadAttributeList").Call(list)

	handles := [3]syscall.Handle{}
	process, err := syscall.GetCurrentProcess()
	if err != nil {
		return 1, err
	}
	for i, f := range []*os.File{os.Stdin, os.Stdout, os.Stderr} {
		src := syscall.Handle(f.Fd())
		if src == 0 || src == syscall.InvalidHandle {
			nul, err := os.OpenFile(os.DevNull, os.O_RDWR, 0)
			if err != nil {
				return 1, err
			}
			defer nul.Close()
			src = syscall.Handle(nul.Fd())
		}
		if err = syscall.DuplicateHandle(process, src, process, &handles[i], 0, true, syscall.DUPLICATE_SAME_ACCESS); err != nil {
			return 1, err
		}
		defer syscall.CloseHandle(handles[i])
	}
	update := kernel.NewProc("UpdateProcThreadAttribute")
	ok, _, e = update.Call(list, 0, 0x20002, uintptr(unsafe.Pointer(&handles[0])), unsafe.Sizeof(handles), 0, 0) // HANDLE_LIST
	if ok == 0 {
		return 1, e
	}
	ok, _, e = update.Call(list, 0, 0x2000d, uintptr(unsafe.Pointer(&job)), unsafe.Sizeof(job), 0, 0) // JOB_LIST
	if ok == 0 {
		return 1, e
	}
	type extendedStartup struct {
		syscall.StartupInfo
		attributes uintptr
	}
	start := extendedStartup{attributes: list}
	start.Cb = uint32(unsafe.Sizeof(start))
	start.Flags = syscall.STARTF_USESTDHANDLES
	start.StdInput, start.StdOutput, start.StdErr = handles[0], handles[1], handles[2]
	exe, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return 1, err
	}
	words := []string{syscall.EscapeArg(path)}
	for _, arg := range args {
		words = append(words, syscall.EscapeArg(arg))
	}
	command, err := syscall.UTF16PtrFromString(strings.Join(words, " "))
	if err != nil {
		return 1, err
	}
	var child syscall.ProcessInformation
	if err = syscall.CreateProcess(exe, command, nil, nil, true, 0x80000, nil, nil, &start.StartupInfo, &child); err != nil {
		return 1, err
	}
	runtime.KeepAlive(handles)
	runtime.KeepAlive(job)
	syscall.CloseHandle(child.Thread)
	defer syscall.CloseHandle(child.Process)
	if _, err = syscall.WaitForSingleObject(child.Process, syscall.INFINITE); err != nil {
		return 1, err
	}
	var code uint32
	if err = syscall.GetExitCodeProcess(child.Process, &code); err != nil {
		return 1, err
	}
	return int(code), nil
}
