# Review Mode Data Contract

## Semantic Columns

- `shop_name`: required shop display name.
- `profile_result`: required review conclusion column.
- `priority`: required introduction priority column.
- `owner`: required current owner column.
- `shop_url`: optional supplied shop or advertising URL.
- `category`: optional target category; unresolved category boundaries produce pending review.
- `phone`: optional public phone display field; numeric values may become digit-equivalent text.

## Row State

- Pending: `shop_name` and matching `owner` are present while `profile_result` and `priority` are both blank.
- Complete: both result cells are nonblank and are reused without browser access.
- Inconsistent: exactly one result cell is nonblank; keep unchanged and report it.
- Placeholder: shop name is blank or the row is a merged-cell subordinate; keep blank.

## Identity

The store `stable_identity` is the normalized shop name plus normalized final official shop URL. A decision additionally binds `category` through `rule_fingerprint`, including classification rules and thresholds. Grouping, workbook reuse and checkpoints must not merge different categories. Supplied and redirected URLs are aliases, not permission to reuse a different shop or category. Conflicting official URLs for one normalized name pause the run.

## Product Evidence

Each evidence item contains:

- `product_id`
- `product_url`
- `title`
- `sales_text`
- `sales_lower_bound`
- `relevant`
- `match_reason`
- `source_type`
- `final_page_url`
- `source_urls`

SPU and `high_sales_links` are calculated after deduplication by `product_id`.

## Review Result

Each completed shop record contains `shop_name`, `official_shop_url`, `category`, `rule_fingerprint`, timezone-aware `captured_at`, deduplicated evidence, source pages, `relevant_spu`, `high_sales_links`, `profile_result`, `priority`, and `complete`.

Observed high-threshold evidence can prove a high result without inventory exhaustion. A low result requires explicit complete coverage and observable sales; having a category or one visible product is not proof of completeness. Missing sales remain unknown, not zero. Unresolved cases continue to fallback pages or remain pending.

Allowed priorities are `高`, `中`, `低`, and `待核验`. A non-complete record must use `待核验` for both `profile_result` and priority.

## Checkpoint

The JSON checkpoint uses `schema_version: 2` and contains `status`, `completed`, `aliases`, `pending`, `current_task`, and a sanitized failure reason. Evidence expires after 24 hours or a rule/category change. Missing timestamps are not fresh. Version 1 checkpoints are preserved in `.v1.bak` before migration and never reused as version 2 decisions. Unknown versions fail closed. Writes are atomic. Cookies, API keys, bearer tokens, Authorization headers, and browser storage are forbidden.
