#!/usr/bin/env python3
"""Standalone entry point for auto-research MCP server."""
import sys
import os

# Ensure the project root is on sys.path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from src.server import main
main()
