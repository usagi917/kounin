"""版の適用。基準日で一意に選び、空白か重複があれば止める（INV15）。登録日ではなく施行日で選ぶ。"""

from datetime import date

from engine.models import Strict


class VersionError(ValueError):
    """適用する版が一意に決まらない。理由を日本語の文にする。"""


class VersionRef(Strict):
    id: str
    effective_from: date


def applicable(versions: list[VersionRef], on: date) -> VersionRef:
    dates = [version.effective_from for version in versions]
    if duplicated := sorted({day for day in dates if dates.count(day) > 1}):
        raise VersionError(f"同じ施行日の版が2つあります: {duplicated[0].isoformat()}")
    started = [version for version in versions if version.effective_from <= on]
    if not started:
        raise VersionError(f"{on.isoformat()} に適用できる版がありません")
    return max(started, key=lambda version: version.effective_from)
