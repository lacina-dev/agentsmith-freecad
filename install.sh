#!/usr/bin/env bash
# Install the AgentSmith addon into the user's FreeCAD Mod directory as a symlink,
# so edits in this repository go live on the next addon reload / FreeCAD start.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/freecad_bridge"
LINK_NAME="AgentSmith"
LEGACY_LINK_NAMES=("CodexBridge" "ModelSmith")

if [[ ! -f "$SOURCE_DIR/InitGui.py" ]]; then
    echo "ERROR: $SOURCE_DIR does not look like the addon (InitGui.py missing)." >&2
    exit 1
fi

# FreeCAD >= 1.0 uses a versioned data dir (e.g. ~/.local/share/FreeCAD/v1-1/Mod),
# older releases used ~/.local/share/FreeCAD/Mod or ~/.FreeCAD/Mod.
candidates=()
for versioned in "$HOME"/.local/share/FreeCAD/v*/; do
    [[ -d "$versioned" ]] && candidates+=("${versioned}Mod")
done
candidates+=("$HOME/.local/share/FreeCAD/Mod" "$HOME/.FreeCAD/Mod")

installed=0
for mod_dir in "${candidates[@]}"; do
    parent="$(dirname "$mod_dir")"
    [[ -d "$parent" ]] || continue
    mkdir -p "$mod_dir"
    # Remove legacy-named symlinks pointing at this addon, otherwise FreeCAD would
    # load the workbench twice (once per link name).
    for legacy in "${LEGACY_LINK_NAMES[@]}"; do
        legacy_target="$mod_dir/$legacy"
        if [[ -L "$legacy_target" && "$(readlink -f "$legacy_target")" == "$(readlink -f "$SOURCE_DIR")" ]]; then
            rm "$legacy_target"
            echo "Removed legacy symlink: $legacy_target"
        fi
    done
    target="$mod_dir/$LINK_NAME"
    if [[ -L "$target" ]]; then
        current="$(readlink -f "$target")"
        if [[ "$current" == "$(readlink -f "$SOURCE_DIR")" ]]; then
            echo "OK (already linked): $target"
            installed=1
            continue
        fi
        echo "Updating stale symlink: $target (was $current)"
        rm "$target"
    elif [[ -e "$target" ]]; then
        echo "SKIP: $target exists and is not a symlink — remove it manually if you want the linked install." >&2
        continue
    fi
    ln -s "$SOURCE_DIR" "$target"
    echo "Linked: $target -> $SOURCE_DIR"
    installed=1
done

if [[ "$installed" == 0 ]]; then
    echo "ERROR: no FreeCAD user data directory found. Start FreeCAD once, then re-run." >&2
    exit 1
fi
echo "Done. Restart FreeCAD (or reload the addon) to pick up changes."
