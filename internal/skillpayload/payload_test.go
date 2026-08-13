package skillpayload

import (
	"os"
	"path/filepath"
	"testing"
)

func TestHashIncludesSupportingFiles(t *testing.T) {
	root := t.TempDir()
	write(t, filepath.Join(root, "SKILL.md"), "# Skill\n")
	write(t, filepath.Join(root, "references", "guide.md"), "first\n")
	before, err := Hash(root)
	if err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(root, "references", "guide.md"), "second\n")
	after, err := Hash(root)
	if err != nil {
		t.Fatal(err)
	}
	if before == after {
		t.Fatal("supporting file change did not change payload hash")
	}
}

func TestHashIgnoresEvaluationFiles(t *testing.T) {
	root := t.TempDir()
	write(t, filepath.Join(root, "SKILL.md"), "# Skill\n")
	before, err := Hash(root)
	if err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(root, "evals", "evals.json"), "{}\n")
	after, err := Hash(root)
	if err != nil {
		t.Fatal(err)
	}
	if before != after {
		t.Fatal("evaluation-only file changed payload hash")
	}
}

func write(t *testing.T, path, contents string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(contents), 0o644); err != nil {
		t.Fatal(err)
	}
}
