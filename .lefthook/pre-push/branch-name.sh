#!/usr/bin/env bash
# lefthook finds a shared hook's script here, in .lefthook/<hook>/ at the root of the
# repository the config came from. The check itself stays in .devtools/, where ci-pr
# runs it from.
#
# git passes a pre-push hook the remote's name and URL. The check takes a branch name
# as its argument, so none of that is handed on.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.devtools/check-branch-name.sh"
