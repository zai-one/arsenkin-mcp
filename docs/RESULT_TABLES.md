# Work with saved SEO results

Ask: “Show the supported Arsenkin profiles, help me prepare a small SEO check,
then turn its saved results into a table.”

`arsenkin_profile_catalog` lists every supported profile, its required fields,
defaults, limits and examples for SERP, clustering and the three Wordstat workflows.
Catalog discovery and `arsenkin_estimate` make no provider requests. Cost estimates
are configured policy units, not a live provider quote.

## A visible worker and budget

`arsenkin_local_diagnostics` explains your local queue, configured spending limits
and the last worker cycle. `not_seen` or `stale` suggests starting the worker with
the same configuration as the MCP server:

```sh
arsenkin-worker --config /ABSOLUTE/PATH/mcp.local.json
```

From a clone, use `uv run --frozen --extra standalone arsenkin-worker ...`.
Keep the worker running while jobs are queued. A recent cycle means the worker
has touched local state within 120 seconds; it does not prove successful provider
access. `arsenkin_status` is the separate live account check. Local diagnostics
show your own job counts and reservations, not another principal's job details.
They never alter budgets, approve a request or resubmit a task.

## Useful tables without paying again

`arsenkin_result_table` projects a selected part of a saved, owned result into
JSON rows or CSV. First use `arsenkin_get_result` to inspect the saved result.
Supply a JSON pointer **relative to its `result` field**:

| View | Select | Result |
|---|---|---|
| `serp_urls` | The provider's `repeatUrl` object | URL and its provider-reported occurrence count |
| `serp_hosts` | The provider's `repeatHost` object | Host and its provider-reported occurrence count |
| `clusters` | A list of cluster records with `clustered` and structured `words` fields | Cluster name, words, available frequencies and leading URL |

For the documented single-task SERP envelope:

```json
{
  "job_id": "YOUR_SAVED_JOB_ID",
  "view": "serp_urls",
  "result_pointer": "/result/result/repeatUrl",
  "format": "csv",
  "max_rows": 500
}
```

For a batch, select the appropriate group's saved result: for example,
`/groups/0/result/result/result/repeatUrl`. Inspect your result rather than assuming
every provider response has that path. Pointer segments escape `/` as `~1` and `~`
as `~0`. Each output row keeps its pointer for checking the original data.

SERP occurrence counts describe repeated URLs/hosts across the saved collection;
they are not positions, traffic or a newly crawled audit. Cluster views keep the
documented fields `clustered`, `words`, `wssumm`, `topurl`, `main`, `ws`, `ws_strict`,
`agregators`, `toponim` and `geo` when present. Missing fields stay null or empty
in CSV, and zero remains zero. Unknown record shapes are rejected; the original
`arsenkin_get_result` and JSONL export remain available for all other shapes.

The view is limited to 1,000 rows and 200,000 response bytes. `selected_view_complete`
refers only to the selected object/list. It never asserts that a provider task or a
whole batch is complete. A row/byte cap gives an explicit stop reason. CSV escapes
formula-like strings; configured secrets are masked before conversion. Business
data in a requested export is still business data.

Source field definitions: [Arsenkin SERP API](https://help.arsenkin.ru/api/api-top),
[clustering API](https://help.arsenkin.ru/api/clustering-dev).

## По-русски

Начните с `arsenkin_profile_catalog`: ассистент получит список профилей, обязательные
поля и примеры запросов. Оценка и каталог не обращаются к провайдеру.

`arsenkin_local_diagnostics` показывает вашу очередь, настройки бюджета и давность
последнего цикла worker. Если worker не виден или давно не работал, запустите
`arsenkin-worker` с тем же конфигом. Свежая отметка не означает, что API уже ответил.

После выполнения задания `arsenkin_result_table` делает таблицу из сохранённых
данных: повторяемость URL/доменов в выдаче либо записи кластеров. Это не запускает
платное задание повторно. Путь `result_pointer` выбирается внутри сохранённого
`result`; пример для обычного SERP-ответа приведён выше. Для кластеров выберите
массив записей с полями `clustered` и `words`. Неизвестный формат можно изучить
через исходный результат или прежний JSONL-экспорт.

Полнота относится только к выбранной таблице. Частоты не превращаются в позиции,
отсутствующие значения не подменяются нулями. В каждой строке остаётся путь к
исходным данным — удобно проверить вывод ассистента.
