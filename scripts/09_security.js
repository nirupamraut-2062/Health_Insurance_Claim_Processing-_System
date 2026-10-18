// ClaimSure - 09. Users, roles and least privilege (self-managed MongoDB)
//   mongosh "mongodb://admin:PASSWORD@localhost:27017/admin" --file scripts/09_security.js
// Start mongod with --auth (and --keyFile for a replica set) for these roles to be enforced.
// On Atlas, create the same users under Security > Database Access instead
// (built-in roles "readWrite"/"read" on database claimsure, or custom roles).

const admin = db.getSiblingDB('admin');
const app = db.getSiblingDB('claimsure');
const pwd = (name) => `${name}-change-me`;   // replace before real use

// Custom role: auditors may read the audit trail and nothing else
['claimsure_auditor_role'].forEach(r => { if (app.getRole(r)) app.dropRole(r); });
app.createRole({
  role: 'claimsure_auditor_role',
  privileges: [{ resource: { db: 'claimsure', collection: 'audit_logs' }, actions: ['find'] }],
  roles: [],
});

const users = [
  { user: 'claimsure_app',     roles: [{ role: 'readWrite', db: 'claimsure' }] },
  { user: 'claimsure_analyst', roles: [{ role: 'read', db: 'claimsure' }] },
  { user: 'claimsure_auditor', roles: [{ role: 'claimsure_auditor_role', db: 'claimsure' }] },
  { user: 'claimsure_dba',     roles: [{ role: 'dbAdmin', db: 'claimsure' }, { role: 'readWrite', db: 'claimsure' }] },
];
users.forEach(u => {
  if (app.getUser(u.user)) app.dropUser(u.user);
  app.createUser({ user: u.user, pwd: pwd(u.user), roles: u.roles });
  print(`created ${u.user.padEnd(18)} ${JSON.stringify(u.roles)}`);
});

// Prove it: connect as the analyst and try to write
const analyst = connect(`mongodb://claimsure_analyst:${pwd('claimsure_analyst')}@localhost:27017/claimsure`);
print(`\nAnalyst can read: ${analyst.claims.countDocuments()} claims`);
try {
  analyst.claims.insertOne({ claim_number: 'HACK-1' });
} catch (e) {
  print(`Analyst insert refused by the server: ${e.codeName} (code ${e.code})`);
}
void admin;
