"""ルールパックの版。承認済みの版は上書きせず、施行日で選ぶ（INV5、INV15）。"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from engine.models import Coefficients, NormalScope, Period, Rule
from engine.versions import VersionRef, applicable

Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
RULE_BLOCK = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


class PackError(ValueError):
    """パックが読めない、または使えない。理由を日本語の文にする。"""


@dataclass(frozen=True)
class PeriodInfo:
    period: Period          # 基準日までに切り詰めた考課の対象期間
    name: str
    payment_date: date


@dataclass(frozen=True)
class Pack:
    version: str
    path: Path
    effective_from: date
    summary: str
    settings: dict
    coefficients_raw: dict
    coefficients: Coefficients
    rules: tuple[Rule, ...]
    increments: dict
    digest: str

    @property
    def scope(self) -> NormalScope:
        return NormalScope.model_validate(self.settings["normal_scope"])

    @property
    def ranks(self) -> list[str]:
        return list(self.coefficients.rank_coefficients)

    @property
    def groups(self) -> list[str]:
        return list(self.settings["groups"])

    def period_on(self, on: date) -> PeriodInfo:
        for item in self.settings["periods"]:
            start, end = (date.fromisoformat(text) for text in item["evaluation"].split(".."))
            if start <= on <= end:
                return PeriodInfo(Period(id=item["id"], start=start, end=end), item["name"],
                                  date.fromisoformat(str(item["payment_date"])))
        raise PackError(f"{on.isoformat()} を含む考課の期がパックにありません")


def _yaml(path: Path):
    return yaml.load(path.read_text("utf-8"), Loader=Loader)


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file() and item.name != "meta.json":
            sha.update(item.relative_to(path).as_posix().encode("utf-8"))
            sha.update(item.read_bytes())
    return sha.hexdigest()


def _rules(path: Path, org_id: str) -> tuple[Rule, ...]:
    rules = []
    for item in sorted((path / "rules").glob("R*.md")):
        block = RULE_BLOCK.search(item.read_text("utf-8"))
        if block is None:
            raise PackError(f"{item.name} に判定条件がありません")
        rule = Rule.model_validate(yaml.safe_load(block.group(1)))
        if rule.org_id != org_id:
            raise PackError(f"{item.name} は別の法人のルールです")     # INV4
        if rule.status == "approved":                                    # INV3
            rules.append(rule)
    return tuple(rules)


@lru_cache(maxsize=16)
def _load(path_text: str, stamp: float) -> Pack:
    path = Path(path_text)
    meta = yaml.safe_load((path / "meta.json").read_text("utf-8"))
    settings = _yaml(path / "pack.yaml")
    raw = _yaml(path / "coefficients.yaml")
    return Pack(version=path.name, path=path, effective_from=date.fromisoformat(meta["effective_from"]),
                summary=meta.get("summary", ""), settings=settings, coefficients_raw=raw,
                coefficients=Coefficients.model_validate(raw), rules=_rules(path, settings["org_id"]),
                increments=_yaml(path / "raises.yaml")["increments"], digest=_digest(path))


def load(home: Path, version: str) -> Pack:
    path = home / "pack" / version
    if not (path / "meta.json").exists():
        raise PackError(f"版 {version} がありません")
    return _load(str(path), (path / "meta.json").stat().st_mtime)


def versions(home: Path) -> list[VersionRef]:
    refs = []
    for path in (home / "pack").iterdir():
        if path.name.isdigit() and (path / "meta.json").exists():
            meta = yaml.safe_load((path / "meta.json").read_text("utf-8"))
            refs.append(VersionRef(id=path.name, effective_from=date.fromisoformat(meta["effective_from"])))
    return sorted(refs, key=lambda ref: int(ref.id))


def for_date(home: Path, on: date) -> Pack:
    return load(home, applicable(versions(home), on).id)
