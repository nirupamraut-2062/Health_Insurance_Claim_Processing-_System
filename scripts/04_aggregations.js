// Advanced Aggregation Pipelines
use('health_insurance_db');

// ===== PIPELINE 1: Claim Status Distribution & Average Amounts =====
print('\n===== Pipeline 1: Claim Status Distribution =====');
db.claims.aggregate([
  { $group: {
      _id: '$status',
      count: { $sum: 1 },
      total_billed: { $sum: '$billed_amount' },
      avg_billed: { $avg: '$billed_amount' },
      total_approved: { $sum: '$approved_amount' }
  }},
  { $sort: { count: -1 } },
  { $project: {
      status: '$_id', _id: 0, count: 1,
      total_billed: { $round: ['$total_billed', 2] },
      avg_billed: { $round: ['$avg_billed', 2] },
      total_approved: { $round: ['$total_approved', 2] }
  }}
]).forEach(printjson);

// ===== PIPELINE 2: Hospital Performance & Claim Volume =====
print('\n===== Pipeline 2: Hospital Claim Volume & Payout =====');
db.claims.aggregate([
  { $lookup: { from: 'hospitals', localField: 'hospital_id', foreignField: 'hospital_id', as: 'hospital' } },
  { $unwind: '$hospital' },
  { $group: {
      _id: { hospital_id: '$hospital_id', hospital_name: '$hospital.name', city: '$hospital.city' },
      total_claims: { $sum: 1 },
      total_billed: { $sum: '$billed_amount' },
      total_approved: { $sum: '$approved_amount' },
      avg_claim_value: { $avg: '$billed_amount' }
  }},
  { $sort: { total_claims: -1 } },
  { $project: {
      _id: 0,
      hospital: '$_id.hospital_name',
      city: '$_id.city',
      total_claims: 1,
      total_billed: { $round: ['$total_billed', 2] },
      total_approved: { $round: ['$total_approved', 2] },
      avg_claim_value: { $round: ['$avg_claim_value', 2] }
  }}
]).forEach(printjson);

// ===== PIPELINE 3: Insurer-wise Approval vs Rejection Rate =====
print('\n===== Pipeline 3: Insurer Approval & Rejection Rates =====');
db.claims.aggregate([
  { $lookup: { from: 'policies', localField: 'policy_id', foreignField: 'policy_id', as: 'policy' } },
  { $unwind: '$policy' },
  { $lookup: { from: 'insurers', localField: 'policy.insurer_id', foreignField: 'insurer_id', as: 'insurer' } },
  { $unwind: '$insurer' },
  { $group: {
      _id: { insurer_id: '$policy.insurer_id', insurer_name: '$insurer.name' },
      total_claims: { $sum: 1 },
      approved: { $sum: { $cond: [{ $eq: ['$status', 'Approved'] }, 1, 0] } },
      rejected: { $sum: { $cond: [{ $eq: ['$status', 'Rejected'] }, 1, 0] } },
      settled: { $sum: { $cond: [{ $eq: ['$status', 'Settled'] }, 1, 0] } },
      total_payout: { $sum: '$approved_amount' }
  }},
  { $project: {
      _id: 0,
      insurer: '$_id.insurer_name',
      total_claims: 1,
      approved: 1, rejected: 1, settled: 1,
      approval_rate: { $round: [{ $multiply: [{ $divide: [{ $add: ['$approved', '$settled'] }, '$total_claims'] }, 100] }, 1] },
      rejection_rate: { $round: [{ $multiply: [{ $divide: ['$rejected', '$total_claims'] }, 100] }, 1] },
      total_payout: { $round: ['$total_payout', 2] }
  }}
]).forEach(printjson);

// ===== PIPELINE 4: Top Diagnosis Categories (using $unwind) =====
print('\n===== Pipeline 4: Top Diagnosis Categories =====');
db.claims.aggregate([
  { $unwind: '$diagnosis' },
  { $group: {
      _id: { icd_code: '$diagnosis.icd_code', description: '$diagnosis.description' },
      claim_count: { $sum: 1 },
      total_billed: { $sum: '$billed_amount' },
      avg_cost: { $avg: '$billed_amount' }
  }},
  { $sort: { claim_count: -1 } },
  { $limit: 10 },
  { $project: {
      _id: 0,
      icd_code: '$_id.icd_code',
      diagnosis: '$_id.description',
      claim_count: 1,
      total_billed: { $round: ['$total_billed', 2] },
      avg_cost: { $round: ['$avg_cost', 2] }
  }}
]).forEach(printjson);

// ===== PIPELINE 5: Monthly Claim Trends =====
print('\n===== Pipeline 5: Monthly Claim Inflow Trends =====');
db.claims.aggregate([
  { $addFields: { claim_date_parsed: { $dateFromString: { dateString: '$claim_date' } } } },
  { $group: {
      _id: { year: { $year: '$claim_date_parsed' }, month: { $month: '$claim_date_parsed' } },
      claim_count: { $sum: 1 },
      total_billed: { $sum: '$billed_amount' },
      avg_billed: { $avg: '$billed_amount' }
  }},
  { $sort: { '_id.year': 1, '_id.month': 1 } },
  { $project: {
      _id: 0,
      month: { $concat: [{ $toString: '$_id.year' }, '-', { $cond: { if: { $lt: ['$_id.month', 10] }, then: { $concat: ['0', { $toString: '$_id.month' }] }, else: { $toString: '$_id.month' } } }] },
      claim_count: 1,
      total_billed: { $round: ['$total_billed', 2] },
      avg_billed: { $round: ['$avg_billed', 2] }
  }}
]).forEach(printjson);

print('\n✅ All 5 aggregation pipelines executed successfully');
