// ClaimSure - 06. Index effectiveness with explain("executionStats")
//   mongosh "mongodb://localhost:27017" --file scripts/06_indexing_explain.js
// Each query runs twice: forced collection scan (hint {$natural: 1}) vs the optimiser's index choice.

db = db.getSiblingDB('claimsure');

function stages(plan) {
  const out = [];
  let node = plan.queryPlan || plan;
  while (node) {
    out.unshift(node.stage + (node.indexName ? `(${node.indexName})` : ''));
    node = node.inputStage || (node.inputStages || [])[0];
  }
  return out.join(' -> ');
}

function compare(label, coll, filter, sort, projection) {
  const run = hint => {
    let cur = db.getCollection(coll).find(filter, projection || {});
    if (sort) cur = cur.sort(sort);
    if (hint) cur = cur.hint(hint);
    const ex = cur.explain('executionStats');
    return { plan: stages(ex.queryPlanner.winningPlan), docs: ex.executionStats.totalDocsExamined,
             keys: ex.executionStats.totalKeysExamined, returned: ex.executionStats.nReturned,
             ms: ex.executionStats.executionTimeMillis };
  };
  const before = run({ $natural: 1 });
  const after = run(null);
  print(`\n${label}\n  db.${coll}.find(${JSON.stringify(filter)})${sort ? `.sort(${JSON.stringify(sort)})` : ''}`);
  print(`  without index: ${before.plan.padEnd(42)} docs examined ${String(before.docs).padStart(6)}  ${before.ms} ms`);
  print(`  with index   : ${after.plan.padEnd(42)} docs examined ${String(after.docs).padStart(6)}  keys ${after.keys}  returned ${after.returned}  ${after.ms} ms`);
}

const sample = db.claims.findOne({ 'fraud.score': { $gte: 30 } });
const member = db.members.findOne();

compare('1. Member claim history (compound index, equality + sort)', 'claims',
        { member_id: sample.member_id }, { admission_date: -1 });
compare('2. Review work queue (ESR: equality on status, sort on submitted_at)', 'claims',
        { status: 'Under Review' }, { submitted_at: -1 });
compare('3. Diagnosis search on an array field (multikey index)', 'claims', { 'diagnosis.icd_code': 'A90' });
compare('4. Fraud watch-list (partial index: only fraud.score >= 30 is indexed)', 'claims',
        { 'fraud.score': { $gte: 60 } }, { 'fraud.score': -1 });
compare('5. Policies covering a member (multikey on embedded array)', 'policies',
        { 'insured_members.member_id': member.member_id });
compare('6. Covered query - answered from the index alone (0 documents examined)', 'payments',
        { claim_number: db.payments.findOne().claim_number }, null, { _id: 0, claim_number: 1 });

print('\nIndex usage since server start ($indexStats) - top 8 on claims:');
db.claims.aggregate([{ $indexStats: {} }, { $sort: { 'accesses.ops': -1 } }, { $limit: 8 },
                     { $project: { _id: 0, name: 1, ops: '$accesses.ops' } }]).forEach(printjson);
