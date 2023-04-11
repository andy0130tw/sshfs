#!/bin/bash
set -e
pip install --user pre-commit
pre-commit run --all-files --show-diff-on-failure
