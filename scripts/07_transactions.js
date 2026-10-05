// ClaimSure - 07. Multi-document ACID transaction: claim settlement
//   mongosh "mongodb://localhost:27017/?replicaSet=rs0" --file scripts/07_transactions.js
// Transactions need a replica set (Atlas clusters always are; locally start mongod with --replSet rs0
// and run rs.initiate() once).
//
// Settlement touches four collections and must be all-or-nothing:
//   claims (status -> Settled), policies (available cover down), payments (new row), audit_logs (entry)

db = db.getSiblingDB('claimsure');

function settle(claimNumber, { forceFailure = false } = {}) {
  const session = db.getMongo().startSession();
  const s = session.getDatabase('claimsure');
  session.startTransaction({ readConcern: { level: 'snapshot' }, writeConcern: { w: 'majority' } });
  try {
    const claim = s.claims.findOne({ claim_number: claimNumber, status: { $in: ['Approved', 'Partially Approved'] } });
    if (!claim) throw new Error('claim is not awaiting payment');
    const amount = claim.approved_amount;
    const now = new Date();

    s.claims.updateOne({ _id: claim._id }, {
      $set: { status: 'Settled', settled_amount: amount, settled_at: now },
      $push: { status_history: { status: 'Settled', at: now, by: 'USR003', by_name: 'Lakshmi Iyer', remarks: 'Paid via NEFT' } },
    });
    print(`  1. claim ${claimNumber} -> Settled`);

    // Guarded update: only succeeds when enough cover is left
    const res = s.policies.updateOne({ policy_number: claim.policy_number, available_cover: { $gte: amount } },
                                     { $inc: { utilized_amount: amount, available_cover: -amount } });
    if (res.matchedCount !== 1) throw new Error('insufficient sum insured');
    print(`  2. policy ${claim.policy_number} cover reduced by ${amount}`);

    if (forceFailure) throw new Error('simulated payment gateway timeout');

    const seq = s.counters.findOneAndUpdate({ _id: 'payment' }, { $inc: { seq: 1 } }, { returnDocument: 'after' }).seq;
    s.payments.insertOne({
      payment_id: `PAY-${now.getFullYear()}-${String(seq).padStart(6, '0')}`, claim_number: claimNumber,
      policy_number: claim.policy_number, insurer_id: claim.insurer_id, amount,
      payee: { type: claim.claim_type === 'Cashless' ? 'Hospital' : 'Member', name: claim.snapshot.hospital_name },
      mode: amount >= 200000 ? 'RTGS' : 'NEFT', utr: `MSH${now.getTime()}`, status: 'Success', paid_at: now,
    });
    print('  3. payment recorded');
    s.audit_logs.insertOne({ timestamp: now, actor_id: 'USR003', action: 'CLAIM_SETTLED', entity_type: 'claim',
                             entity_id: claimNumber, summary: `Settled for ${amount}` });
    print('  4. audit log written');

    session.commitTransaction();
    print('  COMMITTED');
  } catch (e) {
    session.abortTransaction();
    print(`  ABORTED: ${e.message} - every change above is rolled back`);
  } finally {
    session.endSession();
  }
}

const claim = db.claims.findOne({ status: { $in: ['Approved', 'Partially Approved'] } });
const before = db.policies.findOne({ policy_number: claim.policy_number }).available_cover;
print(`Claim ${claim.claim_number}: approved ${claim.approved_amount}; policy cover available ${before}`);

print('\nRun 1 - failure after the first two writes:');
settle(claim.claim_number, { forceFailure: true });
print(`  verify: status=${db.claims.findOne({ claim_number: claim.claim_number }).status}, ` +
      `cover=${db.policies.findOne({ policy_number: claim.policy_number }).available_cover}, ` +
      `payments=${db.payments.countDocuments({ claim_number: claim.claim_number })}`);

print('\nRun 2 - normal settlement:');
settle(claim.claim_number);
print(`  verify: status=${db.claims.findOne({ claim_number: claim.claim_number }).status}, ` +
      `cover=${db.policies.findOne({ policy_number: claim.policy_number }).available_cover}, ` +
      `payments=${db.payments.countDocuments({ claim_number: claim.claim_number })}`);

print('\nRun 3 - settling again is refused:');
settle(claim.claim_number);
