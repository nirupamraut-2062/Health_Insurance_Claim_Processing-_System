from db_config import USE_MOCK

def run_transaction_demo(db, client=None):
    print("\n" + "="*50)
    print("  [ACID] RUNNING ACID TRANSACTION DEMO")
    print("="*50)

    # Find a claim to settle
    claim = db.claims.find_one({'status': 'Approved', 'approved_amount': {'$gt': 0}})
    if not claim:
        print("No 'Approved' claim found to process.")
        return

    claim_id = claim.get('claim_id')
    policy_id = claim.get('policy_id')
    approved_amount = claim.get('approved_amount', 0)

    print(f"\nTarget Claim: {claim_id}")
    print(f"Policy ID: {policy_id}")
    print(f"Approved Amount: ${approved_amount}")

    if USE_MOCK or not client:
        print("\nNote: Running with mongomock or no client provided.")
        print("Transactions (Sessions) are not fully supported in mongomock.")
        print("\nSimulating Transaction Flow:")
        print(f"1. Start Session & Transaction")
        print(f"2. Update claim {claim_id} status to 'Settled'")
        print(f"3. Deduct ${approved_amount} from policy {policy_id} remaining_coverage")
        print(f"4. Commit Transaction")
        
        # Perform non-transactional update for mock
        db.claims.update_one({'claim_id': claim_id}, {'$set': {'status': 'Settled'}})
        db.policies.update_one({'policy_id': policy_id}, {'$inc': {'remaining_coverage': -approved_amount}})
        print("\nMock update completed successfully.")
        return

    # Real Transaction execution
    try:
        with client.start_session() as session:
            with session.start_transaction():
                print("\n1. Started Session & Transaction")
                
                # Update claim
                db.claims.update_one(
                    {'claim_id': claim_id},
                    {'$set': {'status': 'Settled'}},
                    session=session
                )
                print(f"2. Updated claim {claim_id} status to 'Settled'")
                
                # Check policy coverage
                policy = db.policies.find_one({'policy_id': policy_id}, session=session)
                if not policy:
                    raise Exception(f"Policy {policy_id} not found.")
                
                coverage = policy.get('remaining_coverage', 0)
                print(f"Current Policy Coverage: ${coverage}")
                
                if coverage < approved_amount:
                    print("Insufficient coverage! Aborting transaction.")
                    raise Exception("Insufficient coverage")
                
                # Update policy
                db.policies.update_one(
                    {'policy_id': policy_id},
                    {'$inc': {'remaining_coverage': -approved_amount}},
                    session=session
                )
                print(f"3. Deducted ${approved_amount} from policy {policy_id} remaining_coverage")
                
                print("4. Committing Transaction...")
        
        print("\nTransaction committed successfully!")
    except Exception as e:
        print(f"\nTransaction failed and aborted: {e}")
