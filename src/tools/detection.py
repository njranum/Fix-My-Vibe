"""
src/tools/detection.py
Three-layer AI tool detection logic.
Called by the Scanner agent to build a complete picture before the LLM reasons.
"""

from pathlib import Path
from .fs_tools import scan_directory, check_path_tools, check_vscode_extensions


def run_full_detection(project_path: str) -> dict:
    """
    Run all three detection layers and merge results.
    Layer 1: config file signatures
    Layer 2: PATH / shutil.which checks
    Layer 3: .vscode/extensions.json inspection
    Returns merged dict with all detected tools and their evidence.
    """
    layer1 = scan_directory(project_path)
    if "error" in layer1:
        return layer1

    layer2 = check_path_tools()
    layer3 = check_vscode_extensions(project_path)

    # Merge tool detections across all layers
    all_tools: dict[str, dict] = {}

    # From layer 1
    for tool in layer1.get("detected_tools", []):
        all_tools.setdefault(tool, {"evidence": []})
        all_tools[tool]["evidence"].extend(
            [f"config:{f}" for f in layer1.get("tool_files_found", {}).get(tool, [])]
        )

    # From layer 2
    for tool, cmd in layer2.get("path_tools", {}).items():
        all_tools.setdefault(tool, {"evidence": []})
        all_tools[tool]["evidence"].append(f"path:{cmd}")

    # From layer 3
    for tool in layer3.get("vscode_tools", []):
        all_tools.setdefault(tool, {"evidence": []})
        all_tools[tool]["evidence"].append("vscode:extension")

    # Recompute missing_configs for ALL detected tools (not just layer1 hits)
    from pathlib import Path as _Path
    _path = _Path(layer1["project_path"])
    _all_files = set()
    import os as _os
    for root, dirs, files in _os.walk(_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            _all_files.add(str(_Path(root, f).relative_to(_path)))

    from .fs_tools import TOOL_CONFIG_SIGNATURES
    missing_configs: dict[str, str] = {}
    for tool in all_tools:
        primary = TOOL_CONFIG_SIGNATURES.get(tool, [""])[0]
        if primary and primary not in _all_files and not (_path / primary).exists():
            missing_configs[tool] = primary

    return {
        "project_path": layer1["project_path"],
        "detected_tools": list(all_tools.keys()),
        "tool_evidence": all_tools,
        "tool_files_found": layer1.get("tool_files_found", {}),
        "detected_stack": layer1.get("detected_stack", []),
        "is_monorepo": layer1.get("is_monorepo", False),
        "detected_linters": layer1.get("detected_linters", []),
        "missing_configs": missing_configs,
        "security_issues": layer1.get("security_issues", []),
        "has_gitignore": layer1.get("has_gitignore", False),
        "gitignore_content": layer1.get("gitignore_content", ""),
        "total_files_scanned": layer1.get("total_files_scanned", 0),
        "subdirectory_count": layer1.get("subdirectory_count", 0),
        "path_tools": layer2.get("path_tools", {}),
        "vscode_tools": layer3.get("vscode_tools", []),
        "needs_user_prompt": len(all_tools) == 0,
    }
