"""正規化した JSON の SHA-256。項目の順序に依らず同じ値になる。"""

import hashlib
import json
from typing import Any


def stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
