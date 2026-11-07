"""確認まで終えた架空環境を作る。人が画面でする操作を、同じ関数で順に行う。

`python -m agent.core.demo` で `KOUNIN_HOME` の架空環境を作り直す。手元環境は拒否する（INV21）。
"""

from pathlib import Path

from agent.core import ingest, inbox, links, month as months
from agent.core import pack as packs
from agent.core.workspace import ROOT, config, init_sample, reset_sample, resolve_home, users

FIXES = ROOT / "data" / "sample" / "fixes"


def ready(home: Path) -> None:
    reset_sample(home)
    init_sample(home)
    month = config(home)["current_month"]
    hr = next(user for user in users(home) if user["role"] == links.HR)
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
