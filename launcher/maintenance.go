package main

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

func (s *studio) maintain(action string) error {
	if action != "keep" && action != "purge" && action != "prune" {
		return errors.New("维护选项无效，请使用 uninstall.bat 或 start.bat --maintenance prune")
	}
	if action == "prune" {
		hash, err := hashFile(s.lockPath)
		if err != nil {
			return err
		}
		s.lockHash = hash
		fmt.Printf("将清理与当前版本锁定依赖不匹配的旧环境与旧运行时 [%s]。使用旧版本可能需要重新初始化。\n", s.home)
		if err := confirm(); err != nil {
			return err
		}
		return s.pruneOldRuntime()
	}

	backups := filepath.Join(filepath.Dir(s.home), "backups")
	if filepath.Dir(s.app) == s.app || containsPath(s.app, s.home) ||
		containsPath(s.home, s.app) || containsPath(s.app, backups) || containsPath(backups, s.app) {
		return errors.New("发行包目录与本机运行时或备份目录重叠，请使用独立解压目录")
	}
	for _, name := range []string{"app.py", "start.bat", "uninstall.bat"} {
		info, err := os.Lstat(filepath.Join(s.app, name))
		if err != nil || !info.Mode().IsRegular() {
			return fmt.Errorf("无法确认应用卸载目录 [%s]", s.app)
		}
	}
	fmt.Printf("即将删除整个应用目录及其内容 [%s]\n", s.app)
	fmt.Printf("色库和历史记录会先备份至 [%s]\n", backups)
	if action == "purge" {
		fmt.Printf("同时删除所有 Lite/Full 共用的应用环境、依赖、下载缓存和启动日志 [%s]\n", s.home)
	}
	if err := confirm(); err != nil {
		return err
	}
	data := filepath.Join(s.app, "data")
	if _, err := os.Lstat(data); err == nil {
		if err := ensurePlainDirectory(backups); err != nil {
			return err
		}
		backup, err := os.MkdirTemp(backups, s.release.Version+"-")
		if err != nil {
			return err
		}
		if err := copyDataTree(data, filepath.Join(backup, "data")); err != nil {
			os.RemoveAll(backup)
			return fmt.Errorf("备份失败，已停止卸载：%w", err)
		}
		fmt.Printf("数据已备份 [%s]\n", backup)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if action == "purge" {
		if err := removeOwnedDirectory(s.home); err != nil {
			return err
		}
		fmt.Println("应用运行环境、依赖和缓存已清理")
	}
	return nil
}

func (s *studio) importData(source string) error {
	old, err := filepath.Abs(source)
	if err != nil {
		return err
	}
	oldData := filepath.Join(old, "data")
	newData := filepath.Join(s.app, "data")
	if containsPath(oldData, newData) || containsPath(newData, oldData) {
		return errors.New("选择另一个应用目录或卸载备份目录")
	}
	sourceFiles, err := os.ReadDir(oldData)
	if err != nil {
		return fmt.Errorf("读取旧版 data 目录 [%s]：%w", oldData, err)
	}
	if len(sourceFiles) == 0 {
		return errors.New("旧版 data 目录为空")
	}
	hasDefault := false
	entries, err := os.ReadDir(newData)
	if err == nil {
		if err := requirePlainDirectory(newData); err != nil {
			return err
		}
		hasDefault = true
		if len(entries) != 1 || entries[0].Name() != "fk_colors.csv" ||
			!entries[0].Type().IsRegular() {
			return errors.New("新版目录已含用户数据，请解压一份新的发行包并在首次启动前导入")
		}
		hash, err := hashFile(filepath.Join(newData, "fk_colors.csv"))
		if err != nil {
			return err
		}
		if hash != s.release.InitialLibrarySHA256 {
			return errors.New("新版色库已有修改，请解压一份新的发行包后导入")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	// Stage the entire tree next to the destination so final moves stay on one disk.
	staging, err := os.MkdirTemp(s.app, ".zt-import-")
	if err != nil {
		return err
	}
	cleanup := true
	defer func() {
		if cleanup {
			os.RemoveAll(staging)
		}
	}()
	next := filepath.Join(staging, "data")
	if err := copyDataTree(oldData, next); err != nil {
		return fmt.Errorf("复制数据失败：%w", err)
	}
	previous := filepath.Join(staging, "previous-data")
	if hasDefault {
		if err := os.Rename(newData, previous); err != nil {
			return err
		}
	}
	if err := os.Rename(next, newData); err != nil {
		if hasDefault {
			if restoreErr := os.Rename(previous, newData); restoreErr != nil {
				cleanup = false
				err = errors.Join(err, fmt.Errorf("原数据保存在 [%s]：%w", previous, restoreErr))
			}
		}
		return fmt.Errorf("写入新版 data 目录失败：%w", err)
	}
	fmt.Printf("色库和历史数据已从 [%s] 导入 [%s]，请双击 start.bat 启动。\n", oldData, newData)
	return nil
}

func containsPath(parent, child string) bool {
	relative, err := filepath.Rel(parent, child)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(os.PathSeparator))
}

func confirm() error {
	fmt.Print("请关闭其他版本的启动窗口。输入 Y 确认，其他输入取消：")
	var answer string
	if _, err := fmt.Fscan(os.Stdin, &answer); err != nil || !strings.EqualFold(answer, "Y") {
		return errors.New("操作已取消")
	}
	return nil
}

func ensurePlainDirectory(path string) error {
	if err := os.MkdirAll(path, 0700); err != nil {
		return err
	}
	return requirePlainDirectory(path)
}

func requirePlainDirectory(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("需要普通目录 [%s]", path)
	}
	return nil
}

func removeOwnedDirectory(path string) error {
	if err := requirePlainDirectory(path); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return err
	}
	if err := os.RemoveAll(path); err != nil {
		return fmt.Errorf("删除 [%s] 失败，请关闭占用文件的程序：%w", path, err)
	}
	return nil
}

func copyDataTree(source, destination string) error {
	if err := requirePlainDirectory(source); err != nil {
		return err
	}
	return filepath.WalkDir(source, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		out := filepath.Join(destination, relative)
		if entry.IsDir() {
			return os.Mkdir(out, 0700)
		}
		if !entry.Type().IsRegular() {
			return fmt.Errorf("数据目录中存在非普通文件 [%s]", path)
		}

		input, err := os.Open(path)
		if err != nil {
			return err
		}
		defer input.Close()
		output, err := os.OpenFile(out, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		_, copyErr := io.Copy(output, input)
		return errors.Join(copyErr, output.Close())
	})
}

var environmentName = regexp.MustCompile(`^(lite|full-offline)-[a-f0-9]{16}-[a-f0-9]{12}$`)
var generationName = regexp.MustCompile(`^env-[0-9]+$`)
var runtimeName = regexp.MustCompile(`^python-[0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]{12}(\.damaged-stage-[0-9]+)?$`)

func (s *studio) pruneOldRuntime() error {
	if err := requirePlainDirectory(s.home); errors.Is(err, os.ErrNotExist) {
		return nil
	} else if err != nil {
		return err
	}
	runtimes := filepath.Join(s.home, "runtimes")
	keep := map[string]bool{strings.ToLower(s.runtimeFolder()): true}
	for _, mode := range []string{"lite", "full-offline"} {
		folder := s.environmentFolder(mode)
		keep[strings.ToLower(folder)] = true
		if name := activeGeneration(folder); name != "" {
			active := filepath.Join(folder, name)
			base := filepath.Clean(venvSettings(active)["home"])
			if strings.EqualFold(filepath.Dir(base), runtimes) {
				keep[strings.ToLower(base)] = true
			}
			if err := pruneDirectories(folder, generationName, map[string]bool{strings.ToLower(active): true}); err != nil {
				return err
			}
		}
	}
	if err := pruneDirectories(filepath.Join(s.home, "environments"), environmentName, keep); err != nil {
		return err
	}
	return pruneDirectories(runtimes, runtimeName, keep)
}

func pruneDirectories(parent string, names *regexp.Regexp, keep map[string]bool) error {
	if err := requirePlainDirectory(parent); errors.Is(err, os.ErrNotExist) {
		return nil
	} else if err != nil {
		return err
	}
	entries, err := os.ReadDir(parent)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		path := filepath.Join(parent, entry.Name())
		if entry.IsDir() && names.MatchString(entry.Name()) && !keep[strings.ToLower(path)] {
			if err := removeOwnedDirectory(path); err != nil {
				return err
			}
			fmt.Printf("已清理 [%s]\n", path)
		}
	}
	return nil
}
