package herdr

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

type Observer struct {
	executable, outputDir, statusPath string
	WorkspaceID, WorkspaceLabel       string
	panes                             map[string]string
	activePane                        string
}

func RequireEnvironment() error {
	if os.Getenv("HERDR_ENV") != "1" {
		return fmt.Errorf("live skill evaluations require a Herdr-managed pane (HERDR_ENV=1)")
	}
	if _, err := exec.LookPath("herdr"); err != nil {
		return fmt.Errorf("live skill evaluations require herdr in PATH")
	}
	return nil
}

func Start(skillName, outputDir, cwd string) (*Observer, error) {
	if err := RequireEnvironment(); err != nil {
		return nil, err
	}
	executable, _ := exec.LookPath("herdr")
	label := "eval:" + safeLabel(skillName) + ":" + safeLabel(filepath.Base(outputDir))
	observer := &Observer{executable: executable, outputDir: outputDir, statusPath: filepath.Join(outputDir, "herdr", "status.log"), WorkspaceLabel: label, panes: map[string]string{}}
	created, err := observer.call("workspace", "create", "--cwd", cwd, "--label", label, "--no-focus")
	if err != nil {
		return nil, err
	}
	workspace, _ := created["workspace"].(map[string]any)
	rootPane, _ := created["root_pane"].(map[string]any)
	observer.WorkspaceID, _ = workspace["workspace_id"].(string)
	root, _ := rootPane["pane_id"].(string)
	if observer.WorkspaceID == "" || root == "" {
		return nil, fmt.Errorf("Herdr returned an unexpected workspace response")
	}
	bottom, err := observer.split(root, "down", cwd)
	if err != nil {
		return nil, err
	}
	topRight, err := observer.split(root, "right", cwd)
	if err != nil {
		return nil, err
	}
	bottomRight, err := observer.split(bottom, "right", cwd)
	if err != nil {
		return nil, err
	}
	observer.panes = map[string]string{"coordinator": root, "control": topRight, "with_skill": bottom, "judge_results": bottomRight}
	for _, role := range []string{"coordinator", "control", "with_skill", "judge_results"} {
		pane := observer.panes[role]
		label := strings.ReplaceAll(role, "_", "-")
		if _, err := observer.call("pane", "rename", pane, label); err != nil {
			return nil, err
		}
	}
	if err := os.MkdirAll(filepath.Dir(observer.statusPath), 0o755); err != nil {
		return nil, err
	}
	initial := fmt.Sprintf("Skill Eval · %s\nWorkspace · %s\nArtifacts · %s\nExecution · controls first, then with-skill\n", skillName, label, outputDir)
	if err := os.WriteFile(observer.statusPath, []byte(initial), 0o666); err != nil {
		return nil, err
	}
	if _, err := observer.call("pane", "run", root, "tail -f "+shellQuote(observer.statusPath)); err != nil {
		return nil, err
	}
	if _, err := observer.call("workspace", "focus", observer.WorkspaceID); err != nil {
		return nil, err
	}
	return observer, nil
}

func (observer *Observer) split(pane, direction, cwd string) (string, error) {
	result, err := observer.call("pane", "split", pane, "--direction", direction, "--ratio", "0.5", "--cwd", cwd, "--no-focus")
	if err != nil {
		return "", err
	}
	value, _ := result["pane"].(map[string]any)
	id, _ := value["pane_id"].(string)
	if id == "" {
		return "", fmt.Errorf("Herdr returned an unexpected pane response")
	}
	return id, nil
}

func (observer *Observer) Begin(role, title, tracePath, stderrPath string) error {
	pane := observer.panes[role]
	if pane == "" {
		return fmt.Errorf("unsupported Herdr pane role: %s", role)
	}
	for _, path := range []string{tracePath, stderrPath} {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return err
		}
		file, err := os.OpenFile(path, os.O_CREATE, 0o666)
		if err != nil {
			return err
		}
		file.Close()
	}
	command := "printf '%s\\n' " + shellQuote(title) + "; tail -F " + shellQuote(tracePath) + " " + shellQuote(stderrPath)
	if _, err := observer.call("pane", "run", pane, command); err != nil {
		return err
	}
	observer.activePane = pane
	return observer.Note("START · " + title)
}

func (observer *Observer) End(title string, exitCode int) error {
	if observer.activePane != "" {
		if _, err := observer.call("pane", "send-keys", observer.activePane, "ctrl+c"); err != nil {
			return err
		}
		observer.activePane = ""
	}
	return observer.Note(fmt.Sprintf("END · %s · exit %d", title, exitCode))
}

func (observer *Observer) CancelActive() {
	if observer.activePane != "" {
		_, _ = observer.call("pane", "send-keys", observer.activePane, "ctrl+c")
		_ = observer.Note("Cancellation requested for active model process")
	}
}

func (observer *Observer) Finish(status, summary, artifactPath string) error {
	if err := observer.Note("Artifacts · " + artifactPath); err != nil {
		return err
	}
	if err := observer.Note("FINAL · " + status + " · " + summary); err != nil {
		return err
	}
	summaryPath := filepath.Join(observer.outputDir, "herdr", "summary.txt")
	content := fmt.Sprintf("Skill Eval · %s\n\n%s\n\nArtifacts\n%s\n", status, summary, artifactPath)
	if err := os.WriteFile(summaryPath, []byte(content), 0o666); err != nil {
		return err
	}
	if _, err := observer.call("pane", "run", observer.panes["judge_results"], "cat "+shellQuote(summaryPath)); err != nil {
		return err
	}
	if _, err := observer.call("workspace", "rename", observer.WorkspaceID, "["+status+"] "+observer.WorkspaceLabel); err != nil {
		return err
	}
	sound := "request"
	if status == "completed" {
		sound = "done"
	}
	_, err := observer.call("notification", "show", "Skill eval "+status, "--body", summary, "--position", "top-right", "--sound", sound)
	return err
}

func (observer *Observer) Note(message string) error {
	file, err := os.OpenFile(observer.statusPath, os.O_APPEND|os.O_WRONLY, 0o666)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = fmt.Fprintf(file, "%s · %s\n", time.Now().UTC().Format("15:04:05"), message)
	return err
}

func (observer *Observer) call(arguments ...string) (map[string]any, error) {
	command := exec.Command(observer.executable, arguments...)
	output, err := command.Output()
	if err != nil {
		detail := ""
		if exit, ok := err.(*exec.ExitError); ok {
			detail = strings.TrimSpace(string(exit.Stderr))
		}
		return nil, fmt.Errorf("Herdr %s failed: %s", strings.Join(arguments, " "), detail)
	}
	var envelope struct {
		Result map[string]any `json:"result"`
	}
	if len(strings.TrimSpace(string(output))) == 0 {
		return map[string]any{}, nil
	}
	if json.Unmarshal(output, &envelope) != nil || envelope.Result == nil {
		return nil, fmt.Errorf("Herdr returned invalid JSON for %s", strings.Join(arguments, " "))
	}
	return envelope.Result, nil
}

func safeLabel(value string) string {
	value = regexp.MustCompile(`[^a-zA-Z0-9._:-]+`).ReplaceAllString(strings.TrimSpace(value), "-")
	value = strings.Trim(value, "-")
	if value == "" {
		return "run"
	}
	return value
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
}
