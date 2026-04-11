#!/bin/bash
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
source "$PROJECT_DIR/venv/bin/activate"
export PYTHONPATH="$PROJECT_DIR"
pytest -v "$PROJECT_DIR/_tests/"