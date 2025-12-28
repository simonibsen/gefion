#!/bin/bash
#
# Install git hooks for g2 development
#
# Usage: ./scripts/hooks/install.sh
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$(git rev-parse --git-dir)/hooks"

echo "Installing git hooks..."
echo ""

for hook in commit-msg pre-commit pre-push prepare-commit-msg; do
    if [ -f "$SCRIPT_DIR/$hook" ]; then
        cp "$SCRIPT_DIR/$hook" "$HOOKS_DIR/$hook"
        chmod +x "$HOOKS_DIR/$hook"
        echo "  ✓ Installed $hook"
    fi
done

echo ""
echo "Done! Git hooks installed to $HOOKS_DIR"
echo ""
echo "Hooks installed:"
echo "  - commit-msg: Rejects AI attribution, shows dev rules reminder"
echo "  - pre-commit: Checks new files for observability imports"
echo "  - pre-push: Runs test suite before push"
echo "  - prepare-commit-msg: Shows dev rules reminder"
echo ""
