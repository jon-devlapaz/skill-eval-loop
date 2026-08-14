package simpleeval

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadTasks(t *testing.T) {
	tasks, err := LoadTasks("testdata/tasks.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	if len(tasks) != 2 || tasks[0].ID != "unsafe-candidate" || tasks[1].ID != "write-decision" {
		t.Fatalf("unexpected tasks: %+v", tasks)
	}
	if len(tasks[0].Graders) != 3 || tasks[0].Graders[2].Type != "rubric" {
		t.Fatalf("unexpected graders: %+v", tasks[0].Graders)
	}
	if !strings.Contains(string(tasks[0].Raw), `"tags"`) || !strings.Contains(string(tasks[1].Raw), `"fixture"`) {
		t.Fatal("unknown task metadata was not retained")
	}
	if !strings.Contains(string(tasks[0].Graders[0].Raw), `"pattern"`) {
		t.Fatal("grader source was not retained")
	}
}

func TestLoadTasksReportsTaskAndField(t *testing.T) {
	tests := []struct {
		name string
		line string
		want string
	}{
		{"missing prompt", `{"id":"broken","graders":[{"type":"regex","pattern":"ok"}]}`, `task "broken" field prompt`},
		{"bad pattern", `{"id":"broken","prompt":"x","graders":[{"type":"regex","pattern":"["}]}`, `task "broken" field graders[0] field pattern`},
		{"escaping path", `{"id":"broken","prompt":"x","graders":[{"type":"file_exists","path":"../secret"}]}`, `task "broken" field graders[0] field path`},
		{"missing expected", `{"id":"broken","prompt":"x","graders":[{"type":"json_equal","path":"result.json"}]}`, `task "broken" field graders[0] field expected`},
		{"bad rubric", `{"id":"broken","prompt":"x","graders":[{"type":"rubric","text":""}]}`, `task "broken" field graders[0] field text`},
		{"unsupported type", `{"id":"broken","prompt":"x","graders":[{"type":"shell"}]}`, `task "broken" field graders[0] field type`},
	}
	for _, current := range tests {
		t.Run(current.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "tasks.jsonl")
			if err := os.WriteFile(path, []byte(current.line+"\n"), 0o600); err != nil {
				t.Fatal(err)
			}
			_, err := LoadTasks(path)
			if err == nil || !strings.Contains(err.Error(), current.want) {
				t.Fatalf("error = %v, want text %q", err, current.want)
			}
		})
	}
}

func TestLoadTasksRejectsDuplicateIDs(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tasks.jsonl")
	data := "{\"id\":\"same\",\"prompt\":\"one\",\"graders\":[{\"type\":\"regex\",\"pattern\":\"x\"}]}\n" +
		"{\"id\":\"same\",\"prompt\":\"two\",\"graders\":[{\"type\":\"regex\",\"pattern\":\"x\"}]}\n"
	if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := LoadTasks(path)
	if err == nil || !strings.Contains(err.Error(), `task "same" field id`) {
		t.Fatalf("unexpected error: %v", err)
	}
}
