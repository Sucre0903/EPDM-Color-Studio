package main

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

const lockName = "requirements-lock-win-amd64-py313.txt"

var buildVersion = "source"
var buildCommit = "source"

//go:embed runtime.json
var runtimeJSON []byte

type runtimePin struct {
	Version, ABI, Architecture, Archive, URL, SHA256, License string
}

type releaseInfo struct {
	Mode, Version, Commit string
	InitialLibrarySHA256  string `json:"initial_library_sha256"`
}

type launchOptions struct {
	check, verify, headless bool
	port                    int
	importFrom, maintenance string
}

type studio struct {
	app, home, lockPath, lockHash string
	runtime                       runtimePin
	release                       releaseInfo
	log                           *log.Logger
	out                           io.Writer
}

var shaPattern = regexp.MustCompile("^[a-f0-9]{64}$")

func main() {
	var options launchOptions
	flag.BoolVar(&options.check, "check", false, "initialize and verify without starting the app")
	flag.BoolVar(&options.verify, "verify-offline", false, "verify the offline archive and every wheel")
	flag.BoolVar(&options.headless, "headless", false, "print the local URL without opening a browser")
	flag.IntVar(&options.port, "port", 8501, "local starting port")
	flag.StringVar(&options.importFrom, "import-data", "", "import data from an older installation or an uninstall backup")
	flag.StringVar(&options.maintenance, "maintenance", "", "keep, purge, or prune local runtime files")
	flag.Parse()
	if options.port < 1 || options.port > 65535 || flag.NArg() != 0 ||
		(options.importFrom != "" && options.maintenance != "") {
		fmt.Fprintln(os.Stderr, "无效启动参数。运行 start.bat -help 查看选项。")
		os.Exit(2)
	}
	if err := run(options); err != nil {
		fmt.Fprintln(os.Stderr, "启动失败：", err)
		os.Exit(1)
	}
}

func run(options launchOptions) (result error) {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	app := filepath.Dir(executable)
	local := os.Getenv("LOCALAPPDATA")
	if !filepath.IsAbs(local) || strings.HasPrefix(local, `\\`) {
		return fmt.Errorf("LOCALAPPDATA 必须指向本机用户目录 [%s]", local)
	}
	if err := requireLocalDisk(local); err != nil {
		return err
	}
	s := studio{app: app, home: filepath.Join(local, "ZTColorStudio", "studio-v14")}
	if err := readJSON(filepath.Join(app, "distribution.json"), &s.release); err != nil {
		return err
	}
	if s.release.Version != buildVersion || s.release.Commit != buildCommit ||
		(s.release.Mode != "lite" && s.release.Mode != "full-offline") {
		return errors.New("发行标记与启动器不匹配，请重新完整解压发行包")
	}
	if err := json.Unmarshal(runtimeJSON, &s.runtime); err != nil {
		return err
	}
	s.lockPath = filepath.Join(app, lockName)
	if err := ensurePlainDirectory(filepath.Dir(s.home)); err != nil {
		return err
	}
	maintenance := options.importFrom != "" || options.maintenance != ""
	guard, err := lockFile(filepath.Join(filepath.Dir(s.home), "studio-v14.lock"), maintenance, 0)
	if err != nil {
		return fmt.Errorf("无法获取运行环境锁，请关闭所有 ZT Color Match Studio 窗口后重试：%w", err)
	}
	defer guard.Close()
	if options.importFrom != "" {
		return s.importData(options.importFrom)
	}
	if options.maintenance != "" {
		return s.maintain(options.maintenance)
	}

	logDir := filepath.Join(s.home, "logs")
	if err := os.MkdirAll(logDir, 0700); err != nil {
		return fmt.Errorf("创建日志目录 [%s]：%w", logDir, err)
	}
	logFile, err := os.CreateTemp(logDir, time.Now().Format("20060102-150405")+"-*.log")
	if err != nil {
		return fmt.Errorf("创建启动日志 [%s]：%w", logDir, err)
	}
	defer logFile.Close()
	s.out = io.MultiWriter(os.Stdout, logFile)
	s.log = log.New(s.out, "", log.Ltime)
	start := time.Now()
	s.log.Printf("日志 [%s]", logFile.Name())
	defer func() {
		if result != nil {
			s.log.Printf("失败（%s）：%v", time.Since(start).Round(time.Second), result)
			if errors.Is(result, os.ErrPermission) {
				s.log.Print("请将日志交给公司 IT 核对路径权限与终端安全策略。")
			}
		}
	}()
	if s.lockHash, err = hashFile(s.lockPath); err != nil {
		return err
	}
	s.log.Printf("ZT Color Match Studio %s · %s · Python %s (x64)", s.release.Version, s.release.Mode, s.runtime.Version)
	if options.verify {
		if s.release.Mode != "full-offline" {
			return errors.New("仅 Full Offline 包支持离线校验")
		}
		_, err = s.verifyOffline()
		return err
	}

	python, err := s.prepare()
	if err != nil {
		return err
	}
	s.log.Printf("环境就绪（%s） [%s]", time.Since(start).Round(time.Second), python)
	if options.check {
		return nil
	}
	address := os.Getenv("ZT_SERVER_ADDRESS")
	if address == "" {
		address = "127.0.0.1"
	}
	s.log.Printf("启动 Streamlit，浏览器地址 http://localhost:%d", options.port)
	cmd := s.pythonCommand(context.Background(), python, "-m", "streamlit", "run", "app.py",
		"--server.address", address, "--server.port", fmt.Sprint(options.port),
		"--server.headless", fmt.Sprint(options.headless), "--browser.serverAddress", "localhost",
		"--server.showEmailPrompt", "false", "--browser.gatherUsageStats", "false")
	cmd.Stdout, cmd.Stderr = s.out, s.out
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("运行 Streamlit [%s]：%w", python, err)
	}
	return nil
}

func (s *studio) pythonCommand(ctx context.Context, python string, args ...string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, python, append([]string{"-I", "-u", "-X", "utf8"}, args...)...)
	cmd.Dir = s.app
	for _, entry := range os.Environ() {
		upper := strings.ToUpper(entry)
		if strings.HasPrefix(upper, "PIP_") || strings.HasPrefix(upper, "PYTHON") ||
			strings.HasPrefix(upper, "VIRTUAL_ENV=") || strings.HasPrefix(upper, "CONDA_PREFIX=") {
			continue
		}
		cmd.Env = append(cmd.Env, entry)
	}
	cmd.Env = append(cmd.Env, "PIP_CONFIG_FILE=nul", "NO_COLOR=1")
	return cmd
}

func (s *studio) probe(python string, args ...string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := s.pythonCommand(ctx, python, append([]string{filepath.Join(s.app, "windows_runtime.py")}, args...)...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("[%s] %w %s", python, err, strings.TrimSpace(string(output)))
	}
	return nil
}

func (s *studio) stage(python, phase string, timeout time.Duration, args ...string) error {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := s.pythonCommand(ctx, python, args...)
	cmd.Stdout, cmd.Stderr = s.out, s.out
	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return fmt.Errorf("%s超过 %s [%s]：%w", phase, timeout, python, ctx.Err())
		}
		return fmt.Errorf("%s [%s]：%w", phase, python, err)
	}
	return nil
}

func readJSON(path string, target any) error {
	data, err := os.ReadFile(path)
	if err == nil {
		err = json.Unmarshal(data, target)
	}
	if err != nil {
		return fmt.Errorf("读取 [%s]：%w", path, err)
	}
	return nil
}

func hashFile(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", fmt.Errorf("打开 [%s]：%w", path, err)
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", fmt.Errorf("校验 [%s]：%w", path, err)
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}
