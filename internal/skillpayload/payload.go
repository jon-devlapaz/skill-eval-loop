package skillpayload

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Hash returns the digest of the deployable skill payload. Files used only to
// evaluate or test the skill are excluded because they are not installed.
func Hash(root string) (string, error) {
	files, err := Files(root)
	if err != nil {
		return "", err
	}
	hash := sha256.New()
	for _, path := range files {
		relative, _ := filepath.Rel(root, path)
		hash.Write([]byte(filepath.ToSlash(relative)))
		hash.Write([]byte{0})
		info, _ := os.Stat(path)
		if info.Mode()&0o111 != 0 {
			hash.Write([]byte("x"))
		} else {
			hash.Write([]byte("-"))
		}
		hash.Write([]byte{0})
		data, err := os.ReadFile(path)
		if err != nil {
			return "", err
		}
		hash.Write(data)
		hash.Write([]byte{0})
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

// Files returns the regular files that make up the deployable skill payload.
func Files(root string) ([]string, error) {
	files := []string{}
	err := filepath.Walk(root, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("symlinked skill payload entry is not allowed: %s", path)
		}
		if path == root {
			return nil
		}
		relative, _ := filepath.Rel(root, path)
		for _, component := range strings.Split(filepath.ToSlash(relative), "/") {
			if map[string]bool{"evals": true, "tests": true, "__pycache__": true, ".DS_Store": true}[component] {
				if info.IsDir() {
					return filepath.SkipDir
				}
				return nil
			}
		}
		if info.Mode().IsRegular() && filepath.Ext(path) != ".pyc" {
			files = append(files, path)
		}
		return nil
	})
	sort.Strings(files)
	return files, err
}
