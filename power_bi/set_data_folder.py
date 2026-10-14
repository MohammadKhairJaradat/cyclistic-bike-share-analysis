"""Set the Power BI data path for the current copy of this project."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
model_path = ROOT / "power_bi/Cyclistic.SemanticModel/model.bim"
if not model_path.exists():
    raise FileNotFoundError("The JSON semantic model was not found.")
if sys.argv[1:] not in [[], ["--portable"]]:
    raise SystemExit("Usage: python power_bi/set_data_folder.py [--portable]")
data_folder = ROOT / "data/processed"
required_files = ["trip_summary.csv", "station_summary.csv", "cleaning_log.csv",
                  "dim_month.csv", "dim_rider.csv", "dim_bike.csv"]
missing = [name for name in required_files if not (data_folder / name).exists()]
if missing:
    raise FileNotFoundError(f"Missing dashboard inputs: {missing}")
folder_path = "C:/Cyclistic/data/processed" if "--portable" in sys.argv else data_folder.as_posix()
model = json.loads(model_path.read_text(encoding="utf-8"))
parameter = next(item for item in model["model"]["expressions"] if item["name"] == "DataFolder")
parameter["expression"] = json.dumps(folder_path, ensure_ascii=False) + ' meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'
model_path.write_text(json.dumps(model, indent=2), encoding="utf-8")
print("Portable path restored." if "--portable" in sys.argv else "DataFolder is configured. Open the Power BI project and refresh.")
