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
	path := filepath.Join(home, "initialize.lock")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	deadline := time.Now().Add(10 * time.Minute)
	for {
		var overlap syscall.Overlapped
		ok, _, cause := lockFileEx.Call(file.Fd(), 3, 0, 1, 0, uintptr(unsafe.Pointer(&overlap)))
		if ok != 0 {
			return file, nil
		}
		if cause != syscall.Errno(33) || time.Now().After(deadline) {
			file.Close()
			return nil, fmt.Errorf("获取初始化锁 [%s]：%w", path, cause)
		}
		time.Sleep(250 * time.Millisecond)
	}
}
