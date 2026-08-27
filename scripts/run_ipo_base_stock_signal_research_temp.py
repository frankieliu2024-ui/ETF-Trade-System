import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_ipo_base_stock_signals_temp as study

study.STOCKS["300750"]["benchmark"] = "000001.SS"
study.STOCKS["300750"]["benchmark_name"] = "上证指数（广义A股市场控制）"

if __name__ == "__main__":
    raise SystemExit(study.main())
