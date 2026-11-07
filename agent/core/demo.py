"""見本のデータを入れた架空環境を作る。人が画面でする操作を、同じ関数で順に行う。

`python -m agent.core.demo` で `KOUNIN_HOME` の架空環境を作り直す。手元環境は拒否する（INV21）。
"""

from pathlib import Path

from agent.core import ingest, inbox, links, month as months
from agent.core import pack as packs
from agent.core.workspace import ROOT, SAMPLE_MONTH, config, reset_sample, resolve_home, users

SAMPLE = ROOT / "data" / "sample"
FIXES = SAMPLE / "fixes"


def sample_files() -> dict[str, bytes]:
    return {kind: (SAMPLE / SAMPLE_MONTH / f"{kind}.csv").read_bytes() for kind in ingest.UPLOADABLE}


def human(home: Path) -> dict:
    return next(user for user in users(home) if user["role"] == links.HR)


def load_sample(home: Path) -> None:
    """見本の5つのファイルを、人事担当が画面で渡すのと同じ順で入れる。"""
    month = config(home)["current_month"]
    files = sample_files()
    results = ingest.check_first(files, packs.for_date(home, months.base_date(month)))
    problems = [text for result in results.values() for text in result.errors]
    if problems:
        raise RuntimeError(f"見本のファイルが検査に通りません：{problems[0]}")
    ingest.confirm_first(home, month, ingest.stage_first(home, month, files), human(home))


def ready(home: Path) -> None:
    reset_sample(home)
    load_sample(home)
    month = config(home)["current_month"]
    hr = human(home)
    months.run(home, month, hr)
    token = ingest.stage(home, month, ingest.SEGMENTS, (FIXES / "所属.csv").read_bytes())
    ingest.confirm(home, month, ingest.SEGMENTS, token, hr)
    months.run(home, month, None)
    ranks = packs.for_date(home, months.base_date(month)).ranks
    rank = ranks[len(ranks) // 2]
    for contact in links.board(home, month, months.latest(home, month))["contacts"]:
        if not contact["bundles"]:
            continue
        token, _ = links.issue(home, month, contact["id"], [], hr)
        for bundle in links.view(home, token, month)["bundles"]:
            for person in bundle["people"]:
                if bundle["kind"] == inbox.RANK:
                    links.answer_rank(home, token, month, person["item_id"], rank)
                else:
                    links.answer_judge(home, token, month, person["item_id"], person["options"][0])
    run = months.run(home, month, None)[0]
    if run["stopped"]:
        raise RuntimeError(f"確認が{run['stopped']}人残りました")


if __name__ == "__main__":
    ready(resolve_home())
