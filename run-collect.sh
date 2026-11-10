#!/bin/bash
# run-collect.sh — 抓取一轮，不入库之外的副作用
set -e
cd /data/workspace/geointel
exec python3 collect.py "$@"
