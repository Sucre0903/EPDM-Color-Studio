package main

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"time"
	"unsafe"
)

var kernel32 = syscall.NewLazyDLL("kernel32.dll")
var driveType = kernel32.NewProc("GetDriveTypeW")
var lockFileEx = kernel32.NewProc("LockFileEx")

func requireLocalDisk(folder string) error {
	root, err := syscall.UTF16PtrFromString(filepath.VolumeName(folder) + "\\")
	if err != nil {
		return err
	}
	disk, _, _ := driveType.Call(uintptr(unsafe.Pointer(root)))
	if disk != 3 { // DRIVE_FIXED
		return fmt.Errorf("LOCALAPPDATA 必须在本机固定磁盘 [%s]", folder)
	}
	return nil
}

func initializationLock(home string) (*os.File, error) {
	return lockFile(filepath.Join(home, "initialize.lock"), true, 10*time.Minute)
}

func lockFile(path string, exclusive bool, wait time.Duration) (*os.File, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	deadline := time.Now().Add(wait)
	flags := uintptr(1) // LOCKFILE_FAIL_IMMEDIATELY
	if exclusive {
		flags |= 2 // LOCKFILE_EXCLUSIVE_LOCK
	}
	for {
		var overlap syscall.Overlapped
		ok, _, cause := lockFileEx.Call(file.Fd(), flags, 0, 1, 0, uintptr(unsafe.Pointer(&overlap)))
		if ok != 0 {
			return file, nil
		}
		if cause != syscall.Errno(33) || time.Now().After(deadline) {
			file.Close()
			return nil, fmt.Errorf("获取文件锁 [%s]：%w", path, cause)
		}
		time.Sleep(250 * time.Millisecond)
	}
}
