# R001 途中の異動は在籍の長い職場で数える

## 条文
対象期間の途中で異動した職員は、在籍の長い職場で数える。新卒のローテーションの在籍は数えない。応援が4か月以上の人は、まだ決めていない。

## 判定条件
```yaml
id: R001
org_id: sample
version: 1
status: approved
effective: {from: 2026-summer, to: null}
when:
  has_transfer: true
exclude_segments:
  - {kind: 新卒ローテ}
then:
  group_basis: longest_tenure
undecided:
  - {id: U001, kind: 応援, min_months: 4, judgment: J001}
sources:
  - {type: regulation, quote: "対象期間の途中で異動した職員は、在籍の長い職場で数える。新卒のローテーションの在籍は数えない。"}
text: 対象期間の途中で異動した職員は、在籍の長い職場で数える。新卒のローテーションの在籍は数えない。
```

## 出典
架空の賞与規程の第5条。

## テスト
`../tests/R001.yaml`
