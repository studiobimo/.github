#!/usr/bin/env bash
# See branch-name.sh for why this is a wrapper.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.devtools/check-pr-size.sh"
