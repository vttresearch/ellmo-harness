import os
import platform


def list_files(path: str = None, depth: int = 0) -> str:
    """List files and directories inside the given path.

    Args:
        path (str, optional): Directory to list. Defaults to current working directory.
        depth (int, optional): How many levels of subdirectories to include.
            0 = only the given directory (no recursion, current behavior).
            1 = include immediate subdirectories' contents, etc.
    """
    if path is None:
        path = os.getcwd()

    if not os.path.exists(path):
        os_name = platform.system()
        return f"Path does not exist on this system ({os_name}): {path}"

    if not os.path.isdir(path):
        return f"Path is not a directory: {path}"

    lines = [f"Files in {path} (depth={depth}):"]

    def _walk(current_path: str, current_depth: int) -> None:
        try:
            entries = os.listdir(current_path)
        except OSError as e:
            lines.append(f"Error listing files in directory {current_path}: {e}")
            return

        # If too many files in a non-root folder, crop output
        if current_path != path and len(entries) > 100:
            lines.append(
                f"The folder {current_path} contains too many files to list as a subfolder. "
                f'Call "list_files" with this folder as the root if you need to view them.'
            )
            return

        if not entries:
            lines.append(f"The directory {current_path} is empty.")
            return

        indent = "  " * current_depth

        for entry in sorted(entries):
            full_path = os.path.join(current_path, entry)
            if os.path.isdir(full_path):
                lines.append(f"{indent}[DIR]  {entry}")
                # Recurse into subdirectory if we haven't reached max depth
                if current_depth < depth:
                    _walk(full_path, current_depth + 1)
            else:
                lines.append(f"{indent}[FILE] {entry}")

    _walk(path, 0)

    return "\n".join(lines)
