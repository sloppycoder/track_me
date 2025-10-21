import fnmatch
import os
import sys


def get_all_file_extensions(directory, exclude_patterns=[]):
    exts = set()
    for root, dirs, files in os.walk(directory):
        relative_root = os.path.relpath(root, start_dir)
        if relative_root != "." and any(
            fnmatch.fnmatch(relative_root, pattern) for pattern in exclude_patterns
        ):
            # print(f"skipping {relative_root}")
            continue

        for file in files:
            if any(fnmatch.fnmatch(file, pattern) for pattern in exclude_patterns):
                continue
            ext = os.path.splitext(file)[1]
            # print(f"checking {root}/{file} -> {ext}")
            if ext:
                exts.add(ext)
    return sorted(exts)


if __name__ == "__main__":
    start_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    file_exts = get_all_file_extensions(start_dir, [".*"])
    print(file_exts)
