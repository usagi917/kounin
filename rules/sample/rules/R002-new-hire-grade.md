# R002 入職4か月未満はB評価

## 条文
考課対象期間の末日時点で入職から4か月未満の職員は、人事考課の結果にかかわらず成績「B」として賞与を計算する。

## 判定条件
```yaml
id: R002
org_id: sample
version: 1
status: approved
effective: {from: 2026-summer, to: null}
when:
  tenure_months_at_eval_end: "< 4"
then:
  grade: B
sources:
  - {type: regulation, quote: "考課対象期間の末日時点で入職から4か月未満の職員は、人事考課の結果にかかわらず成績「B」として賞与を計算する。"}
text: 考課対象期間の末日時点で入職から4か月未満の職員は、人事考課の結果にかかわらず成績「B」として賞与を計算する。
```

## 出典
架空の賞与規程の第4条。

## テスト
`../tests/R002.yaml`
