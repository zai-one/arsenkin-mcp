# Clustering parameters (`clustering` profile)

Source: https://help.arsenkin.ru/api/clustering-dev

| Field | Values | Notes |
|---|---|---|
| `queries` | list, ≤70,000 here | dedupe and lowercase first |
| `se` | 1 Яндекс, 2 Google | |
| `region` | Yandex region id (213 Москва) / Google region id | |
| `group` | `hard` (default) or `soft` | hard = every pair shares URLs (tighter clusters); soft = shares with main query |
| `count` | 2–10 (default 3) | how many common URLs make queries one cluster; higher = stricter |
| `depth` | 10 (default), 20, 30 | SERP depth compared |
| `stoplist` | domains, e.g. ["yandex.ru","avito.ru"] | exclude aggregators/marketplaces |
| `ws` | [] or any of `base`, `quoted`, `overal`, `exact` | extra frequency collection (costs more) |
| `main` | true/false | exclude home pages |

Defaults that usually work: `group=hard, count=3, depth=10`, stoplist of marketplaces for e-commerce.
Cost grows with number of queries and with `ws`; always estimate first.
