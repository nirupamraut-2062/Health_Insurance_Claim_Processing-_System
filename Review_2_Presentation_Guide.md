# Review Presentation Guide: ClaimSure

**Title:** ClaimSure: A NoSQL-Based Health Insurance Claim Processing and Analytics System using MongoDB

Before the review:
- Run `pip install -r requirements.txt` once.
- Start the app with `python main.py web` and keep `http://127.0.0.1:8000` open.
- No internet is needed: Chart.js is bundled.
- To reset the data, switch user to *Ananya Rao (Admin)*, open **DB Console** and click **Reset database**.

---

## Demo script (about 15 minutes)

### 1. Problem and design (2 min)
- Insurers process thousands of claims. Each claim is a *case file* with a variable bill, diagnoses, documents, queries and an audit history. This is a natural fit for a document database.
- Open **DB Console → Data model** and walk through the embed/reference table and the six design patterns.
- Mention the scale: 14 collections, about 16,900 documents, 5,288 insured members, 2,592 claims.

### 2. Dashboard (2 min)
- KPIs: claim settlement ratio, decision turnaround (cashless vs reimbursement), open fraud alerts, premium in force.
- Point out that the whole KPI row comes from **one `$facet` aggregation**.

### 3. Claim lifecycle, live (5 min)
1. **New Claim** → click a sample policy → **Look up**. Insured members load and the network hospital check works.
2. Pick a cardiac diagnosis (e.g. *Acute myocardial infarction*), click **Suggest** for the bill, choose room **Deluxe**, then **Submit**.
   - The claim is validated against `$jsonSchema`, scored by the fraud engine and written to the audit log.
3. Switch user (top right) to **Neha Kapoor, Claims Executive (limit ₹75,000)** → *Update status → Under Review* → **Record decision**.
   - It is **escalated** automatically because the payable amount is above her limit.
4. Show the **adjudication worksheet**:
   - room-rent excess and the **proportionate deduction** (an IRDAI rule)
   - the non-payable items
   - the eligibility checks table (waiting periods)
5. Switch to the assigned **Senior Adjuster / Claims Manager** → **Record decision**.
6. Click **Simulate failure**. The yellow and red box lists every step and then **ROLLBACK**. The cover and claim status are unchanged.
7. Click **Pay**. The transaction commits across `claims`, `policies`, `payments` and `audit_logs`.
   - Show the timeline and the updated policy cover.
   - Click **Pay** again (or reload): settlement is refused, and the unique index blocks a second payment.

### 4. Database features (4 min)
- **DB Console → Run benchmark**: COLLSCAN vs IXSCAN and documents examined (e.g. 2,592 → 2).
  - Explain the **ESR rule** for the compound index `{status, submitted_at}`.
- **Insert invalid claim**: the `$jsonSchema` validator rejects the document with a list of rule violations.
- **Indexes** section covers each index type:
  - unique: `payments.claim_number`
  - multikey: `diagnosis.icd_code`, `insured_members.member_id`
  - text: claims search
  - partial: fraud watch-list
  - sparse: `agent_id`
  - TTL: audit log retention
  - 2dsphere: nearest hospitals
- **Analytics**: open any report → **Pipeline** to show the real aggregation.
  - Good ones to show: *Insurer scorecard* (two pipelines + `$lookup`), *Age bands* (`$bucket`), *Where deductions come from* (`$unwind`).
- **Query playground**: run an example such as `$elemMatch` or `$expr` with `$size`.

### 5. Wrap-up (1 min)
- CLI (`python main.py`) shows the same features in a terminal.
- `python main.py test` runs 79 automated tests.
- **For the final review**:
  - move to a real MongoDB replica set (free Community Server or Atlas M0)
  - run `python main.py init-db`
  - present the mongosh scripts: window functions, `$dateDiff` with `$percentile`, `$merge` materialised views, `$geoNear`, `$text`

---

## Rubric mapping

| Criterion | Where to show it |
|---|---|
| **Schema design** | DB Console → Data model and Design patterns. Claim document (embedded bill, history, adjudication, `snapshot`). Policy (embedded `insured_members`, `renewal_history`) |
| **Query optimisation** | Index benchmark page, index catalogue with a purpose for each index, partial/TTL/text/2dsphere indexes, "project early" and "group before lookup" in the analytics pipelines |
| **Advanced features** | ACID settlement with rollback demo, `$jsonSchema` validation, 19 aggregation reports, fraud engine, approval-limit workflow, audit trail, geo search |

---

## Viva questions

**Q1. Why MongoDB rather than a relational database?**
A claim is a self-contained case file whose shape varies: a different number of bill heads, diagnoses, documents, queries and history entries. Embedding these lets one read load the whole claim and one atomic update change status and history together. Shared entities (members, hospitals, policies) are still referenced, as in a relational design.

**Q2. When did you embed and when did you reference?**
Embed when the data is owned by the parent, read with it and bounded, e.g. the bill, `status_history`, a policy's insured members and renewal years. Reference when the data is shared or unbounded, e.g. claims → members/hospitals/policies, and payments as their own collection. We also use the *extended reference* pattern: claims keep a `snapshot` of names, age and city so lists need no `$lookup`.

**Q3. How do joins work?**
With `$lookup` in aggregation pipelines. We group first and then look up (e.g. 10 insurers instead of 2,592 claims), which keeps the join small. The real-MongoDB scripts also use `$lookup` with a correlated sub-pipeline (`let` + `$expr`).

**Q4. Explain your indexes.**
- unique business keys
- compound `{status: 1, submitted_at: -1}` for work queues (ESR rule)
- `{member_id, admission_date}` for claim history and duplicate detection
- multikey on array fields
- a text index for search
- a partial index on `fraud.score >= 30`, so only risky claims are indexed
- a sparse index on `agent_id`
- a TTL index on audit logs
- 2dsphere on hospital locations

**Q5. How do you know an index is used?**
`explain("executionStats")`: the winning plan changes from COLLSCAN to IXSCAN → FETCH, and documents examined drops to close to documents returned. The benchmark page and `scripts/06_indexing_explain.js` show both plans.

**Q6. What does the transaction protect against?**
Settlement writes to four collections. Without a transaction, a crash after reducing the policy cover but before recording the payment would leave the cover reduced and no payment. With a session (snapshot read concern, majority write concern) all four writes commit or none do. The policy update is guarded by `available_cover: {$gte: amount}`, and a unique index on `payments.claim_number` prevents a double payment.

**Q7. Transactions need a replica set. How does mock mode work?**
mongomock has no sessions, so `claimsure/transactions.py` emulates one. It takes a lock, records a before-image of every document it changes and restores them on failure. The demo shows the same outcome. On a real replica set the same code path uses `client.start_session()`.

**Q8. What is `$jsonSchema` and what do you validate?**
It is MongoDB's built-in document validator. We enforce:
- required fields and BSON types
- enums (claim status, room category)
- patterns (claim number `CLM-YYYY-NNNNNN`, 13-digit ROHINI id, Indian mobile numbers)
- ranges (non-negative amounts, PED waiting period of 48 months or less, solvency ratio of 1.5 or more)

The same `validators.json` is used by MongoDB and by the app.

**Q9. How is the payable amount calculated?**
1. Eligibility checks: waiting periods, exclusions.
2. Then deductions: non-payable items, room rent above eligibility plus a proportionate deduction on associated charges, ambulance limit, sub-limits, co-pay and the available sum insured.

Each deduction is stored in the claim with its reason.

**Q10. How does fraud detection work?**
Nine rule-based red flags add up to a 0–100 score. Examples: overlapping stays for the same member, a bill over 2.5× the diagnosis benchmark, a claim within 90 days of inception, a watch-listed hospital. The scan builds member histories with one `$group`/`$push` aggregation, not one query per claim.

**Q11. How are claim numbers generated safely under concurrency?**
A `counters` collection with `findOneAndUpdate({$inc: {seq: 1}})` is atomic on a single document, so two users never get the same number.

**Q12. What is the claim settlement ratio and incurred claim ratio?**
- CSR = claims paid ÷ claims decided (by count).
- ICR = claims paid ÷ premium earned (by amount).

Both come from aggregation pipelines. ICR uses the premiums embedded in `renewal_history`.
