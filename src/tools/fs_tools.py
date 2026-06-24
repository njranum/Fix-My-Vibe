"""
src/tools/fs_tools.py
Raw filesystem tools for the Scanner and Executor agents.
All file I/O in agent logic goes through here — never raw open() calls.
"""

import os
import shutil
import json
from pathlib import Path


TOOL_CONFIG_SIGNATURES: dict[str, list[str]] = {
    "claude_code": ["CLAUDE.md", ".claude/settings.json", "claude_desktop_config.json"],
    "cursor":      [".cursorrules", ".cursor/rules", ".cursorignore", ".cursorindexingignore"],
    "copilot":     [".github/copilot-instructions.md", ".copilot", ".copilotignore"],
    "windsurf":    [".windsurfrc", ".windsurf/rules.md"],
    "aider":       [".aider.conf.yml", ".aiderignore", "aider.conf.yml"],
    "cline":       [".clinerules", ".cline/settings.json"],
    "continue":    [".continue/config.json", ".continuerc.json"],
}

STACK_SIGNATURES: dict[str, list[str]] = {
    "python":     ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "poetry.lock"],
    "node":       ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
    "nextjs":     ["next.config.js", "next.config.ts", "next.config.mjs"],
    "django":     ["manage.py"],
    "react":      ["src/App.jsx", "src/App.tsx"],
    "typescript": ["tsconfig.json"],
    "rust":       ["Cargo.toml", "Cargo.lock"],
    "go":         ["go.mod", "go.sum"],
    "docker":     ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
    "monorepo":   ["pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json"],
}

# Stack entries that require content inspection (filename alone is too generic)
CONTENT_STACK_SIGNALS: dict[str, tuple[str, list[str]]] = {
    "fastapi": ("requirements.txt", ["fastapi", "FastAPI"]),
    "flask":   ("requirements.txt", ["flask", "Flask"]),
    "vite":    ("package.json",     ['"vite"', '"@vitejs']),
}

LINTER_SIGNATURES: dict[str, list[str]] = {
    "python": ["ruff.toml", ".ruff.toml", "pyproject.toml", ".flake8", ".pylintrc"],
    "node":   [".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml",
               "eslint.config.js", "biome.json"],
    "all":    [".prettierrc", ".prettierrc.json", "prettier.config.js",
               ".editorconfig", ".pre-commit-config.yaml", "pre-commit-config.yaml"],
}


def scan_directory(project_path: str) -> dict:
    """
    Layer 1 + security scan: walk the directory, detect tools and stack
    from config file signatures, flag security issues.
    """
    path = Path(project_path).resolve()
    if not path.exists():
        return {"error": f"Path does not exist: {project_path}"}
    if not path.is_dir():
        return {"error": f"Path is not a directory: {project_path}"}

    # Walk the project tree. We include hidden dirs that are known to hold AI tool
    # configs (.github, .cursor, .vscode, etc.) and skip only irrelevant ones.
    _SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".next",
                  ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "venv", ".venv"}
    all_files: set[str] = set()
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        rel_root = Path(root).relative_to(path)
        if len(rel_root.parts) > 4:
            dirs.clear()
            continue
        for f in files:
            all_files.add(str(Path(root, f).relative_to(path)))

    detected_tools: list[str] = []
    tool_files_found: dict[str, list[str]] = {}
    for tool, signatures in TOOL_CONFIG_SIGNATURES.items():
        found = [s for s in signatures if s in all_files or (path / s).exists()]
        if found:
            detected_tools.append(tool)
            tool_files_found[tool] = found

    detected_stack: list[str] = []
    for stack, signatures in STACK_SIGNATURES.items():
        if any(s in all_files or (path / s).exists() for s in signatures):
            detected_stack.append(stack)

    # Content-based stack detection for cases where the filename alone is too generic
    for stack_name, (check_file, keywords) in CONTENT_STACK_SIGNALS.items():
        if stack_name not in detected_stack:
            candidate = path / check_file
            if candidate.exists():
                try:
                    content = candidate.read_text(encoding="utf-8", errors="ignore")
                    if any(kw in content for kw in keywords):
                        detected_stack.append(stack_name)
                except Exception:
                    pass

    detected_linters: list[str] = []
    for _stack, sigs in LINTER_SIGNATURES.items():
        detected_linters.extend(s for s in sigs if s in all_files or (path / s).exists())

    # Security scan
    gitignore_content = ""
    has_gitignore = ".gitignore" in all_files
    if has_gitignore:
        try:
            gitignore_content = (path / ".gitignore").read_text()
        except Exception:
            pass

    security_issues: list[dict] = []
    env_files = [f for f in all_files if f == ".env" or f.startswith(".env.") or "/.env" in f]
    for env_file in env_files:
        if not _is_gitignored(env_file, gitignore_content):
            security_issues.append({
                "type": "exposed_env",
                "file": env_file,
                "severity": "high",
                "description": f"{env_file} present but not in .gitignore — secrets may leak to AI context window",
            })

    # Check for private key and certificate files anywhere in the tree
    sensitive_patterns = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer")
    sensitive_names = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials.json"}
    for f in all_files:
        fname = Path(f).name
        if fname in sensitive_names or any(fname.endswith(p) for p in sensitive_patterns):
            if not _is_gitignored(f, gitignore_content):
                security_issues.append({
                    "type": "exposed_credential",
                    "file": f,
                    "severity": "critical",
                    "description": f"{f} looks like a private key or credential file and is not gitignored",
                })

    if "cursor" in detected_tools and ".cursorignore" not in all_files:
        security_issues.append({
            "type": "missing_cursorignore",
            "severity": "high",
            "description": ".cursorrules detected but no .cursorignore — Cursor reads .env files by default",
        })

    missing_configs: dict[str, str] = {}
    for tool in detected_tools:
        primary = TOOL_CONFIG_SIGNATURES[tool][0]
        if primary not in all_files and not (path / primary).exists():
            missing_configs[tool] = primary

    is_monorepo = "monorepo" in detected_stack
    subdirs = [d for d in path.iterdir() if d.is_dir() and not d.name.startswith(".")]

    return {
        "project_path": str(path),
        "detected_tools": detected_tools,
        "tool_files_found": tool_files_found,
        "detected_stack": [s for s in detected_stack if s != "monorepo"],
        "is_monorepo": is_monorepo,
        "detected_linters": detected_linters,
        "missing_configs": missing_configs,
        "security_issues": security_issues,
        "has_gitignore": has_gitignore,
        "gitignore_content": gitignore_content,
        "total_files_scanned": len(all_files),
        "subdirectory_count": len(subdirs),
    }


def _is_gitignored(filename: str, gitignore_content: str) -> bool:
    """Check if a file is covered by any pattern in .gitignore content."""
    name = Path(filename).name
    for raw_line in gitignore_content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Exact match on full path or filename
        if line == filename or line == name:
            return True
        # Glob: *.ext covers any file with that extension
        if line.startswith("*.") and name.endswith(line[1:]):
            return True
        # Prefix glob: .env.* covers .env.local, .env.production, etc.
        if line.endswith("*") and (filename.startswith(line[:-1]) or name.startswith(line[:-1])):
            return True
        # Directory match: .env inside a dir like secrets/.env
        if "/" not in line and ("/" + line) in ("/" + filename):
            return True
    return False


def check_path_tools() -> dict:
    """Layer 2: Check which AI coding tools are installed on PATH."""
    cli_names: dict[str, list[str]] = {
        "claude_code": ["claude"],
        "cursor":      ["cursor"],
        "aider":       ["aider"],
        "copilot":     [],  # no standalone CLI — detected via config files and VS Code extensions
        "windsurf":    ["windsurf"],
        "cline":       [],  # VS Code extension only
        "continue":    [],  # VS Code extension only
    }
    path_tools: dict[str, str] = {}
    for tool, cmds in cli_names.items():
        for cmd in cmds:
            if shutil.which(cmd):
                path_tools[tool] = cmd
                break
    return {"path_tools": path_tools}


def check_vscode_extensions(project_path: str) -> dict:
    """
    Layer 3: Check .vscode/extensions.json for AI tool extension IDs.
    Returns a list of detected tools from VS Code extension recommendations.
    """
    ext_file = Path(project_path) / ".vscode" / "extensions.json"
    if not ext_file.exists():
        return {"vscode_tools": [], "source": "no .vscode/extensions.json"}

    try:
        data = json.loads(ext_file.read_text())
        recommendations = data.get("recommendations", [])
    except Exception as e:
        return {"vscode_tools": [], "error": str(e)}

    extension_map = {
        "github.copilot":           "copilot",
        "github.copilot-chat":      "copilot",
        "cursor.cursor":            "cursor",
        "codeium.windsurf":         "windsurf",
        "anthropic.claude":         "claude_code",
        "continue.continue":        "continue",
        "saoudrizwan.claude-dev":   "cline",
    }

    vscode_tools: list[str] = []
    for ext_id in recommendations:
        tool = extension_map.get(ext_id.lower())
        if tool and tool not in vscode_tools:
            vscode_tools.append(tool)

    return {"vscode_tools": vscode_tools, "extensions_found": recommendations}


_AI_CONTEXT_FILES = frozenset({
    "CLAUDE.md", ".cursorrules", ".clinerules",
    ".github/copilot-instructions.md", ".copilot",
    ".windsurfrc", ".windsurf/rules.md",
    ".aider.conf.yml", "aider.conf.yml",
    ".continue/config.json", ".continuerc.json",
})


def read_existing_context_file(project_path: str, filename: str) -> dict:
    """Read and audit an existing AI tool context file."""
    filepath = Path(project_path) / filename
    if not filepath.exists():
        return {"exists": False}

    try:
        content = filepath.read_text(encoding="utf-8")
        lines = content.splitlines()
        estimated_tokens = len(content) // 4
        is_ai_context = filename in _AI_CONTEXT_FILES or filename.endswith(".md")
        return {
            "exists": True,
            "filename": filename,
            "line_count": len(lines),
            "word_count": len(content.split()),
            "estimated_tokens": estimated_tokens,
            "has_project_overview": any(k in content.lower() for k in ["overview", "project", "about"]),
            "has_commands_section": any(k in content.lower() for k in ["npm", "pip", "make", "run", "test", "build"]),
            "has_prohibitions": "do not" in content.lower() or "don't" in content.lower(),
            "quality_concerns": _audit_context_file(content, estimated_tokens) if is_ai_context else [],
            "content_preview": content[:500],
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}


def _audit_context_file(content: str, token_count: int) -> list[str]:
    concerns: list[str] = []
    if token_count > 2000:
        concerns.append(f"Very long ({token_count} tokens) — consider splitting into layered files")
    if token_count < 100:
        concerns.append("Very short — likely missing key sections")
    if "todo" in content.lower() or "fixme" in content.lower():
        concerns.append("Contains TODO/FIXME markers — incomplete sections")
    if content.count("DO NOT") + content.count("don't") + content.count("never") < 1:
        concerns.append("No explicit prohibition section — add DO NOT rules for common mistakes")
    if not any(k in content.lower() for k in ["test", "build", "run", "install"]):
        concerns.append("Missing commands section — agent won't know how to run tests or build")
    return concerns


def infer_project_conventions(project_path: str) -> dict:
    """
    Deeply infer project conventions from file structure and content.
    Used by the Planner to generate tailored config files.
    """
    import re as _re
    path = Path(project_path).resolve()
    conventions: dict = {
        "test_command": None,
        "build_command": None,
        "lint_command": None,
        "type_check_command": None,
        "package_manager": None,
        "python_version": None,
        "formatting": None,
        "pre_commit": False,
        "naming_conventions": {},
        "key_directories": [],
        "readme_summary": None,
    }

    # Package manager
    if (path / "pnpm-lock.yaml").exists():
        conventions["package_manager"] = "pnpm"
    elif (path / "yarn.lock").exists():
        conventions["package_manager"] = "yarn"
    elif (path / "package-lock.json").exists():
        conventions["package_manager"] = "npm"

    # Python version from .python-version
    pyver_file = path / ".python-version"
    if pyver_file.exists():
        try:
            conventions["python_version"] = pyver_file.read_text().strip()
        except Exception:
            pass

    # pyproject.toml — rich source of conventions
    pyproject = path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text()
            if "pytest" in content:
                conventions["test_command"] = "pytest"
            if "[tool.ruff]" in content or 'ruff' in content:
                conventions["lint_command"] = "ruff check ."
                conventions["formatting"] = "ruff format ."
            elif "[tool.black]" in content or "black" in content:
                conventions["formatting"] = "black ."
            if "[tool.mypy]" in content:
                conventions["type_check_command"] = "mypy ."
            elif "[tool.pyright]" in content:
                conventions["type_check_command"] = "pyright"
            m = _re.search(r'python_requires\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                conventions["python_version"] = m.group(1)
        except Exception:
            pass

    # Dedicated config files for Python tooling
    for cfg_file, tool_cmd in [
        ("pytest.ini", "pytest"),
        ("setup.cfg", "pytest"),
        (".flake8", "flake8"),
        ("mypy.ini", "mypy ."),
        ("ruff.toml", "ruff check ."),
        (".ruff.toml", "ruff check ."),
    ]:
        if (path / cfg_file).exists():
            if "pytest" in tool_cmd and not conventions["test_command"]:
                conventions["test_command"] = "pytest"
            elif "flake8" in tool_cmd and not conventions["lint_command"]:
                conventions["lint_command"] = "flake8"
            elif "ruff" in tool_cmd and not conventions["lint_command"]:
                conventions["lint_command"] = "ruff check ."
                if not conventions["formatting"]:
                    conventions["formatting"] = "ruff format ."
            elif "mypy" in tool_cmd and not conventions["type_check_command"]:
                conventions["type_check_command"] = "mypy ."

    # Makefile — parse targets for common commands
    makefile = path / "Makefile"
    if makefile.exists():
        try:
            mk = makefile.read_text()
            make_targets = _re.findall(r'^([a-zA-Z][a-zA-Z0-9_-]*):', mk, _re.MULTILINE)
            if not conventions["test_command"] and "test" in make_targets:
                conventions["test_command"] = "make test"
            if not conventions["build_command"] and "build" in make_targets:
                conventions["build_command"] = "make build"
            if not conventions["lint_command"] and "lint" in make_targets:
                conventions["lint_command"] = "make lint"
        except Exception:
            pass

    # package.json scripts
    pkg_json = path / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            scripts = pkg.get("scripts", {})
            pm = conventions["package_manager"] or "npm"
            if not conventions["test_command"] and "test" in scripts:
                conventions["test_command"] = f"{pm} test"
            if not conventions["build_command"] and "build" in scripts:
                conventions["build_command"] = f"{pm} run build"
            if not conventions["lint_command"] and "lint" in scripts:
                conventions["lint_command"] = f"{pm} run lint"
            if not conventions["type_check_command"] and "typecheck" in scripts:
                conventions["type_check_command"] = f"{pm} run typecheck"
        except Exception:
            pass

    # Pre-commit hooks
    if (path / ".pre-commit-config.yaml").exists():
        conventions["pre_commit"] = True

    # Key directories
    key_dirs = ["src", "tests", "test", "docs", "scripts", "api", "lib",
                "components", "pages", "app", "pkg", "cmd", "internal"]
    conventions["key_directories"] = [d for d in key_dirs if (path / d).is_dir()]

    # Naming conventions — check Python file stems
    py_files = list(path.glob("**/*.py"))[:30]
    if py_files:
        snake_count = sum(1 for f in py_files if "_" in f.stem)
        if snake_count > len(py_files) * 0.6:
            conventions["naming_conventions"]["python_files"] = "snake_case"

    # README summary — first non-empty paragraph
    for readme_name in ("README.md", "README.rst", "README.txt", "README"):
        readme = path / readme_name
        if readme.exists():
            try:
                lines = readme.read_text(encoding="utf-8", errors="ignore").splitlines()
                # Skip heading lines, grab first substantive paragraph
                para_lines = []
                for line in lines:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or stripped.startswith("=") or stripped.startswith("-"):
                        if para_lines:
                            break
                        continue
                    # Strip markdown badges and HTML
                    if stripped.startswith("[![") or stripped.startswith("<"):
                        continue
                    para_lines.append(stripped)
                    if len(" ".join(para_lines)) > 300:
                        break
                if para_lines:
                    conventions["readme_summary"] = " ".join(para_lines)[:400]
            except Exception:
                pass
            break

    return conventions


def write_file(project_path: str, relative_path: str, content: str) -> dict:
    """Safe file write with backup. Called by Executor only after user confirmation."""
    target = Path(project_path) / relative_path
    if not _is_safe_path(Path(project_path), target):
        return {"error": f"Path traversal blocked: {relative_path}"}

    target.parent.mkdir(parents=True, exist_ok=True)
    backed_up = False
    if target.exists():
        backup_path = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(target, backup_path)
        backed_up = True

    target.write_text(content, encoding="utf-8")
    return {
        "written": str(target),
        "backed_up": backed_up,
        "backup_path": str(target.with_suffix(target.suffix + ".bak")) if backed_up else None,
        "size_bytes": len(content.encode("utf-8")),
    }


def verify_file(project_path: str, relative_path: str, expected_sections: list[str]) -> dict:
    """Verify a written file contains expected sections. Called by Verifier agent."""
    target = Path(project_path) / relative_path
    if not target.exists():
        return {"verified": False, "reason": "File does not exist"}
    content = target.read_text(encoding="utf-8")
    missing = [s for s in expected_sections if s.lower() not in content.lower()]
    return {
        "verified": len(missing) == 0,
        "file": str(target),
        "size_bytes": len(content.encode("utf-8")),
        "missing_sections": missing,
    }


def _is_safe_path(base: Path, target: Path) -> bool:
    """Ensure target is inside base — blocks path traversal."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False
