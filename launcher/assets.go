package main

import (
	"archive/zip"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"
)

type offlineFile struct {
	Path, SHA256, Name, Version string
	Size                        int64
}

type offlineManifest struct {
	Schema       int
	Version      string
	Commit       string
	LockSHA256   string `json:"lock_sha256"`
	Runtime      runtimePin
	RuntimeAsset offlineFile `json:"runtime_asset"`
	Wheels       []offlineFile
}

func (s *studio) verifyOffline() (string, error) {
	s.log.Print("校验离线 Python 和全部依赖 wheel")
	base := filepath.Join(s.app, "offline")
	var manifest offlineManifest
	if err := readJSON(filepath.Join(base, "manifest.json"), &manifest); err != nil {
		return "", err
	}
	if manifest.Schema != 1 || manifest.Version != s.release.Version || manifest.Commit != s.release.Commit ||
		manifest.LockSHA256 != s.lockHash || manifest.Runtime != s.runtime ||
		manifest.RuntimeAsset.Path != "python-runtime/"+s.runtime.Archive ||
		manifest.RuntimeAsset.SHA256 != s.runtime.SHA256 {
		return "", errors.New("离线清单与发行版本或依赖锁不匹配")
	}
	if err := verifyAsset(base, manifest.RuntimeAsset); err != nil {
		return "", err
	}
	data, err := os.ReadFile(s.lockPath)
	if err != nil {
		return "", err
	}
	pins := make(map[string]string)
	for _, line := range strings.Split(string(data), "\n") {
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		pin, hash, ok := strings.Cut(line, " --hash=sha256:")
		if !ok || !shaPattern.MatchString(hash) || pins[pin] != "" {
			return "", fmt.Errorf("无效或重复依赖锁条目 [%s]", line)
		}
		pins[pin] = hash
	}
	entries, err := os.ReadDir(filepath.Join(base, "wheelhouse"))
	if err != nil {
		return "", err
	}
	if len(pins) == 0 || len(pins) != len(manifest.Wheels) || len(entries) != len(pins) {
		return "", errors.New("wheelhouse 文件数量与依赖锁不符，请重新完整解压发行包")
	}
	paths := make(map[string]bool)
	for _, wheel := range manifest.Wheels {
		pin := wheel.Name + "==" + wheel.Version
		if pins[pin] == "" || pins[pin] != wheel.SHA256 || paths[wheel.Path] ||
			wheel.Path != "wheelhouse/"+path.Base(wheel.Path) || !strings.HasSuffix(wheel.Path, ".whl") {
			return "", fmt.Errorf("离线 wheel 未与依赖锁匹配 [%s]", wheel.Path)
		}
		paths[wheel.Path] = true
		delete(pins, pin)
		if err := verifyAsset(base, wheel); err != nil {
			return "", err
		}
	}
	s.log.Printf("离线资产校验通过（Python 与 %d 个 wheel）", len(manifest.Wheels))
	return filepath.Join(base, filepath.FromSlash(manifest.RuntimeAsset.Path)), nil
}

func verifyAsset(base string, asset offlineFile) error {
	file := filepath.Join(base, filepath.FromSlash(asset.Path))
	info, err := os.Stat(file)
	if err != nil {
		return fmt.Errorf("读取离线资产 [%s]：%w", file, err)
	}
	if !info.Mode().IsRegular() || info.Size() != asset.Size {
		return fmt.Errorf("离线资产大小不匹配 [%s]", file)
	}
	sum, err := hashFile(file)
	if err != nil {
		return err
	}
	if sum != asset.SHA256 {
		return fmt.Errorf("离线资产 SHA256 不匹配 [%s]", file)
	}
	return nil
}

func (s *studio) managedRuntime(offlineArchive string) (string, error) {
	runtimes := filepath.Join(s.home, "runtimes")
	if err := os.MkdirAll(runtimes, 0700); err != nil {
		return "", err
	}
	dest := filepath.Join(runtimes, "python-"+s.runtime.Version+"-"+s.runtime.SHA256[:12])
	python := filepath.Join(dest, "python.exe")
	if _, err := os.Stat(dest); err == nil {
		if err := s.probe(python, "base", s.runtime.Version); err == nil {
			s.log.Printf("复用应用 Python [%s]", python)
			return python, nil
		} else {
			s.log.Printf("应用 Python 需要修复：%v", err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	archive := offlineArchive
	if archive == "" {
		if s.release.Mode != "lite" {
			return "", errors.New("完整离线版缺少已校验的本地运行时")
		}
		var err error
		archive, err = s.acquireRuntime()
		if err != nil {
			return "", err
		}
	}
	stage, err := os.MkdirTemp(runtimes, "stage-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(stage)
	s.log.Printf("解包 Python 到本机 [%s]", stage)
	if err := extractRuntime(archive, stage); err != nil {
		return "", err
	}
	if err := s.probe(filepath.Join(stage, "python.exe"), "base", s.runtime.Version); err != nil {
		return "", fmt.Errorf("校验解包后的 Python：%w", err)
	}
	if _, err := os.Stat(dest); err == nil {
		old := dest + ".damaged-" + filepath.Base(stage)
		if err := os.Rename(dest, old); err != nil {
			return "", fmt.Errorf("移出损坏运行时 [%s]：%w", dest, err)
		}
	}
	if err := os.Rename(stage, dest); err != nil {
		return "", fmt.Errorf("提交运行时 [%s]：%w", dest, err)
	}
	return python, nil
}

func extractRuntime(archive, stage string) error {
	z, err := zip.OpenReader(archive)
	if err != nil {
		return fmt.Errorf("打开 Python 压缩包 [%s]：%w", archive, err)
	}
	defer z.Close()
	for _, item := range z.File {
		name, isRuntime := strings.CutPrefix(item.Name, "tools/")
		if !isRuntime || name == "" || strings.HasSuffix(name, "/") {
			continue
		}
		if !filepath.IsLocal(filepath.FromSlash(name)) || strings.ContainsAny(name, `\:`) ||
			path.Clean(name) != name || !item.Mode().IsRegular() {
			return fmt.Errorf("无效的 Python 运行时条目 [%s]", item.Name)
		}
		target := filepath.Join(stage, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
			return fmt.Errorf("创建 Python 目录 [%s]：%w", filepath.Dir(target), err)
		}
		if err := extractFile(item, target); err != nil {
			return err
		}
	}
	return nil
}

func extractFile(entry *zip.File, target string) error {
	source, err := entry.Open()
	if err != nil {
		return fmt.Errorf("读取压缩条目 [%s]：%w", entry.Name, err)
	}
	defer source.Close()
	file, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return fmt.Errorf("创建 Python 文件 [%s]：%w", target, err)
	}
	_, copyErr := io.Copy(file, source)
	closeErr := file.Close()
	if copyErr != nil {
		return fmt.Errorf("写入 Python 文件 [%s]：%w", target, copyErr)
	}
	if closeErr != nil {
		return fmt.Errorf("关闭 Python 文件 [%s]：%w", target, closeErr)
	}
	return nil
}

type transferProgress struct {
	count, total int64
	start, last  time.Time
	studio       *studio
}

func (p *transferProgress) Write(data []byte) (int, error) {
	p.count += int64(len(data))
	now := time.Now()
	if now.Sub(p.last) >= time.Second || p.count == p.total {
		speed := float64(p.count) / now.Sub(p.start).Seconds() / (1 << 20)
		if p.total > 0 {
			p.studio.log.Printf("Python %.1f / %.1f MiB，平均 %.1f MiB/s",
				float64(p.count)/(1<<20), float64(p.total)/(1<<20), speed)
		} else {
			p.studio.log.Printf("Python %.1f MiB，平均 %.1f MiB/s", float64(p.count)/(1<<20), speed)
		}
		p.last = now
	}
	return len(data), nil
}

func (s *studio) acquireRuntime() (string, error) {
	folder := filepath.Join(s.home, "cache", "runtime")
	if err := os.MkdirAll(folder, 0700); err != nil {
		return "", err
	}
	dest := filepath.Join(folder, s.runtime.Archive)
	if checksum, err := hashFile(dest); err == nil && checksum == s.runtime.SHA256 {
		s.log.Printf("复用 SHA256 已校验的 Python 下载包 [%s]", dest)
		return dest, nil
	}
	client := &http.Client{
		Timeout: 90 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if req.URL.Scheme != "https" || len(via) >= 4 {
				return errors.New("Python 下载重定向超出允许范围")
			}
			return nil
		},
	}
	var err error
	for attempt := 1; attempt <= 2; attempt++ {
		s.log.Printf("下载 Python（%d/2）%s", attempt, s.runtime.URL)
		err = s.downloadRuntime(client, dest)
		if err == nil {
			return dest, nil
		}
		s.log.Printf("Python 下载失败：%v", err)
	}
	return "", fmt.Errorf("无法获取锁定的 Python 运行时：%w", err)
}

func (s *studio) downloadRuntime(client *http.Client, dest string) error {
	response, err := client.Get(s.runtime.URL)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %s", response.Status)
	}
	partial, err := os.CreateTemp(filepath.Dir(dest), "python-*.part")
	if err != nil {
		return err
	}
	defer os.Remove(partial.Name())
	now := time.Now()
	progress := &transferProgress{start: now, last: now, total: response.ContentLength, studio: s}
	_, copyErr := io.Copy(io.MultiWriter(partial, progress), io.LimitReader(response.Body, 64<<20))
	if err := errors.Join(copyErr, partial.Close()); err != nil {
		return fmt.Errorf("保存下载包 [%s]：%w", partial.Name(), err)
	}
	hash, err := hashFile(partial.Name())
	if err != nil {
		return err
	}
	if hash != s.runtime.SHA256 {
		return fmt.Errorf("下载的 Python SHA256 不匹配 [%s]", partial.Name())
	}
	return os.Rename(partial.Name(), dest)
}
