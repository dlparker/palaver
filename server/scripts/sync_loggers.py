#!/usr/bin/env python3
"""
Script to find all logging.getLogger() calls in src/ and ensure they're in scripts/loggers.py.

Usage:
    uv run scripts/sync_loggers.py          # Check for missing loggers
    uv run scripts/sync_loggers.py --fix    # Update loggers.py automatically
"""
import re
import ast
from pathlib import Path
import argparse
import sys


def find_logger_names_in_src(src_path: Path) -> set[str]:
    """Find all logger names from logging.getLogger() calls in Python files."""
    logger_names = set()

    for py_file in src_path.rglob("*.py"):
        try:
            content = py_file.read_text()
            tree = ast.parse(content)

            for node in ast.walk(tree):
                # Look for logging.getLogger(...) calls
                if isinstance(node, ast.Call):
                    # Check if it's a getLogger call
                    is_get_logger = False
                    if isinstance(node.func, ast.Attribute):
                        if node.func.attr == "getLogger":
                            # Check if it's logging.getLogger
                            if isinstance(node.func.value, ast.Name) and node.func.value.id == "logging":
                                is_get_logger = True

                    if is_get_logger and node.args:
                        # Get the first argument
                        arg = node.args[0]
                        # Only include string literals (skip __name__, variables, etc.)
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            logger_names.add(arg.value)
        except Exception as e:
            print(f"Warning: Could not parse {py_file}: {e}", file=sys.stderr)

    return logger_names


def get_loggers_from_file(loggers_file: Path) -> list[str]:
    """Extract logger names from the get_loggers() function in loggers.py."""
    content = loggers_file.read_text()
    tree = ast.parse(content)

    logger_names = []

    for node in ast.walk(tree):
        # Look for logging.getLogger(...) calls inside get_loggers function
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "getLogger":
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "logging":
                        if node.args and isinstance(node.args[0], ast.Constant):
                            logger_names.append(node.args[0].value)

    return logger_names


def update_loggers_file(loggers_file: Path, logger_names: list[str]) -> None:
    """Update the get_loggers() function with the sorted list of logger names."""
    content = loggers_file.read_text()

    # Build the new get_loggers function
    sorted_names = sorted(logger_names)
    lines = ["def get_loggers():", "    res  = []"]
    for name in sorted_names:
        lines.append(f'    res.append(logging.getLogger("{name}"))')
    lines.append("    return res")
    lines.append("")  # Trailing newline

    new_function = "\n".join(lines)

    # Replace the get_loggers function
    pattern = r'def get_loggers\(\):.*?return res\n'
    new_content = re.sub(pattern, new_function + '\n', content, flags=re.DOTALL)

    loggers_file.write_text(new_content)


def main():
    parser = argparse.ArgumentParser(description="Sync logger names between src/ and scripts/loggers.py")
    parser.add_argument("--fix", action="store_true", help="Update loggers.py automatically")
    args = parser.parse_args()

    # Paths
    project_root = Path(__file__).parent.parent
    src_path = project_root / "src"
    loggers_file = project_root / "scripts" / "loggers.py"

    # Find all logger names in src
    src_loggers = find_logger_names_in_src(src_path)
    print(f"Found {len(src_loggers)} unique logger names in src/:")
    for name in sorted(src_loggers):
        print(f"  - {name}")
    print()

    # Get logger names from loggers.py
    file_loggers = set(get_loggers_from_file(loggers_file))
    print(f"Found {len(file_loggers)} logger names in scripts/loggers.py:")
    for name in sorted(file_loggers):
        print(f"  - {name}")
    print()

    # Find differences
    missing_in_file = src_loggers - file_loggers
    extra_in_file = file_loggers - src_loggers

    if missing_in_file:
        print(f"⚠️  Missing in loggers.py ({len(missing_in_file)}):")
        for name in sorted(missing_in_file):
            print(f"  - {name}")
        print()

    if extra_in_file:
        print(f"ℹ️  In loggers.py but not found in src/ ({len(extra_in_file)}):")
        for name in sorted(extra_in_file):
            print(f"  - {name}")
        print("  (These might be from deleted code or dynamically created loggers)")
        print()

    if not missing_in_file and not extra_in_file:
        print("✓ All logger names are in sync!")
        return 0

    if args.fix:
        # Merge: keep all loggers (src + existing)
        all_loggers = sorted(src_loggers | file_loggers)
        update_loggers_file(loggers_file, all_loggers)
        print(f"✓ Updated {loggers_file} with {len(all_loggers)} loggers")
        return 0
    else:
        print("Run with --fix to update loggers.py automatically")
        return 1


if __name__ == "__main__":
    sys.exit(main())
