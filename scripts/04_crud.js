// ClaimSure - 04. CRUD operations on the claim lifecycle
//   mongosh "mongodb://localhost:27017" --file scripts/04_crud.js

db = db.getSiblingDB('claimsure');
const now = new Date();
const title = t => print(`\n========== ${t} ==========`);

// ---------------------------------------------------------------- CREATE
title('CREATE: intimate a claim (number issued by an atomic $inc counter)');
const policy = db.policies.findOne({ status: 'Active', policy_type: 'Family Floater' });
const member = policy.insured_members[0];
const hospital = db.hospitals.findOne({ 'network.insurer_ids': policy.insurer_id });
const seq = db.counters.findOneAndUpdate({ _id: 'claim' }, { $inc: { seq: 1 } }, { returnDocument: 'after' }).seq;
const claimNumber = `CLM-${now.getFullYear()}-${String(seq).padStart(6, '0')}`;
const bill = { room_charges: 9000, icu_charges: 0, nursing_charges: 3000, doctor_fees: 4500, surgeon_ot_charges: 0,
               investigations: 8000, medicines: 14000, consumables: 2500, implants: 0, ambulance: 0, admin_charges: 800 };
bill.total = Object.values(bill).reduce((a, b) => a + b, 0);
const admitted = new Date(now.getTime() - 6 * 86400000);
db.claims.insertOne({
  claim_number: claimNumber, claim_type: 'Cashless', admission_type: 'Emergency', status: 'Intimated',
  policy_number: policy.policy_number, member_id: member.member_id, hospital_id: hospital.hospital_id,
  insurer_id: policy.insurer_id, tpa_id: policy.tpa_id, plan_id: policy.plan_id,
  snapshot: { member_name: member.name, relation: member.relation, hospital_name: hospital.name,
              hospital_city: hospital.address.city, hospital_state: hospital.address.state,
              hospital_tier: hospital.city_tier, network_hospital: true },
  primary_icd: 'A90', diagnosis: [{ icd_code: 'A90', description: 'Dengue fever', type: 'Primary' }],
  treatment: { specialty: 'General Medicine', line: 'Medical', procedure: 'Conservative medical management' },
  admission_date: admitted, discharge_date: new Date(admitted.getTime() + 3 * 86400000), length_of_stay: 3,
  icu_days: 0, room_category: 'Twin Sharing', room_rent_per_day: 3000, bill, claimed_amount: bill.total,
  approved_amount: 0, settled_amount: 0, intimation_date: now, submitted_at: null,
  documents: [{ type: 'Discharge Summary', status: 'Pending' }, { type: 'Final Hospital Bill', status: 'Pending' }],
  status_history: [{ status: 'Intimated', at: now, by: 'HOSPITAL', by_name: hospital.name, remarks: 'Pre-auth request' }],
  fraud: { score: 0, risk_level: 'Low', flags: [] }, created_at: now, updated_at: now,
});
print(`Created ${claimNumber} for ${member.name} at ${hospital.name} (claimed Rs ${bill.total})`);

title('CREATE: an invalid document is rejected by the $jsonSchema validator');
try {
  db.claims.insertOne({ claim_number: 'BAD-1', status: 'Paid', claimed_amount: -10 });
} catch (e) {
  print(`Rejected: ${e.codeName} (code ${e.code})`);
  printjson(e.errInfo?.details?.schemaRulesNotSatisfied?.slice(0, 2));
}

// ---------------------------------------------------------------- READ
title('READ: equality + range + sort + projection');
db.claims.find({ status: 'Settled', claimed_amount: { $gt: 500000 } },
               { _id: 0, claim_number: 1, 'snapshot.member_name': 1, claimed_amount: 1, settled_amount: 1 })
  .sort({ claimed_amount: -1 }).limit(5).forEach(printjson);

title('READ: array query - members with BOTH diabetes and hypertension ($all)');
print(db.members.countDocuments({ pre_existing_conditions: { $all: ['Diabetes', 'Hypertension'] } }) + ' members');

title('READ: $elemMatch on embedded deductions');
db.claims.find({ 'adjudication.deductions': { $elemMatch: { category: 'Room Rent Excess', amount: { $gte: 50000 } } } },
               { _id: 0, claim_number: 1, room_category: 1, 'adjudication.deductions.$': 1 }).limit(3).forEach(printjson);

title('READ: policies covering a member (multikey index on insured_members.member_id)');
db.policies.find({ 'insured_members.member_id': member.member_id }, { _id: 0, policy_number: 1, policy_type: 1, sum_insured: 1 })
  .forEach(printjson);

title('READ: claim with patient & hospital via $lookup');
db.claims.aggregate([
  { $match: { claim_number: claimNumber } },
  { $lookup: { from: 'members', localField: 'member_id', foreignField: 'member_id', as: 'member' } },
  { $lookup: { from: 'hospitals', localField: 'hospital_id', foreignField: 'hospital_id', as: 'hospital' } },
  { $project: { _id: 0, claim_number: 1, status: 1, 'member.name': 1, 'member.contact.phone': 1,
                'hospital.name': 1, 'hospital.accreditation': 1 } },
]).forEach(printjson);

// ---------------------------------------------------------------- UPDATE
title('UPDATE: move to Under Review - $set + $push to the embedded history in ONE atomic write');
db.claims.updateOne({ claim_number: claimNumber, status: 'Intimated' }, {
  $set: { status: 'Under Review', submitted_at: new Date(), assigned_to: 'USR011', updated_at: new Date() },
  $push: { status_history: { status: 'Under Review', at: new Date(), by: 'USR011', by_name: 'Neha Kapoor',
                             remarks: 'Documents received' } },
});

title('UPDATE: arrayFilters - mark every pending document as Verified');
db.claims.updateOne({ claim_number: claimNumber },
  { $set: { 'documents.$[d].status': 'Verified', 'documents.$[d].uploaded_at': new Date() } },
  { arrayFilters: [{ 'd.status': 'Pending' }] });
printjson(db.claims.findOne({ claim_number: claimNumber }, { _id: 0, status: 1, documents: 1 }));

title('UPDATE: $inc on a counter + upsert');
db.counters.updateOne({ _id: 'demo_runs' }, { $inc: { seq: 1 } }, { upsert: true });
printjson(db.counters.findOne({ _id: 'demo_runs' }));

// ---------------------------------------------------------------- DELETE
title('DELETE: soft delete (withdraw) keeps the audit trail');
db.claims.updateOne({ claim_number: claimNumber }, {
  $set: { status: 'Withdrawn', assigned_to: null },
  $push: { status_history: { status: 'Withdrawn', at: new Date(), by: 'MEMBER', by_name: member.name,
                             remarks: 'Withdrawn by insured' } },
});
print(`Status now: ${db.claims.findOne({ claim_number: claimNumber }).status}`);

title('DELETE: hard delete of the demo claim and counter');
print(`Deleted ${db.claims.deleteOne({ claim_number: claimNumber }).deletedCount} claim, ` +
      `${db.counters.deleteOne({ _id: 'demo_runs' }).deletedCount} counter`);
