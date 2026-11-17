// Package main is the launcher of the portable Windows build of Artisan (LVG ERP fork).
//
// Layout of the folder it lives in (see src/erp/updater.py, which writes the same files):
//
//	Artisan.exe              this launcher; the shortcuts point here
//	current.json             {"current": "125", "previous": "120", "blocked": null}
//	versions\125\artisan.exe one PyInstaller onedir build per release
//	versions\125\.healthy    written by Artisan once it got as far as the login
//	versions\130.partial\    a download in progress, never started
//	launcher.log             what the launcher did (kept small)
//
// The launcher only chooses which version to start and rolls back a version that does not start. Downloading and installing a new
// version is Artisan's job, done while it runs, into a folder of its own: nothing that runs is ever overwritten.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

const (
	versionsDir   = "versions"
	stateFile     = "current.json"
	healthyMark   = ".healthy"
	partialSuffix = ".partial"
	appExe        = "artisan.exe"
)

// State is current.json. Nil means "none" (JSON null or missing).
type State struct {
	Current  *string `json:"current"`
	Previous *string `json:"previous"`
	Blocked  *string `json:"blocked"`
}

func str(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

func ptr(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

// readState never fails: a missing or broken file reads as empty (the launcher then picks the newest version there is).
func readState(root string) State {
	var st State
	data, err := os.ReadFile(filepath.Join(root, stateFile))
	if err != nil {
		return State{}
	}
	if json.Unmarshal(data, &st) != nil {
		return State{}
	}
	return st
}

// writeState replaces current.json atomically (Artisan may read it at any time).
func writeState(root string, st State) error {
	data, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(root, stateFile+".*.tmp")
	if err != nil {
		return err
	}
	name := tmp.Name()
	if _, err := tmp.Write(append(data, '\n')); err != nil {
		_ = tmp.Close()
		_ = os.Remove(name)
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		_ = os.Remove(name)
		return err
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(name)
		return err
	}
	return os.Rename(name, filepath.Join(root, stateFile))
}

func exePath(root, version string) string {
	return filepath.Join(root, versionsDir, version, appExe)
}

// usable: a complete version folder with artisan.exe, not a download in progress, not a plain path trick.
func usable(root, version string) bool {
	if version == "" || strings.HasSuffix(version, partialSuffix) || strings.ContainsAny(version, `/\:`) || version == "." || version == ".." {
		return false
	}
	info, err := os.Stat(exePath(root, version))
	return err == nil && !info.IsDir()
}

func healthy(root, version string) bool {
	_, err := os.Stat(filepath.Join(root, versionsDir, version, healthyMark))
	return err == nil
}

var errNoVersion = errors.New("không tìm thấy bản Artisan nào trong thư mục versions")

// choose returns the version to start: current, else previous, else the highest numbered usable folder that is not blocked.
func choose(root string) (string, error) {
	st := readState(root)
	for _, candidate := range []string{str(st.Current), str(st.Previous)} {
		if usable(root, candidate) && candidate != str(st.Blocked) {
			return candidate, nil
		}
	}
	entries, err := os.ReadDir(filepath.Join(root, versionsDir))
	if err != nil {
		return "", fmt.Errorf("%w: %v", errNoVersion, err)
	}
	type numbered struct {
		name string
		n    int
	}
	var found []numbered
	for _, e := range entries {
		if !e.IsDir() || e.Name() == str(st.Blocked) || !usable(root, e.Name()) {
			continue
		}
		n, convErr := strconv.Atoi(e.Name())
		if convErr != nil {
			n = -1 // a folder with another name only if there is nothing numbered
		}
		found = append(found, numbered{e.Name(), n})
	}
	if len(found) == 0 {
		return "", errNoVersion
	}
	sort.Slice(found, func(i, j int) bool { return found[i].n > found[j].n })
	return found[0].name, nil
}

// afterExit decides what to do once the version started by this launcher has ended. A version that never got as far as the login
// (no .healthy) while it was the current one is rolled back: the previous version becomes current, the broken one is blocked (Artisan
// does not fetch it again) and the previous one is returned to be started once. "" means nothing to do.
func afterExit(root, version string) (string, error) {
	if healthy(root, version) {
		return "", nil
	}
	st := readState(root)
	if str(st.Current) != version { // somebody switched meanwhile (an update): not ours to judge
		return "", nil
	}
	previous := str(st.Previous)
	if previous == version || !usable(root, previous) {
		return "", nil
	}
	if err := writeState(root, State{Current: ptr(previous), Previous: nil, Blocked: ptr(version)}); err != nil {
		return "", err
	}
	return previous, nil
}
