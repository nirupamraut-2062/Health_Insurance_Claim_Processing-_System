# Review Presentation Guide: ClaimSure

**Title:** ClaimSure: A NoSQL-Based Health Insurance Claim Processing and Analytics System using MongoDB

Before the review:
- Run `pip install -r requirements.txt` once.
- Make sure `.env` holds your Atlas connection strings, and that **Network Access** in Atlas allows the network you will present from.
- Run `python main.py test-live` the morning of the review: 32 passing tests show the server is reachable and everything works.
- Open the public link (Render) once, about a minute early, so the free server wakes up. Or start the app locally with `python main.py web` (`http://127.0.0.1:8000`). The header must say **LIVE · MongoDB (Atlas)**.
- Have three sign-ins ready (passwords are in `.env`):
  - **Head Admin**: `USR001` (Ananya Rao)
  - **Supervisor**: `USR003` (Lakshmi Iyer, Claims Manager)
  - **Staff**: `USR011` (Neha Kapoor, Claims Executive)
- Use a private window for a second user so both stay signed in.
- If the college network blocks Atlas, add `CLAIMSURE_MODE=mock` to `.env`. Everything still runs on the in-memory mock, and the labs say what is emulated.
- Open a second terminal with `python main.py watch` for the change-stream demo.

---

## Demo script (about 18 minutes)

### 1. Problem and design (2 min)
- Insurers process thousands of claims. Each claim is a *case file* with a variable bill, diagnoses, documents, queries and an audit history. This is a natural fit for a document database.
- Open **DB Console → Data model** and walk through the embed/reference table and the six design patterns.
- Mention the scale: 14 collections, 16,758 documents, 5,288 insured members, 2,591 claims, plus a 100,000-document benchmark collection, all on MongoDB Atlas.

### 2. Sign-in and access levels (2 min)
- Open the site: you are sent to the **sign-in page**. Sign in as `USR011` (Claims Executive, **Staff**).
- The top bar shows the role and the access level.
- **My Account** lists what this person may and may not do.
- Open `/admin/staff`: **Access denied**. A Claims Executive cannot manage staff.
- Sign out and sign in as `USR001` (**Head Admin**). **Staff & Access** now appears.
  - Show the permission matrix and the security events, including the sign-ins just made.
  - **Reset password** for a worker. A temporary password is shown once, and they must change it at their next sign-in.
- Database point: passwords are scrypt hashes in a separate `credentials` collection, and sessions live in `sessions` with a **TTL index**. Show both in **DB Console → Query playground**: it refuses `$lookup` into `credentials`.

### 3. Dashboard (2 min)
- KPIs: claim settlement ratio, decision turnaround (cashless vs reimbursement), open fraud alerts, premium in force.
- Point out that the whole KPI row comes from **one `$facet` aggregation**.

### 4. Claim lifecycle, live (5 min)
1. **New Claim** → click a sample policy → **Look up**. Insured members load and the network hospital check works.
2. Pick a cardiac diagnosis (e.g. *Acute myocardial infarction*), click **Suggest** for the bill, choose room **Deluxe**, then **Submit**.
   - The claim is validated against `$jsonSchema`, scored by the fraud engine and written to the audit log.
3. Sign in as **Neha Kapoor, Claims Executive (limit ₹75,000)** (`USR011`) → *Update status → Under Review* → **Record decision**.
   - It is **escalated** automatically because the payable amount is above her limit.
4. Show the **adjudication worksheet**:
   - room-rent excess and the **proportionate deduction** (an IRDAI rule)
   - the non-payable items
   - the eligibility checks table (waiting periods)
5. Sign in as the assigned **Senior Adjuster / Claims Manager** (the claim shows who it is assigned to) → **Record decision**.
6. Click **Simulate failure**. The yellow and red box lists every step and then **ROLLBACK**. The cover and claim status are unchanged.
7. Click **Pay**. The transaction commits across `claims`, `policies`, `payments` and `audit_logs`.
   - Show the timeline and the updated policy cover.
   - Click **Pay** again (or reload): settlement is refused, and the unique index blocks a second payment.
8. **Reopen the dead case (Head Admin authority)**:
   - Still on the settled claim, show the Claims Executive first: "Only the Head Admin can reopen a closed case".
   - As `USR001`, open **Reopen case for re-verification**, give a reason and assign it to the Fraud Analyst `USR021`. The claim moves to **Re-verification** and lands in her queue.
   - Sign in as `USR021` and choose **Settlement flagged for recovery**. The claim returns to Settled with a recovery flag, and the payment is not made a second time.
   - For a **Rejected** claim, the verifier can choose *re-adjudicate* instead: the old decision moves to `previous_adjudications`.

### 5. Database features (6 min)
- **Concurrency Lab → Run all four scenarios**: two adjusters pay against one policy at the same time.
  - A loses ₹1,00,000 (lost update).
  - B is fixed by an atomic `$inc`.
  - C is fixed by optimistic locking: watch the orange *CONFLICT* line.
  - D is fixed by a transaction: watch the orange *WriteConflict* line, then the retry.
- **Optimistic locking on a real claim**: open the same claim in two tabs, change its status in one, then try in the other. The second attempt is refused with "changed by someone else".
- **Scale Benchmark** (run it once before the review): 100,000 documents.
  - Covered query: 0 documents read.
  - Prefix rule: the index is not used.
  - Unanchored regex and low-selectivity queries: the index makes things slower.
- **Live Feed** (or the `watch` terminal): settle a claim and three events arrive at once, one each for the claim, the policy and the payment.
- **Security & Roles → Test the read-only analyst account**: reads work, while insert, update, delete and drop are refused by the server (Atlas returns code 8000; a self-hosted server returns code 13, Unauthorized).
- **Maintenance**:
  - **Migrate to v2** upgrades 2,591 claims in about one second.
  - Show the `members_masked` view.
  - **Refresh** the materialised view, which shows the live vs stored timing.
  - **Back up now**.
- **DB Console → Run benchmark**: COLLSCAN vs IXSCAN on the real claims.
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

### 6. Wrap-up (1 min)
- CLI (`python main.py`) shows the same features in a terminal.
- `python main.py test` runs 108 tests on the mock, and `python main.py test-live` runs 32 more on Atlas.
- The app is deployed publicly (Render + Atlas), and the evaluators can open it from their phones.
- mongosh scripts 01–09 cover window functions, `$dateDiff` with `$percentile`, `$merge`, `$geoNear`, `$text`, `explain()` and roles.
- The design rationale is in `docs/DATABASE_DESIGN.md`: ER model, relational comparison, CAP, shard keys, backup and security.

---

## Rubric mapping

| Criterion | Where to show it |
|---|---|
| **Schema design** | DB Console → Data model and Design patterns. Claim document (embedded bill, history, adjudication, `snapshot`). Policy (embedded `insured_members`, `renewal_history`) |
| **Query optimisation** | Scale benchmark (100,000 docs, real `explain()`), index catalogue with a purpose for each index, partial/TTL/text/2dsphere indexes, "project early" and "group before lookup" in the pipelines |
| **Advanced features** | ACID settlement with retry on write conflict, concurrency lab, optimistic locking, change streams, roles and a masked view, views and a materialised view, schema migration, backup and restore, 19 aggregation reports |

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

---

## Viva questions on the new database features

**Q13. Optimistic or pessimistic locking: which did you use and why?**
Optimistic. A claim reviewer can spend minutes on a claim, so holding a lock that long would block everyone else. Each claim has a `version` field, and every update matches `{_id, version: v}` and does `$inc: {version: 1}`. If someone else updated the claim first, the update matches 0 documents and the user is told to reload. The Concurrency Lab, scenario C, shows it.

**Q14. What is a lost update and how does MongoDB prevent it?**
Two clients read the same value, both compute a new one, and the second write silently overwrites the first (scenario A loses ₹1,00,000). There are three fixes:
- let the server compute it with an atomic `$inc`
- use optimistic locking
- run inside a transaction, where MongoDB detects the write conflict

**Q15. What happens when two transactions update the same document?**
MongoDB uses snapshot isolation. The second transaction to write gets a `WriteConflict`, a transient error, and is aborted. `session.with_transaction()` retries it automatically on fresh data, so both payments are applied exactly once. Scenario D shows the retry.

**Q16. What is a covered query?**
A query whose filter and projection use only indexed fields. MongoDB answers it from the index without reading any documents: `PROJECTION_COVERED`, `totalDocsExamined: 0`. The benchmark shows it going from 100,000 documents to 0.

**Q17. Why is your index not used for "claims since September"?**
The prefix rule. A compound index `{status, submitted_at}` can only be used when the query includes its leading field `status`. A query on `submitted_at` alone needs its own index.

**Q18. When does an index make a query slower?**
When the query is not selective. Fetching 78% of the documents through the index means random reads, which is slower than one sequential collection scan. An unanchored regex also has to read every index key.

**Q19. What is a change stream?**
A push feed of inserts, updates and deletes, read from the replica set's oplog. ClaimSure watches four collections and streams events to the browser over Server-Sent Events. It needs a replica set, and a consumer can resume from a saved token after a restart.

**Q20. View or materialised view?**
A view (`members_masked`) is a stored pipeline computed on every read, so it is always fresh and stores nothing. A materialised view (`mv_insurer_monthly`) is a real collection refreshed with `$merge`, which makes it faster to read but only as fresh as its last refresh.

**Q21. How do you change a schema in a schemaless database?**
With the schema versioning pattern. Documents carry `schema_version`, the code reads every version, and an idempotent migration (`update_many` on `schema_version: {$exists: false}`) upgrades documents in place, even while the app is running. The `$jsonSchema` validator still guards every write.

**Q22. How would you shard the claims collection?**
On `{member_id: "hashed"}`. Writes spread evenly, and a member's history and the claim page stay on one shard. The claim number is rejected as a key because it always increases, so every insert would hit one shard. Status is rejected because it has too few values. Transactions still work across shards.

**Q23. How is the data secured?**
- separate database users with least privilege (the analyst can only read)
- a custom auditor role
- a masked view that hides personal data
- TLS and encryption at rest on Atlas
- an IP allow-list
- secrets kept in a git-ignored `.env`
- every change written to the audit log

**Q24. How would you recover from data loss?**
For a logical backup, `python main.py backup` or `mongodump`. To restore, `python main.py restore`, which drops the data, re-applies the validators, reloads and rebuilds the indexes. In production, Atlas continuous backup replays the oplog to any second, and the WiredTiger journal protects committed writes from a crash.

**Q25. How do staff sign in, and where are the passwords stored?**
In a separate `credentials` collection, 1 : 1 with `users` through a unique index. Only a salted scrypt hash is stored, never the password. The separation keeps secrets out of the profile documents that analysts and the query playground can read, and a reset of `users` does not wipe the passwords.

**Q26. How do sessions work?**
On sign-in the server generates a random token and sends it in an HttpOnly cookie. It stores only the token's SHA-256 digest in `sessions`, with an `expires_at` field. A **TTL index** (`expireAfterSeconds: 0`) makes MongoDB delete each session when it expires. Every request resolves the user with one aggregation: `$match` on the digest, then `$lookup` into `users` and `credentials`.

**Q27. What stops someone from guessing passwords?**
Each wrong password does an atomic `$inc` on `failed_attempts`. At five, `locked_until` is set 15 minutes ahead, and the event goes to `audit_logs`. The Head Admin can unlock the account early. Each IP address is also throttled.

**Q28. How are the access levels enforced?**
One permission matrix in `claimsure/auth.py` maps each permission to the roles that hold it. The web routes check it, so a hand-crafted POST is refused, and so do the workflow services, so the CLI obeys the same rules. The templates only use it to hide buttons.

**Q29. What does reopening a dead case do to the data?**
The claim's status changes from Settled, Rejected or Withdrawn to **Re-verification**, under the optimistic lock. An entry is pushed onto the embedded `reverifications` array: who reopened it, why, from which status and who verifies it. `reopen_count` is incremented and an audit entry is written. The server-side `$jsonSchema` enum was extended with the new status, and the app syncs validators with `collMod` at start-up. A settled claim can never be paid again, because the only outcomes are uphold or flag for recovery, and the unique index on `payments.claim_number` would block a second payment anyway.
