# Databricks storage: analytical vs transactional (the distinction the names hide)

Databricks storage terms describe file *format* and *architecture*, not *workload*. "Delta table," "lakehouse," and "warehouse" tell you how data is stored and governed; they say nothing about whether the store is good at the two opposite jobs a database can do. That gap trips up experienced engineers: a team can spend months treating a "Delta table" as a general-purpose database, then discover it was the wrong tool for half of what they asked of it. This document restores the intent behind the vocabulary.

The distinction is old and taught in every database course: **OLTP vs OLAP**, transactional vs analytical. The Databricks names simply do not carry it on their face, so it is easy to forget which one you are actually holding.

## The one distinction that matters

| | **Transactional (OLTP)** | **Analytical (OLAP)** |
| --- | --- | --- |
| Built for | Many small operations: read/write a single row, right now | Few large operations: scan/aggregate millions of rows |
| Access pattern | Point read, point write, row update | Full-column scans, joins, group-by |
| Latency profile | Sub-millisecond to low-ms per operation | Seconds+, but enormous throughput |
| Storage shape | Row store, indexed | Columnar files (compressed by column) |
| Typical uses | Sessions, logs you read back live, user/app state, counters, queues | Dashboards, reports, ML features, historical analysis |
| Cold start | None to speak of (a Postgres connection is warm) | Real: an analytical engine has to spin up (2-6s serverless) |

The trap is that a columnar analytical store is *fantastic* at the thing you demo first (a big aggregate query returns fast and impresses everyone) and *quietly terrible* at the thing you hit later on the hot path (fetch one row per request, update one row per request). By the time you notice, the naming has already convinced everyone it is "the database."

## Decoding the Databricks vocabulary

| Term you hear | What it actually is | Which side | Note |
| --- | --- | --- | --- |
| **Parquet** | A columnar file format on object storage | Analytical | Just files. No ACID transactions, no updates. |
| **Delta table / Delta Lake** | Parquet + a transaction log (ACID, time travel, MERGE) | Analytical | The ACID part fools people. It makes Parquet *reliable*, not *transactional in the OLTP sense*. Point reads and row updates are still slow. |
| **Lakehouse** | The *architecture/brand*: Delta tables + Unity Catalog governance + warehouses, over object storage | Analytical | Not a database you point-query. It IS Delta underneath. When you hear "lakehouse," think analytical. |
| **SQL Warehouse** | The compute engine that runs SQL over Delta | Analytical | JVM startup, query planning, no connection pooling, 2-6s serverless cold start. Great for scans; wrong for per-request point reads. |
| **Lakebase** | Managed **serverless Postgres** | Transactional | This is the OLTP layer. Sub-second point ops, pooled connections, scales to zero and wakes in roughly 370-500 ms. |

### The one-word confusion to watch for

**lake*house* is analytical. lake*base* is transactional.** They are one syllable apart, both are Databricks marketing words, and they name opposite workloads. If you only remember one thing from this page, remember that the "-house" is the analytical platform (Delta underneath) and the "-base" is the Postgres OLTP layer.

## Why the names mislead

"Delta table" names a storage format and its ACID guarantees. It does not name an access pattern. So the word is silent on the only question that determines whether it is the right tool: *am I doing point operations, or scans?* ACID transactions make people hear "transactional," but Delta's transactions are about *consistency of large batch writes*, not *low-latency single-row operations*. Two different meanings of the same word, and the gap is exactly where the wrong architecture gets chosen.

## How to recover the intent (questions to ask, whatever it is called)

Ignore the brand name and ask:

1. **Point or scan?** Do I fetch/modify one row (or a few) at a time, or do I scan/aggregate many? Point -> transactional. Scan -> analytical.
2. **Read it back live?** Do I write something and read it back on the same request path within milliseconds (a session, a live log, a counter)? Yes -> transactional.
3. **Latency budget?** Is sub-second per operation a requirement, or is "seconds, but huge throughput" fine? Sub-second per op -> transactional.
4. **Cold start on the hot path?** Can this sit in a user request, or would an analytical engine's 2-6s spin-up land in the user's face? If it is on the hot path -> transactional.

If the answers say "transactional" but the tool is a Delta table behind a SQL Warehouse, the name tricked you.

## The logging special case (this one is subtle)

Logging is where the mistake hides, because "log to a Delta table" is *both* right and wrong depending on what you do next:

- **Append-only, analyze later: Delta is correct.** Delta handles append-heavy event streams well. Landing logs/events in Delta for dashboards, batch analysis, and ML features is exactly what it is for.
- **Write then read back live, or update a row: wrong tool.** Reading a single log/session row back on the request path, or updating a row, pays the analytical penalty (slow point ops + warehouse cold start). That state belongs in Lakebase/Postgres.

So the same word "logging" points at two different stores. Hot-path state (sessions, live status you read back immediately) -> transactional (Lakebase/Postgres). Historical events you analyze in aggregate -> analytical (Delta). Often you want both: write to Postgres for the live path, stream the same events to Delta for later analysis.

## Cheat sheet: which store for which job

| I need to... | Use |
| --- | --- |
| Store sessions / per-request state and read it back live | Lakebase (Postgres) |
| Keep a live counter, queue, or status a request reads back | Lakebase (Postgres) |
| Look up one user/record by key on the hot path | Lakebase (Postgres) |
| Append events/logs for later analysis | Delta table |
| Run dashboards, reports, aggregations over history | Delta + SQL Warehouse |
| Build ML features from large historical data | Delta + SQL Warehouse |
| Serve a raw columnar file dump, no updates | Parquet |

## See also

- [Latency axes](databricks-latency-axes.md) - Axis 2 is this distinction applied to a request-serving app: per-request point reads against a SQL Warehouse are the anti-pattern; move them to Lakebase.
- [Databricks Apps vs Model Serving](databricks-apps-vs-model-serving.md) - section 4 (the warehouse latency axis) has the measured numbers and sources (SQL Warehouse point reads ~2-6s; Lakebase scale-to-zero wake ~370-465 ms).
