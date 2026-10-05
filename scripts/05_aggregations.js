// ClaimSure - 05. Advanced aggregation pipelines (MongoDB 7.0+ / Atlas)
//   mongosh "mongodb://localhost:27017" --file scripts/05_aggregations.js
// These use server features that the in-memory mock cannot run:
// $setWindowFields, $dateDiff, $dateTrunc, $round, $percentile, $lookup with a sub-pipeline, $merge.

db = db.getSiblingDB('claimsure');
const title = t => print(`\n===== ${t} =====`);

title('1. Executive KPIs in one round trip ($facet)');
printjson(db.claims.aggregate([{ $facet: {
  totals: [{ $group: { _id: null, claims: { $sum: 1 }, claimed: { $sum: '$claimed_amount' }, paid: { $sum: '$settled_amount' } } },
           { $project: { _id: 0, claims: 1, claimed: 1, paid: 1, payout_ratio: { $round: [{ $multiply: [{ $divide: ['$paid', '$claimed'] }, 100] }, 1] } } }],
  by_status: [{ $sortByCount: '$status' }],
  by_type: [{ $group: { _id: '$claim_type', claims: { $sum: 1 }, avg_bill: { $avg: '$claimed_amount' } } },
            { $set: { avg_bill: { $round: ['$avg_bill', 0] } } }],
} }]).toArray()[0]);

title('2. Monthly payout with running total and 3-month moving average ($setWindowFields)');
db.claims.aggregate([
  { $match: { status: 'Settled' } },
  { $group: { _id: { $dateTrunc: { date: '$settled_at', unit: 'month' } }, paid: { $sum: '$settled_amount' }, claims: { $sum: 1 } } },
  { $setWindowFields: { sortBy: { _id: 1 }, output: {
      cumulative_paid: { $sum: '$paid', window: { documents: ['unbounded', 'current'] } },
      moving_avg_3m: { $avg: '$paid', window: { documents: [-2, 0] } } } } },
  { $project: { _id: 0, month: { $dateToString: { date: '$_id', format: '%Y-%m' } }, claims: 1, paid: 1,
                cumulative_paid: 1, moving_avg_3m: { $round: ['$moving_avg_3m', 0] } } },
]).forEach(r => print(`  ${r.month}  claims ${String(r.claims).padStart(4)}  paid ${String(r.paid).padStart(10)}  cumulative ${String(r.cumulative_paid).padStart(11)}  3m avg ${r.moving_avg_3m}`));

title('3. Turnaround time computed on the fly ($dateDiff) with median and p90 ($percentile)');
db.claims.aggregate([
  { $match: { decided_at: { $ne: null }, submitted_at: { $ne: null } } },
  { $set: { tat_hours: { $dateDiff: { startDate: '$submitted_at', endDate: '$decided_at', unit: 'hour' } } } },
  { $group: { _id: '$claim_type', claims: { $sum: 1 }, avg_hours: { $avg: '$tat_hours' },
              pct: { $percentile: { input: '$tat_hours', p: [0.5, 0.9], method: 'approximate' } } } },
  { $project: { claims: 1, avg_days: { $round: [{ $divide: ['$avg_hours', 24] }, 1] },
                median_days: { $round: [{ $divide: [{ $arrayElemAt: ['$pct', 0] }, 24] }, 1] },
                p90_days: { $round: [{ $divide: [{ $arrayElemAt: ['$pct', 1] }, 24] }, 1] } } },
]).forEach(printjson);

title('4. Insurer scorecard: correlated $lookup sub-pipeline for premium earned');
db.claims.aggregate([
  { $group: { _id: '$insurer_id', claims: { $sum: 1 }, paid: { $sum: '$settled_amount' },
              settled: { $sum: { $cond: [{ $eq: ['$status', 'Settled'] }, 1, 0] } },
              rejected: { $sum: { $cond: [{ $eq: ['$status', 'Rejected'] }, 1, 0] } } } },
  { $lookup: { from: 'insurers', localField: '_id', foreignField: 'insurer_id', as: 'insurer' } },
  { $lookup: { from: 'policies', let: { ins: '$_id' }, as: 'premium', pipeline: [
      { $match: { $expr: { $eq: ['$insurer_id', '$$ins'] } } },
      { $unwind: '$renewal_history' },
      { $match: { 'renewal_history.end_date': { $gte: ISODate('2024-04-01') } } },
      { $group: { _id: null, earned: { $sum: '$renewal_history.premium.total' } } }] } },
  { $project: { _id: 0, insurer: { $first: '$insurer.name' }, claims: 1,
                settlement_ratio: { $round: [{ $multiply: [{ $divide: ['$settled', { $add: ['$settled', '$rejected'] }] }, 100] }, 1] },
                incurred_claim_ratio: { $round: [{ $multiply: [{ $divide: ['$paid', { $first: '$premium.earned' }] }, 100] }, 1] } } },
  { $sort: { incurred_claim_ratio: -1 } },
]).forEach(printjson);

title('5. Rank hospitals by payout within each state ($rank window)');
db.claims.aggregate([
  { $group: { _id: { state: '$snapshot.hospital_state', hospital: '$snapshot.hospital_name' }, paid: { $sum: '$settled_amount' } } },
  { $setWindowFields: { partitionBy: '$_id.state', sortBy: { paid: -1 }, output: { rank: { $rank: {} } } } },
  { $match: { rank: 1 } },
  { $project: { _id: 0, state: '$_id.state', top_hospital: '$_id.hospital', paid: 1 } },
  { $sort: { paid: -1 } },
]).forEach(printjson);

title('6. Diagnosis cost vs benchmark ($unwind + $lookup + $round)');
db.claims.aggregate([
  { $unwind: '$diagnosis' },
  { $match: { 'diagnosis.type': 'Primary' } },
  { $group: { _id: '$diagnosis.icd_code', claims: { $sum: 1 }, avg_bill: { $avg: '$claimed_amount' } } },
  { $sort: { claims: -1 } }, { $limit: 10 },
  { $lookup: { from: 'icd_codes', localField: '_id', foreignField: 'code', as: 'icd' } },
  { $unwind: '$icd' },
  { $project: { _id: 0, code: '$_id', diagnosis: '$icd.description', claims: 1, avg_bill: { $round: ['$avg_bill', 0] },
                benchmark: '$icd.avg_cost',
                over_benchmark_pct: { $round: [{ $multiply: [{ $divide: [{ $subtract: ['$avg_bill', '$icd.avg_cost'] }, '$icd.avg_cost'] }, 100] }, 1] } } },
]).forEach(printjson);

title('7. Family claim exposure ($lookup members -> group by family)');
db.claims.aggregate([
  { $match: { status: 'Settled' } },
  { $group: { _id: '$member_id', paid: { $sum: '$settled_amount' } } },
  { $lookup: { from: 'members', localField: '_id', foreignField: 'member_id', as: 'm', pipeline: [{ $project: { family_id: 1, name: 1 } }] } },
  { $group: { _id: { $first: '$m.family_id' }, members_claimed: { $sum: 1 }, paid: { $sum: '$paid' } } },
  { $sort: { paid: -1 } }, { $limit: 5 },
]).forEach(printjson);

title('8. On-demand materialized view ($merge) - refreshed by re-running this pipeline');
db.claims.aggregate([
  { $group: { _id: { insurer: '$insurer_id', month: { $dateToString: { date: '$admission_date', format: '%Y-%m' } } },
              claims: { $sum: 1 }, claimed: { $sum: '$claimed_amount' }, paid: { $sum: '$settled_amount' } } },
  { $set: { refreshed_at: '$$NOW' } },
  { $merge: { into: 'mv_insurer_monthly', on: '_id', whenMatched: 'replace', whenNotMatched: 'insert' } },
]);
print(`mv_insurer_monthly now holds ${db.mv_insurer_monthly.countDocuments()} documents`);
printjson(db.mv_insurer_monthly.find().sort({ '_id.month': -1 }).limit(2).toArray());
