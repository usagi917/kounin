.PHONY: setup test check-public seed app demo demo-csv reset eval

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

# 確認まで終えた架空環境を別の場所に作って開く。make app の状態には触らない。
demo:
	KOUNIN_HOME=var/demo uv run python -m agent.core.demo
	KOUNIN_HOME=var/demo KOUNIN_FAKE=1 uv run python -m ui.serve --port 8030

# デモで置く CSV を使う順に並べる。見本の写しなので、見本を作り直したらもう一度実行する。
demo-csv:
	rm -rf var/demo-csv
	mkdir -p var/demo-csv/1_誤りあり var/demo-csv/2_正しいデータ var/demo-csv/3_確認で置く
	cp data/sample/2026-10/*.csv var/demo-csv/2_正しいデータ/
	cp data/sample/2026-10/*.csv var/demo-csv/1_誤りあり/
	rm var/demo-csv/1_誤りあり/所属.csv
	cp data/sample/fixes/所属-誤りの見本.csv var/demo-csv/1_誤りあり/
	cp data/sample/fixes/所属.csv var/demo-csv/3_確認で置く/
	@echo "作りました: var/demo-csv"

# 架空環境を最初の状態へ戻す。手元環境は拒否する。
reset:
	uv run python -m agent.core.workspace reset

# 実モデルの評価。S3 で足す。
eval:
	uv run pytest -q -m live
