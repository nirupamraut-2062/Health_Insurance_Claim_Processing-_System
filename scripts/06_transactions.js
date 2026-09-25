// Multi-Document ACID Transaction: Claim Settlement
// This transaction atomically:
// 1. Updates claim status to 'Settled'
// 2. Deducts approved_amount from policy's remaining_coverage
// If remaining_coverage is insufficient, transaction ABORTS

use('health_insurance_db');

print('\n===== ACID TRANSACTION: Claim Settlement =====');

// Find an Approved claim to settle
var claim = db.claims.findOne({ status: 'Approved' });
if (!claim) {
  print('❌ No Approved claims found to settle. Skipping transaction demo.');
} else {
  print('Found approved claim: ' + claim.claim_id);
  print('Approved amount: ₹' + claim.approved_amount);

  var policy = db.policies.findOne({ policy_id: claim.policy_id });
  print('Policy: ' + policy.policy_id + ' | Remaining coverage: ₹' + policy.remaining_coverage);

  // Start transaction
  var session = db.getMongo().startSession();
  session.startTransaction({ readConcern: { level: 'snapshot' }, writeConcern: { w: 'majority' } });

  try {
    var claimsColl = session.getDatabase('health_insurance_db').claims;
    var policiesColl = session.getDatabase('health_insurance_db').policies;

    // Check if policy has sufficient remaining coverage
    var currentPolicy = policiesColl.findOne({ policy_id: claim.policy_id });
    if (currentPolicy.remaining_coverage < claim.approved_amount) {
      throw new Error('Insufficient remaining coverage. Available: ₹' + currentPolicy.remaining_coverage + ', Required: ₹' + claim.approved_amount);
    }

    // Step 1: Update claim status to Settled
    claimsColl.updateOne(
      { claim_id: claim.claim_id },
      {
        $set: { status: 'Settled' },
        $push: {
          status_history: {
            status: 'Settled',
            timestamp: new Date().toISOString(),
            notes: 'Payment settled via ACID transaction',
            updated_by: 'finance_system'
          }
        }
      }
    );
    print('Step 1: Claim ' + claim.claim_id + ' marked as Settled');

    // Step 2: Deduct from policy remaining coverage
    policiesColl.updateOne(
      { policy_id: claim.policy_id },
      { $inc: { remaining_coverage: -claim.approved_amount } }
    );
    print('Step 2: Policy ' + claim.policy_id + ' coverage reduced by ₹' + claim.approved_amount);

    // Commit
    session.commitTransaction();
    print('\n✅ Transaction COMMITTED successfully');

    // Verify
    var updatedPolicy = db.policies.findOne({ policy_id: claim.policy_id });
    print('Updated remaining coverage: ₹' + updatedPolicy.remaining_coverage);

  } catch (error) {
    session.abortTransaction();
    print('\n❌ Transaction ABORTED: ' + error.message);
  } finally {
    session.endSession();
  }
}

print('\n✅ Transaction demonstration complete');
