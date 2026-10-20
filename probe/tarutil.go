package main

import (
	"archive/tar"
	"compress/gzip"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"
)

func runPut(args []string, in io.Reader, out io.Writer, errOut io.Writer) int {
	flags := flag.NewFlagSet("put", flag.ContinueOnError)
	flags.SetOutput(errOut)
	dest := flags.String("dest", "", "destination directory")
	useGzip := flags.Bool("gzip", false, "input is gzip compressed")
	if err := flags.Parse(args); err != nil {
		return exitUsage
	}
	if *dest == "" {
		fmt.Fprintln(errOut, "ferry-probe: put requires --dest")
		return exitUsage
	}
	if err := os.MkdirAll(*dest, 0o755); err != nil {
		fmt.Fprintf(errOut, "ferry-probe: cannot create dest: %v\n", err)
		return exitUsage
	}

	var reader io.Reader = in
	if *useGzip {
		gz, err := gzip.NewReader(in)
		if err != nil {
			fmt.Fprintf(errOut, "ferry-probe: invalid gzip stream: %v\n", err)
			return exitUsage
		}
		defer gz.Close()
		reader = gz
	}
	files, bytes, err := extractTar(reader, *dest)
	if err != nil {
		fmt.Fprintf(errOut, "ferry-probe: extract failed: %v\n", err)
		return exitStepFailed
	}
	_ = json.NewEncoder(out).Encode(map[string]any{
		"ok":    true,
		"files": files,
		"bytes": bytes,
	})
	return exitOK
}

func extractTar(reader io.Reader, dest string) (int, int64, error) {
	destAbs, err := filepath.Abs(dest)
	if err != nil {
		return 0, 0, err
	}
	if err := os.MkdirAll(destAbs, 0o755); err != nil {
		return 0, 0, err
	}
	tr := tar.NewReader(reader)
	files := 0
	var bytes int64
	for {
		header, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return files, bytes, err
		}
		name := path.Clean("/" + filepath.ToSlash(header.Name))
		target := filepath.Join(destAbs, filepath.FromSlash(name))
		if !within(destAbs, target) {
			return files, bytes, fmt.Errorf("unsafe tar entry rejected: %s", header.Name)
		}
		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return files, bytes, err
			}
		case tar.TypeReg, tar.TypeRegA:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return files, bytes, err
			}
			file, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o644)
			if err != nil {
				return files, bytes, err
			}
			written, copyErr := io.Copy(file, tr)
			closeErr := file.Close()
			if copyErr != nil {
				return files, bytes, copyErr
			}
			if closeErr != nil {
				return files, bytes, closeErr
			}
			bytes += written
			files++
		case tar.TypeSymlink:
			if filepath.IsAbs(header.Linkname) {
				return files, bytes, fmt.Errorf("absolute symlink rejected: %s", header.Name)
			}
			resolved := filepath.Clean(filepath.Join(filepath.Dir(target), filepath.FromSlash(header.Linkname)))
			if !within(destAbs, resolved) {
				return files, bytes, fmt.Errorf("symlink escapes destination: %s", header.Name)
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return files, bytes, err
			}
			_ = os.Remove(target)
			if err := os.Symlink(header.Linkname, target); err != nil {
				return files, bytes, err
			}
			files++
		case tar.TypeLink:
			linkName := path.Clean("/" + filepath.ToSlash(header.Linkname))
			linkTarget := filepath.Join(destAbs, filepath.FromSlash(linkName))
			if !within(destAbs, linkTarget) {
				return files, bytes, fmt.Errorf("hardlink escapes destination: %s", header.Name)
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return files, bytes, err
			}
			_ = os.Remove(target)
			if err := os.Link(linkTarget, target); err != nil {
				return files, bytes, err
			}
			files++
		}
	}
	return files, bytes, nil
}

func within(root, target string) bool {
	relative, err := filepath.Rel(root, target)
	if err != nil {
		return false
	}
	return relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

func runGet(args []string, out io.Writer, errOut io.Writer) int {
	flags := flag.NewFlagSet("get", flag.ContinueOnError)
	flags.SetOutput(errOut)
	root := flags.String("root", "", "root directory to archive")
	paths := flags.String("paths", "", "comma separated relative paths")
	useGzip := flags.Bool("gzip", false, "gzip the output")
	if err := flags.Parse(args); err != nil {
		return exitUsage
	}
	if *root == "" {
		fmt.Fprintln(errOut, "ferry-probe: get requires --root")
		return exitUsage
	}

	var writer io.Writer = out
	var gz *gzip.Writer
	if *useGzip {
		gz = gzip.NewWriter(out)
		writer = gz
	}
	tw := tar.NewWriter(writer)

	selected := []string{}
	if *paths != "" {
		for _, item := range strings.Split(*paths, ",") {
			if trimmed := strings.TrimSpace(item); trimmed != "" {
				selected = append(selected, trimmed)
			}
		}
	}

	var walkErr error
	if len(selected) == 0 {
		walkErr = addTree(tw, *root, ".")
	} else {
		for _, item := range selected {
			if err := addTree(tw, *root, item); err != nil {
				walkErr = err
				break
			}
		}
	}
	if closeErr := tw.Close(); walkErr == nil {
		walkErr = closeErr
	}
	if gz != nil {
		if closeErr := gz.Close(); walkErr == nil {
			walkErr = closeErr
		}
	}
	if walkErr != nil {
		fmt.Fprintf(errOut, "ferry-probe: get failed: %v\n", walkErr)
		return exitStepFailed
	}
	return exitOK
}

func addTree(tw *tar.Writer, root, relative string) error {
	base := filepath.Join(root, filepath.FromSlash(relative))
	return filepath.Walk(base, func(current string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(root, current)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		header, err := tar.FileInfoHeader(info, "")
		if err != nil {
			return err
		}
		header.Name = filepath.ToSlash(rel)
		header.Uid = 0
		header.Gid = 0
		header.Uname = ""
		header.Gname = ""
		if err := tw.WriteHeader(header); err != nil {
			return err
		}
		if info.Mode().IsRegular() {
			file, err := os.Open(current)
			if err != nil {
				return err
			}
			_, copyErr := io.Copy(tw, file)
			closeErr := file.Close()
			if copyErr != nil {
				return copyErr
			}
			return closeErr
		}
		return nil
	})
}
