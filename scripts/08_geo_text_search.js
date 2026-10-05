// ClaimSure - 08. Geospatial and full-text search
//   mongosh "mongodb://localhost:27017" --file scripts/08_geo_text_search.js

db = db.getSiblingDB('claimsure');

// --- Geospatial: nearest cashless hospitals for a member (2dsphere index on hospitals.location)
const here = { type: 'Point', coordinates: [79.1559, 12.9692] };   // Katpadi, Vellore
const insurer = 'INS02';
print(`Nearest network hospitals of ${insurer} within 150 km of [${here.coordinates}]:`);
db.hospitals.aggregate([
  { $geoNear: { near: here, distanceField: 'distance_m', maxDistance: 150000, spherical: true,
                query: { 'network.insurer_ids': insurer } } },
  { $limit: 5 },
  { $project: { _id: 0, name: 1, city: '$address.city', type: 1, km: { $round: [{ $divide: ['$distance_m', 1000] }, 1] } } },
]).forEach(printjson);

print('\nHospitals within 25 km of central Bengaluru ($geoWithin + $centerSphere):');
print(db.hospitals.countDocuments({ location: { $geoWithin: { $centerSphere: [[77.5946, 12.9716], 25 / 6378.1] } } }));

// --- Full-text search (text index over patient, hospital, diagnosis and procedure)
for (const q of ['cataract', 'laparoscopic', '"knee replacement"', 'dengue -typhoid']) {
  print(`\nText search: ${q}`);
  db.claims.find({ $text: { $search: q } },
                 { _id: 0, claim_number: 1, 'snapshot.member_name': 1, primary_icd: 1, score: { $meta: 'textScore' } })
    .sort({ score: { $meta: 'textScore' } }).limit(3).forEach(printjson);
}
