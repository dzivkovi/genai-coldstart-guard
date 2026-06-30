# Databricks Apps vs Model Serving: cold start, cost, and where to host a web service

Databricks gives you more than one way to run a service, and the cost/latency trade-offs differ sharply. A common mistake is hosting a **non-model web service** (a router, an API, glue code, an agent front-end) as an MLflow model on **Model Serving** - where it pays a recurring scale-to-zero cold-start tax for no model-serving benefit. **Databricks Apps** is the purpose-built home for that kind of service, but it has its own cost and cold-start characteristics that are easy to get wrong.

This is a grounded, fact-checked comparison of the two for hosting a Python web service (Flask/FastAPI), distilled from Databricks' own docs and independent practitioner reports (all linked inline). It answers four questions: how the cold start differs, what it actually costs, which problem Apps solves (and which it doesn't), and what Databricks itself recommends. Every claim has a source; nothing here is vendor marketing.

Nothing in this document is specific to any project or workspace - it is purely about Databricks platform behaviour.

## 1. Cold start: App vs Model Serving - the win is "always-warm," not "fast wake"

Correction to an earlier assumption (that the wake was "seconds"): a Databricks App **starting from Stopped takes ~2-3 minutes** (community MVP report: "Cold start is 2-3 min - schedule ~15 min before business hours") - comparable to Model Serving's boot. **But here's the real difference:** a Databricks App has only two states, **Running** (warm, billed) or **Stopped** (free) - it does **not** auto-suspend on idle. So while it's Running, there is **no per-request cold start, ever**. Model Serving scale-to-zero, by contrast, auto-suspends after idle and cold-starts on *every* first-request-after-idle.

So the App doesn't wake faster - it just **stays warm**, which removes the *recurring* cold start. ([key-concepts](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/key-concepts), [community MVP](https://community.databricks.com/t5/mvp-articles/cut-our-databricks-apps-costs-by-76-with-two-scheduled-jobs/td-p/158558))

## 2. Cost: Apps do NOT scale to zero

Multiple sources confirm: **Databricks Apps have no native scale-to-zero / idle auto-shutdown.** They bill while Running (24/7) until you explicitly Stop them. The "CPU type" is just the compute tier (CPU serverless, no GPU) - it does **not** imply idle suspension.

- Pricing: Medium = 0.5 DBU/hr, Large = 1 DBU/hr at ~$0.75/DBU → roughly **$270/mo (Medium) or $540/mo (Large) for 24/7**. ([Apps pricing](https://www.databricks.com/product/pricing/databricks-apps))
- The supported cost-saving pattern is **scheduled start/stop** via Jobs/CLI: one team cut cost **~76%** by running 9am-6pm weekdays only. ([community MVP](https://community.databricks.com/t5/mvp-articles/cut-our-databricks-apps-costs-by-76-with-two-scheduled-jobs/td-p/158558), [community Q&A](https://community.databricks.com/t5/data-engineering/databricks-apps/td-p/158552))

So vs an always-on **GPU** Model Serving endpoint, a CPU App is far cheaper - but it's "cheaper warm unit + schedulable," **not** "free when idle."

## 3. Which of the two problems (cost and cold start) does the App actually solve?

- **Cold start: yes** - by staying warm (keep it Running, or schedule it warm during business hours → no per-request cold start when users are active).
- **Cost: partially** - cheaper CPU compute than GPU serving, and ~76% reducible via scheduling, but **no free idle**. The scale-to-zero-for-cost behaviour lives in **Lakebase** (the data layer scales to zero, wakes <500ms), **not** the App runtime. ([Syren on Lakebase](https://syrencloud.com/building-databricks-app-on-lakebase/))

## 4. The Warehouse latency - a second, separate axis

When a service does per-request point reads (user lookup, history, logging) against a **SQL Warehouse**, the latency adds up. An independent practitioner (Covasant) measured exactly this pain: *"Every API request... was paying SQL Warehouse cold-start latency - roughly 2-6 seconds on Serverless... Delta-via-Spark is not a sub-second point-read engine (JVM startup, query planning, no connection pooling)."* Their fix: move hot-path reads to **Lakebase** (sub-second, pooled). ([Covasant](https://www.covasant.com/blogs/databricks-apps-api-performance-single-source-of-truth)) So that's a **third** latency axis, separate from the container cold start: the data layer.

## 5. Best practices - is there an official case for Flask-on-Apps over Model Serving? Yes.

Databricks' own docs make the case:

- **"Migrate an agent from Model Serving to Databricks Apps"** - *"Databricks recommends authoring agents on Databricks Apps"* over Model Serving, citing rapid iteration, Git/CI-CD, async concurrency, custom routes/middleware, any framework. ([migrate doc](https://docs.databricks.com/aws/en/generative-ai/agent-framework/migrate-agent-to-apps))
- **Best practices for Databricks Apps**: keep startup lightweight to reduce cold start; **offload heavy work** - "use SQL warehouses for queries, Model Serving for inference, Jobs for batch"; app compute is for UI/routing. ([best practices](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/best-practices))
- Databricks-solutions ships an official skill for building **Flask/FastAPI** on Apps (Gunicorn/uvicorn). Third-party (Qubika): Apps are great for thin UI/routing, "dangerous when stretched" into heavy execution.

## The practical sweet spot

A thin Flask/FastAPI routing service on **Databricks Apps**, kept warm during business hours (scheduled start/stop), with **Lakebase** for the state layer and **Foundation Model APIs** for generation, removes all three cold-start axes during work hours and costs a fraction of always-on GPU serving. The App fixes the container cold start and the packaging mismatch; Lakebase fixes the warehouse latency + its cold start; FMAPI removes the LLM axis.

One caveat to carry: don't quote the "seconds wake" figure - from Stopped it's ~2-3 min; the win is staying warm, not waking fast.

## References (the starting points that matter)

Databricks' own guidance (decision starting points):

- [Best practices for Databricks Apps](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/best-practices) - keep the app thin, offload heavy work, minimize startup.
- [Migrate an agent from Model Serving to Databricks Apps](https://docs.databricks.com/aws/en/generative-ai/agent-framework/migrate-agent-to-apps) - Databricks recommends Apps over Model Serving for agent / web-app workloads.
- [Key concepts in Databricks Apps](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/key-concepts) - the Running / Stopped lifecycle (billed while running; no auto-idle).
- [Databricks Apps pricing](https://www.databricks.com/product/pricing/databricks-apps) - Medium 0.5 DBU/hr, Large 1 DBU/hr.

Cost / reclaiming idle spend (Apps have no native scale-to-zero, so manage idle actively):

- [Reclaim Spend from Idle Databricks Apps](https://www.databricksters.com/p/reclaim-spend-from-idle-databricks) (Databricksters) - a governance workflow (System Tables + OpenTelemetry) to find and stop idle apps. Pairs with the business-hours pattern: keep apps warm while teams are working/testing (e.g. 9-5 in their time zone), stop them otherwise.
- [Cut Databricks Apps costs ~76% with two scheduled jobs](https://community.databricks.com/t5/mvp-articles/cut-our-databricks-apps-costs-by-76-with-two-scheduled-jobs/td-p/158558) - scheduled start/stop pattern (and the ~2-3 min start time).

Data-layer latency (the second axis):

- [How we made Databricks Apps APIs fast](https://www.covasant.com/blogs/databricks-apps-api-performance-single-source-of-truth) (Covasant) - SQL Warehouse point reads cost ~2-6s; move hot reads to Lakebase.
- [Building a Databricks App on Lakebase](https://syrencloud.com/building-databricks-app-on-lakebase/) (Syren) - measured Lakebase scale-to-zero cold start (370-465 ms).

Decision guide:

- [Choosing the right way to serve workloads in Databricks](https://qubika.com/blog/choosing-right-way-serve-workloads-databricks/) (Qubika) - Apps for UI / routing; delegate heavy execution to Jobs / SQL.
