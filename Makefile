.PHONY: setup test check-public seed app reset eval

# 依存を入れる。pre-commit のフックは git の共通領域に入る。
setup:
	uv sync
	uv run pre-commit install

test:
	uv run pytest -q

# git の管理下と、無視されていない新しいファイルを全部調べる。
check-public:
	git ls-files -z --cached --others --exclude-standard | xargs -0 uv run python scripts/check_public.py

# 架空の職員を作り直す。固定シードなので同じ結果になる。
seed:
	uv run python -m synth.generate

# 架空環境を開く。127.0.0.1 だけで待ち受ける。
app:
	KOUNIN_FAKE=$${KOUNIN_FAKE-1} uv run python -m ui.serve

# 架空環境を最初の状態へ戻す。手元環境は拒否する。
reset:
	uv run python -m agent.core.workspace reset

# 実モデルの評価。S3 で足す。
eval:
	uv run pytest -q -m live
