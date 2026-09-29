#!/bin/zsh
# Double-click to start local-flow dictation (hold Right-Option to dictate).
cd "$(dirname "$0")"
exec .venv/bin/python flow.py --ollama
