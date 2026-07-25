import os
import sys

# 让 tests/ 能 import app（venv 未 -e 安装时）
sys.path.insert(0, os.path.dirname(__file__))
