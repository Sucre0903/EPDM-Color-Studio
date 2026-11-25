package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const officialIndex = "https://pypi.org/simple"

func (s *studio) environmentFolder(mode string) string {
	return filepath.Join(s.home, "environments", mode+"-"+s.lockHash[:16]+"-"+s.runtime.SHA256[:12])
}

func (s *studio) prepare() (string, error) {
	s.log.Print("检查本机运行环境")
	guard, err := initializationLock(s.home)
	if err != nil {
		return "", err
	}
	defer guard.Close()
	folder := s.environmentFolder(s.release.Mode)
	expected := ""
	if s.release.Mode == "full-offline" {
		expected = s.runtime.Version
	}
	if python := s.reusablePython(folder, expected, false); python != "" {
		s.log.Print("复用已验证的本机环境")
		return python, nil
	}
	other := "full-offline"
	if s.release.Mode == "full-offline" {
		other = "lite"
	}
	if python := s.reusablePython(s.environmentFolder(other), s.runtime.Version, other == "lite"); python != "" {
		s.log.Printf("复用 %s 已验证的本机环境", other)
		return python, nil
	}
	if err := os.MkdirAll(folder, 0700); err != nil {
		return "", err
	}
	statePath := filepath.Join(folder, "active.json")

	var offlineArchive string
	if s.release.Mode == "full-offline" {
		offlineArchive, err = s.verifyOffline()
		if err != nil {
			return "", err
		}
	} else if python := s.findSystemPython(); python != "" {
		s.log.Printf("复用已安装的 Python [%s]", python)
		env, err := s.createEnvironment(folder, statePath, python, true, "")
		if err == nil {
			return env, nil
		}
		// A network failure will also affect the bundled base. Report it directly.
		var installFailure *onlineInstallError
		if errors.As(err, &installFailure) {
			return "", err
		}
		s.log.Printf("系统 Python 无法建立可用环境，尝试随应用管理的 Python：%v", err)
	}
	base, err := s.managedRuntime(offlineArchive)
	if err != nil {
		return "", err
	}
	return s.createEnvironment(folder, statePath, base, false, expected)
}

func activeGeneration(folder string) string {
	var active string
	if err := readJSON(filepath.Join(folder, "active.json"), &active); err != nil ||
		filepath.Base(active) != active || !strings.HasPrefix(active, "env-") {
		return ""
	}
	return active
}

func (s *studio) reusablePython(folder, expected string, managedOnly bool) string {
	active := activeGeneration(folder)
	if active == "" {
		return ""
	}
	dir := filepath.Join(folder, active)
	if managedOnly && !managedIsolatedVenv(dir, s.runtimeFolder()) {
		return ""
	}
	python := filepath.Join(dir, "Scripts", "python.exe")
	if err := s.probe(python, "env", s.lockPath, expected); err != nil {
		s.log.Printf("已缓存环境需要修复：%v", err)
		return ""
	}
	return python
}

func managedIsolatedVenv(dir, runtimeDir string) bool {
	options := venvSettings(dir)
	return strings.EqualFold(options["include-system-site-packages"], "false") &&
		strings.EqualFold(filepath.Clean(options["home"]), filepath.Clean(runtimeDir))
}

func venvSettings(dir string) map[string]string {
	data, err := os.ReadFile(filepath.Join(dir, "pyvenv.cfg"))
	if err != nil {
		return nil
	}
	options := make(map[string]string)
	for _, line := range strings.Split(string(data), "\n") {
		if key, value, ok := strings.Cut(line, "="); ok {
			options[strings.TrimSpace(key)] = strings.TrimSpace(value)
		}
	}
	return options
}

func (s *studio) createEnvironment(folder, statePath, base string, reuse bool, expected string) (string, error) {
	dir, err := os.MkdirTemp(folder, "env-")
	if err != nil {
		return "", err
	}
	ready := false
	defer func() {
		if !ready {
			if err := os.RemoveAll(dir); err != nil {
				s.log.Printf("清理初始化目录 [%s]：%v", dir, err)
			}
		}
	}()
	args := []string{"-m", "venv", "--copies", "--without-scm-ignore-files"}
	if reuse {
		args = append(args, "--system-site-packages")
	}
	s.log.Printf("创建应用虚拟环境 [%s]", dir)
	if err := s.stage(base, "创建虚拟环境", 4*time.Minute, append(args, dir)...); err != nil {
		return "", err
	}
	python := filepath.Join(dir, "Scripts", "python.exe")
	if reuse && s.probe(python, "env", s.lockPath, expected) == nil {
		s.log.Print("现有依赖符合完整锁定版本，跳过安装")
	} else {
		if err := s.installWheels(python); err != nil {
			return "", err
		}
	}
	s.log.Print("校验完整依赖版本与原生模块")
	if err := s.probe(python, "env", s.lockPath, expected); err != nil {
		return "", err
	}
	if err := saveState(statePath, filepath.Base(dir)); err != nil {
		return "", err
	}
	ready = true
	return python, nil
}

func saveState(path, name string) error {
	file, err := os.CreateTemp(filepath.Dir(path), "state-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	writeErr := json.NewEncoder(file).Encode(name)
	closeErr := file.Close()
	if err := errors.Join(writeErr, closeErr); err != nil {
		return fmt.Errorf("写入状态 [%s]：%w", path, err)
	}
	return os.Rename(file.Name(), path)
}

type onlineInstallError struct{ error }

func (s *studio) installWheels(python string) error {
	common := []string{"-m", "pip", "--disable-pip-version-check", "--no-input", "install",
		"--only-binary", ":all:", "--require-hashes", "--no-deps", "--no-compile", "--progress-bar", "on",
		"-r", s.lockPath}
	if s.release.Mode == "full-offline" {
		wheels := filepath.Join(s.app, "offline", "wheelhouse")
		s.log.Printf("从已校验的本地 wheelhouse 安装 [%s]", wheels)
		return s.stage(python, "离线安装", 8*time.Minute,
			append(common, "--no-index", "--no-cache-dir", "--find-links", wheels)...)
	}
	indexes := []string{officialIndex}
	if mirror := strings.TrimSpace(os.Getenv("ZT_PYPI_INDEX_URL")); mirror != "" {
		parsed, err := url.Parse(mirror)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" ||
			parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
			return &onlineInstallError{errors.New("ZT_PYPI_INDEX_URL 必须是无凭据、查询参数及片段的 HTTPS 源")}
		}
		if strings.TrimRight(mirror, "/") != officialIndex {
			indexes = append([]string{mirror}, indexes...)
		}
	}
	cache := filepath.Join(s.home, "cache", "pip")
	if err := os.MkdirAll(cache, 0700); err != nil {
		return err
	}
	var err error
	for _, index := range indexes {
		start := time.Now()
		s.log.Printf("检查及获取缺失的依赖，来源 %s", index)
		err = s.stage(python, "在线安装", 8*time.Minute,
			append(common, "--index-url", index, "--cache-dir", cache, "--timeout", "12", "--retries", "1")...)
		if err == nil {
			return nil
		}
		s.log.Printf("来源失败（%s）：%v", time.Since(start).Round(time.Second), err)
	}
	return &onlineInstallError{fmt.Errorf("依赖安装失败，重试会复用 pip 缓存：%w", err)}
}

func (s *studio) findSystemPython() string {
	var candidates []string
	if explicit := os.Getenv("ZT_PYTHON_EXE"); explicit != "" {
		candidates = append(candidates, explicit)
	}
	registry := filepath.Join(os.Getenv("SystemRoot"), "System32", "reg.exe")
	for _, root := range []string{"HKCU", "HKLM"} {
		ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
		key := root + `\SOFTWARE\Python\PythonCore\3.13\InstallPath`
		output, _ := exec.CommandContext(ctx, registry, "query", key, "/v", "ExecutablePath", "/reg:64").Output()
		cancel()
		for _, line := range strings.Split(string(output), "\n") {
			if _, path, ok := strings.Cut(strings.TrimSpace(line), "REG_SZ"); ok {
				candidates = append(candidates, strings.TrimSpace(path))
			}
		}
	}
	for _, dir := range filepath.SplitList(os.Getenv("PATH")) {
		if filepath.IsAbs(dir) {
			candidates = append(candidates, filepath.Join(dir, "python.exe"))
		}
	}
	for _, dir := range []string{os.Getenv("LOCALAPPDATA"), os.Getenv("USERPROFILE"), os.Getenv("ProgramData")} {
		if filepath.IsAbs(dir) {
			candidates = append(candidates, filepath.Join(dir, "Programs", "Python", "Python313", "python.exe"))
			candidates = append(candidates, filepath.Join(dir, "anaconda3", "python.exe"), filepath.Join(dir, "miniconda3", "python.exe"))
		}
	}
	seen := make(map[string]bool)
	for _, candidate := range candidates {
		key := strings.ToLower(filepath.Clean(candidate))
		if !filepath.IsAbs(candidate) || seen[key] || strings.Contains(key, `\microsoft\windowsapps\`) {
			continue
		}
		seen[key] = true
		if st, err := os.Stat(candidate); err != nil || st.IsDir() {
			continue
		}
		if err := s.probe(candidate, "base", ""); err != nil {
			s.log.Printf("跳过不兼容 Python：%v", err)
		} else {
			return candidate
		}
	}
	return ""
}
