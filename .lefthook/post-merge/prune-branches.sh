#!/usr/bin/env bash
# lefthook finds a shared hook's script here, in .lefthook/<hook>/ at the root of the
# repository the config came from. The cleanup itself stays in .devtools/.
#
# git passes a post-merge hook a squash flag. The script takes none, so it is not handed on.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.devtools/prune-merged-branches.sh"
