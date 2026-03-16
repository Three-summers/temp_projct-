from pathlib import Path
import sys


# 让测试在未安装包的情况下直接导入 src 目录。
SRC_PATH = Path(__file__).resolve().parents[1] / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))
