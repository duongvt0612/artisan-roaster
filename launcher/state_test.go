package main

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// version makes versions/<name>/artisan.exe; script is its content (a shell script stands in for Artisan in the start tests).
func version(t *testing.T, root, name, script string, healthyToo bool) {
	t.Helper()
	dir := filepath.Join(root, versionsDir, name)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if script == "" {
		script = "#!/bin/sh\nexit 0\n"
	}
	if err := os.WriteFile(filepath.Join(dir, appExe), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	if healthyToo {
		if err := os.WriteFile(filepath.Join(dir, healthyMark), nil, 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func setState(t *testing.T, root string, st State) {
	t.Helper()
	if err := writeState(root, st); err != nil {
		t.Fatal(err)
	}
}

func TestChooseCurrent(t *testing.T) {
	root := t.TempDir()
	version(t, root, "120", "", true)
	version(t, root, "125", "", false)
	setState(t, root, State{Current: ptr("125"), Previous: ptr("120")})
	if got, err := choose(root); err != nil || got != "125" {
		t.Fatalf("choose = %q, %v", got, err)
	}
}

func TestChooseFallsBackWhenCurrentIsIncomplete(t *testing.T) {
	root := t.TempDir()
	version(t, root, "120", "", true)
	if err := os.MkdirAll(filepath.Join(root, versionsDir, "125"), 0o755); err != nil { // no artisan.exe
		t.Fatal(err)
	}
	setState(t, root, State{Current: ptr("125"), Previous: ptr("120")})
	if got, _ := choose(root); got != "120" {
		t.Fatalf("choose = %q", got)
	}
}

func TestChooseWithoutStateTakesTheHighestNumber(t *testing.T) {
	root := t.TempDir()
	version(t, root, "99", "", false)
	version(t, root, "120", "", false)
	version(t, root, "130.partial", "", false)
	version(t, root, "125", "", false)
	if err := os.WriteFile(filepath.Join(root, stateFile), []byte("{broken"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got, _ := choose(root); got != "125" {
		t.Fatalf("choose = %q", got)
	}
}

func TestChooseSkipsTheBlockedVersion(t *testing.T) {
	root := t.TempDir()
	version(t, root, "120", "", true)
	version(t, root, "125", "", false)
	setState(t, root, State{Current: ptr("125"), Previous: ptr("120"), Blocked: ptr("125")})
	if got, _ := choose(root); got != "120" {
		t.Fatalf("choose = %q", got)
	}
}

func TestChooseNothingThere(t *testing.T) {
	if _, err := choose(t.TempDir()); err == nil {
		t.Fatal("phải báo lỗi khi không có bản nào")
	}
}

func TestChooseRefusesPathTricks(t *testing.T) {
	root := t.TempDir()
	version(t, root, "120", "", true)
	setState(t, root, State{Current: ptr(`..\..\evil`), Previous: ptr("120")})
	if got, _ := choose(root); got != "120" {
		t.Fatalf("choose = %q", got)
	}
}

func TestAfterExitHealthyDoesNothing(t *testing.T) {
	root := t.TempDir()
	version(t, root, "120", "", true)
	version(t, root, "125", "", true)
	setState(t, root, State{Current: ptr("125"), Previous: ptr("120")})
	if got, err := afterExit(root, "125"); err != nil || got != "" {
		t.Fatalf("afterExit = %q, %v", got, err)
	}
}

func TestAfterExitRollsBackAVersionThatNeverStarted(t *testing.T) {
	root := t.TempDir()
	version(t, root, "120", "", true)
	version(t, root, "125", "", false)
	setState(t, root, State{Current: ptr("125"), Previous: ptr("120")})
	got, err := afterExit(root, "125")
	if err != nil || got != "120" {
		t.Fatalf("afterExit = %q, %v", got, err)
	}
	st := readState(root)
	if str(st.Current) != "120" || st.Previous != nil || str(st.Blocked) != "125" {
		t.Fatalf("state = %q %v %q", str(st.Current), st.Previous, str(st.Blocked))
	}
}

func TestAfterExitWithoutFallbackDoesNothing(t *testing.T) {
	root := t.TempDir()
	version(t, root, "125", "", false)
	setState(t, root, State{Current: ptr("125")})
	if got, _ := afterExit(root, "125"); got != "" {
		t.Fatalf("afterExit = %q", got)
	}
}

// Artisan switched current.json to a new update while it ran: the version that just ended is not judged.
func TestAfterExitLeavesASwitchAlone(t *testing.T) {
	root := t.TempDir()
	version(t, root, "120", "", false)
	version(t, root, "125", "", false)
	setState(t, root, State{Current: ptr("125"), Previous: ptr("120")})
	if got, _ := afterExit(root, "120"); got != "" {
		t.Fatalf("afterExit = %q", got)
	}
}

// The file Artisan writes (src/erp/updater.py write_current) is read the same way.
func TestReadsTheFileArtisanWrites(t *testing.T) {
	root := t.TempDir()
	content := "{\n  \"current\": \"125\",\n  \"previous\": \"120\",\n  \"blocked\": null\n}\n"
	if err := os.WriteFile(filepath.Join(root, stateFile), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	st := readState(root)
	if str(st.Current) != "125" || str(st.Previous) != "120" || st.Blocked != nil {
		t.Fatalf("state = %+v", st)
	}
}

func TestSplitArgs(t *testing.T) {
	if pid, rest := splitArgs([]string{"--wait-pid", "42", "C:/r.alog"}); pid != 42 || len(rest) != 1 || rest[0] != "C:/r.alog" {
		t.Fatalf("pid=%d rest=%v", pid, rest)
	}
	if pid, rest := splitArgs([]string{"C:/r.alog"}); pid != 0 || len(rest) != 1 {
		t.Fatalf("pid=%d rest=%v", pid, rest)
	}
}

// start passes the launcher folder and its own path to Artisan (the self-update needs both) and the arguments through.
func TestStartPassesTheEnvironment(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell script stands in for artisan.exe")
	}
	root := t.TempDir()
	out := filepath.Join(root, "seen.txt")
	version(t, root, "125", "#!/bin/sh\necho \"$LVG_LAUNCHER_ROOT|$LVG_LAUNCHER_EXE|$1|$(pwd)\" > \""+out+"\"\n", false)
	if err := start(root, "/x/Artisan.exe", "125", []string{"file.alog"}); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	dir, _ := filepath.EvalSymlinks(filepath.Join(root, versionsDir, "125"))
	want := root + "|/x/Artisan.exe|file.alog|" + dir + "\n"
	if string(data) != want {
		t.Fatalf("seen %q want %q", data, want)
	}
}

// A version whose exe cannot be started at all (not executable here; a broken exe on Windows) is rolled back too.
func TestRunRollsBackAVersionThatCannotStart(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell script stands in for artisan.exe")
	}
	root := t.TempDir()
	out := filepath.Join(root, "ran.txt")
	version(t, root, "120", "#!/bin/sh\necho 120 > \""+out+"\"\n", true)
	version(t, root, "125", "", false)
	if err := os.Chmod(exePath(root, "125"), 0o644); err != nil { // cannot be executed
		t.Fatal(err)
	}
	setState(t, root, State{Current: ptr("125"), Previous: ptr("120")})
	old := os.Args
	os.Args = []string{"Artisan.exe"}
	defer func() { os.Args = old }()
	if err := run(filepath.Join(root, "Artisan.exe")); err != nil {
		t.Fatalf("run = %v", err)
	}
	if data, _ := os.ReadFile(out); string(data) != "120\n" {
		t.Fatalf("fallback did not run: %q", data)
	}
	if st := readState(root); str(st.Current) != "120" || str(st.Blocked) != "125" {
		t.Fatalf("state = %+v", st)
	}
}
