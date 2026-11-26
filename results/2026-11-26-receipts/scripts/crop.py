"""Cut the part of each screenshot that holds the number, for docs/site/receipts/.

    uvx --with pillow python -I crop.py SCREENS_DIR OUT_DIR

The crop changes no pixel inside the box. The full screenshots stay in SCREENS_DIR.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

PHOENIX_TREE = (400, 95, 760, 310)
PHOENIX_COST = (402, 95, 700, 380)
MLFLOW_ROW = (1100, 130, 1420, 210)
CROPS = {
    "ghost-phoenix-trace": PHOENIX_TREE,
    "double-phoenix-cost": PHOENIX_COST,
    "double-jaeger-genai": (440, 100, 870, 130),
    "double-mlflow-traces": MLFLOW_ROW,
    "cache-phoenix-cost": PHOENIX_COST,
    "phantom-jaeger-genai": (360, 320, 960, 610),
    "vanish-mlflow-traces": MLFLOW_ROW,
    "falseok-phoenix-trace": (762, 165, 1000, 198),
    "falseok-mlflow-traces": MLFLOW_ROW,
}


def main(screens: str, out: str) -> int:
    Path(out).mkdir(parents=True, exist_ok=True)
    for name, box in CROPS.items():
        with Image.open(Path(screens) / f"{name}.png") as im:
            im.crop(box).convert("RGB").save(Path(out) / f"{name}.png", optimize=True)
        print(name, box)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
