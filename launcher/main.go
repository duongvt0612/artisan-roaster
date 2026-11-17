package main

import (
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"time"
)

const (
	envRoot       = "LVG_LAUNCHER_ROOT"
	envExe        = "LVG_LAUNCHER_EXE"
	logFile       = "launcher.log"
	maxLogBytes   = 1 << 20
	waitPIDLimit  = 2 * time.Minute
	waitPIDOption = "--wait-pid"
)

// splitArgs takes "--wait-pid <pid>" off the front (Artisan restarting into an update); everything else is for Artisan (files to open).
func splitArgs(args []string) (int, []string) {
	if len(args) >= 2 && args[0] == waitPIDOption {
		if pid, err := strconv.Atoi(args[1]); err == nil && pid > 0 {
			return pid, args[2:]
		}
		return 0, args[2:]
	}
	return 0, args
}

func openLog(root string) *os.File {
	path := filepath.Join(root, logFile)
	if info, err := os.Stat(path); err == nil && info.Size() > maxLogBytes {
		_ = os.Rename(path, path+".1")
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil
	}
	return f
}

// start runs one version and waits for it to end.
func start(root, self, version string, args []string) error {
	cmd := exec.Command(exePath(root, version), args...)
	cmd.Dir = filepath.Join(root, versionsDir, version)
	cmd.Env = append(os.Environ(), envRoot+"="+root, envExe+"="+self)
	log.Printf("start %s %v", version, args)
	if err := cmd.Start(); err != nil {
		return err
	}
	err := cmd.Wait()
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		log.Printf("%s ended with code %d", version, exitErr.ExitCode())
		return nil
	}
	if err != nil {
		return err
	}
	log.Printf("%s ended", version)
	return nil
}

func launcherPath() (string, error) {
	self, err := os.Executable()
	if err != nil {
		return "", err
	}
	if resolved, err := filepath.EvalSymlinks(self); err == nil {
		self = resolved
	}
	return self, nil
}

func run(self string) error {
	root := filepath.Dir(self)
	pid, args := splitArgs(os.Args[1:])
	if pid > 0 {
		log.Printf("waiting for process %d to end", pid)
		waitPID(pid, waitPIDLimit)
	}

	version, err := choose(root)
	if err != nil {
		return err
	}
	if err := start(root, self, version, args); err != nil {
		return fmt.Errorf("không chạy được Artisan %s: %w", version, err)
	}
	fallback, err := afterExit(root, version)
	if err != nil {
		log.Printf("rollback of %s failed: %v", version, err)
		return nil
	}
	if fallback == "" {
		return nil
	}
	log.Printf("%s did not start, rolled back to %s", version, fallback)
	showMessage("Artisan", fmt.Sprintf("Bản Artisan %s không khởi động được. Đã quay về bản %s.", version, fallback))
	if err := start(root, self, fallback, args); err != nil {
		return fmt.Errorf("không chạy được Artisan %s: %w", fallback, err)
	}
	return nil
}

func main() {
	self, err := launcherPath()
	if err == nil {
		// the log stays open until the very end: an error of run() must land in it too
		if f := openLog(filepath.Dir(self)); f != nil {
			defer func() { _ = f.Close() }()
			log.SetOutput(f)
		}
		log.SetFlags(log.LstdFlags)
		err = run(self)
	}
	if err != nil {
		log.Printf("error: %v", err)
		showMessage("Artisan", err.Error())
		os.Exit(1) //nolint:gocritic // the deferred Close is skipped, but the log is written unbuffered
	}
}
