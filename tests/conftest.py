import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def write_hb(tmp_path):
    hb = tmp_path / "hb"
    hb.mkdir()

    def _write(slug, *records):
        with open(hb / f"{slug}.jsonl", "a") as f:
            for ts, result in records:
                f.write(json.dumps({"ts": ts, "routine": slug, "result": result}) + "\n")
        return hb
    _write.dir = hb
    return _write
