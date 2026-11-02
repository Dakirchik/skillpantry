#!/usr/bin/env bash
# skillpantry: install on Linux and macOS. Options are passed to pantryctl.sh install.
cd "$(dirname "$0")" && exec bash ./pantryctl.sh install "$@"
