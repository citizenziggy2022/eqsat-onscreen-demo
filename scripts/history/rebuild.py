#!/usr/bin/env python3
"""Rebuild state/history.db from the raw JSONL archives alone (recovery path).

Usage: python3 scripts/history/rebuild.py <rawdir> <db_path>
(rawdir = raw/ inside a clone of the eqsat-history repo, branch history)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import rebuild_db

if __name__ == "__main__":
    n = rebuild_db(sys.argv[1], sys.argv[2])
    print(f"observations: {n}")
