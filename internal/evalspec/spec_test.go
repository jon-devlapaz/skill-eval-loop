package evalspec

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestSafeRelativePathRejectsTraversalAndSymlinkEscape(t *testing.T) {
	root := t.TempDir()
	if _, err := SafeRelativePath(root, "../outside", "fixture"); err == nil {
		t.Fatal("traversal accepted")
	}
	if runtime.GOOS == "windows" {
		t.Skip("migration target is macOS/Linux")
	}
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(root, "escape")); err != nil {
		t.Fatal(err)
	}
	if _, err := SafeRelativePath(root, "escape/file", "fixture"); err == nil || !strings.Contains(err.Error(), "escapes") {
		t.Fatalf("error = %v", err)
	}
}

func TestSafeRelativePathAllowsMissingPathBelowRoot(t *testing.T) {
	root := t.TempDir()
	path, err := SafeRelativePath(root, "missing/file.json", "artifact")
	if err != nil {
		t.Fatal(err)
	}
	resolvedRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(resolvedRoot, "missing", "file.json")
	if path != want {
		t.Fatalf("path = %q, want %q", path, want)
	}
}

func TestEveryDeterministicResponseGrader(t *testing.T) {
	tests := []struct {
		grader   map[string]any
		response string
		want     bool
	}{
		{map[string]any{"type": "response_contains", "value": "needle"}, "a needle", true},
		{map[string]any{"type": "response_not_contains", "value": "bad"}, "good", true},
		{map[string]any{"type": "response_regex", "pattern": "o.e"}, "one", true},
		{map[string]any{"type": "markdown_table_column_regex", "column": "Result", "pattern": "pass"}, "| Result |\n| --- |\n| pass |", true},
	}
	for _, test := range tests {
		got, err := GradeResponse(test.response, test.grader)
		if err != nil || got != test.want {
			t.Fatalf("grader=%v got=%v err=%v", test.grader, got, err)
		}
	}
}

func TestCanonicalSHA256MatchesFrozenPython(t *testing.T) {
	tests := []struct {
		value any
		want  string
	}{
		{map[string]any{"a": json.Number("1"), "b": "é"}, "09ad9fd2fb648cb2f62141215828ea00a62c299db05d20aa9ade2f527a301cc6"},
		{map[string]any{"n": json.Number("1.0")}, "3b6b06ecd1c968c8e738e0f11c4bb361fca80a9a694de22fe66a05286afbd081"},
		{map[string]any{"n": json.Number("1e-07")}, "ff7a1315299260617fe404199e54e6d976a0b03e47da54fccec073c2fa48ff5c"},
		{map[string]any{"n": json.Number("1e20")}, "ec663afd6a17a8746b0225837f32e4c9247c72d3a18f5e8118bc8f82606d7002"},
		{map[string]any{"n": json.Number("-0.0")}, "a8a313cade05001e69f7ddb5db01e1e2d06fb8f6913ab492cc4506d4e65d465a"},
		{map[string]any{"nested": []any{true, nil, map[string]any{"z": "<>&"}}}, "8c0f4992147f1221c24812ba9d179e0179f653e1e161c2ebbf5fcb0996519399"},
	}
	for _, test := range tests {
		got, err := CanonicalSHA256(test.value)
		if err != nil || got != test.want {
			t.Fatalf("value=%v got=%s want=%s err=%v", test.value, got, test.want, err)
		}
	}
}

func TestGradeCaseCoversDeterministicWorkspaceAndExternalGraders(t *testing.T) {
	workspace := t.TempDir()
	if err := os.WriteFile(filepath.Join(workspace, "value.json"), []byte("{\"ok\":true}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	graders := []map[string]any{
		{"name": "contains", "type": "response_contains", "value": "ok"},
		{"name": "file", "type": "file_exists", "path": "value.json"},
		{"name": "json", "type": "json_exact", "path": "value.json", "expected": map[string]any{"ok": true}},
		{"name": "judge", "type": "model_rubric"},
	}
	result, err := GradeCase(workspace, "ok", graders, map[string]map[string]any{"judge": {"passed": true, "evidence": "specific"}})
	if err != nil {
		t.Fatal(err)
	}
	if result.Summary.Passed != 4 || result.Summary.PassRate != 1 {
		t.Fatalf("result=%#v", result)
	}
}

func TestGradeCaseRejectsWorkspaceSymlinkEscape(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("migration target is macOS/Linux")
	}
	workspace := t.TempDir()
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "secret"), []byte("secret"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(workspace, "escape")); err != nil {
		t.Fatal(err)
	}
	_, err := GradeCase(workspace, "", []map[string]any{{"name": "file", "type": "file_exists", "path": "escape/secret"}}, nil)
	if err == nil || !strings.Contains(err.Error(), "escapes") {
		t.Fatalf("error=%v", err)
	}
}
