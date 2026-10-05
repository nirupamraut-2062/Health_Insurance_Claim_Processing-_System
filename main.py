"""ClaimSure command-line entry point.

    python main.py              interactive console (menu)
    python main.py web          start the web dashboard on http://127.0.0.1:8000
    python main.py seed         (re)load data/*.json into the database
    python main.py init-db      apply validators + indexes + data on a real MongoDB
    python main.py generate     regenerate the synthetic data set
    python main.py report       print the KPI summary and key reports
    python main.py benchmark    run the index benchmark
    python main.py test         run the automated test-suite
"""
import argparse
import os
import sys
import threading
import time
import webbrowser

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles and the ₹ sign

from colorama import Fore, Style, init as colorama_init
from tabulate import tabulate

from claimsure import APP_NAME, APP_TAGLINE, VERSION, analytics, indexes, services
from claimsure.constants import (OPEN_STATUSES, AWAITING_PAYMENT, REJECTION_REASONS, MANUAL_REJECTION_CODES,
                                 UNDER_REVIEW, ESCALATED)
from claimsure.db import get_db, ensure_seeded, is_mock, server_info
from claimsure.schema import validate
from claimsure.seed import seed, COLLECTIONS
from claimsure.utils import inr, inr_short, fmt_date

colorama_init(autoreset=True)
C, G, Y, R, B, M, DIM, BOLD = (Fore.CYAN, Fore.GREEN, Fore.YELLOW, Fore.RED, Fore.BLUE, Fore.MAGENTA,
                               Style.DIM, Style.BRIGHT)
WEB_PORT = 8000
_web_thread = None


# --------------------------------------------------------------------------- #
# console helpers
# --------------------------------------------------------------------------- #
def header(title):
    print(f"\n{B}{BOLD}{'=' * 78}\n  {title}\n{'=' * 78}{Style.RESET_ALL}")


def ask(prompt, default=""):
    try:
        value = input(f"{C}{prompt}{Style.RESET_ALL}" + (f" [{default}]" if default else "") + ": ").strip()
    except EOFError:
        value = ""
    return value or default


def pause():
    ask("\nPress Enter to continue")


def table(rows, headers):
    print(tabulate(rows, headers=headers, tablefmt="rounded_outline", disable_numparse=True))


def acting_user(db, preferred="USR003"):
    return services.get_user(db, preferred)


# --------------------------------------------------------------------------- #
# menu actions
# --------------------------------------------------------------------------- #
def show_summary(db):
    header("Database summary")
    info = server_info(db)
    print(f"  Engine: {BOLD}{info['engine']}{Style.RESET_ALL}   database: {info['database']}   "
          f"transactions: {info['transactions']}   validation: {info['validation']}")
    rows = [(c, f"{db[c].estimated_document_count():,}", len(db[c].index_information())) for c in COLLECTIONS]
    table(rows, ["Collection", "Documents", "Indexes"])
    k = analytics.kpis(db)
    print(f"\n  {BOLD}Claims{Style.RESET_ALL} {k['total_claims']:,}  |  open {k['open']}  |  awaiting payment "
          f"{k['awaiting_payment']}  |  paid {inr_short(k['settled'])}  |  settlement ratio {k['settlement_ratio']}%")
    print(f"  {BOLD}Policies{Style.RESET_ALL} {k['active_policies']:,} active covering {k['lives_covered']:,} lives  |  "
          f"premium in force {inr_short(k['premium_in_force'])}")


def print_claims(rows):
    table([(c["claim_number"], c["status"], c["claim_type"], c["snapshot"]["member_name"],
            c["snapshot"]["hospital_name"][:28], c["primary_icd"], inr(c["claimed_amount"]),
            (c.get("fraud") or {}).get("risk_level", "-")) for c in rows],
          ["Claim", "Status", "Type", "Patient", "Hospital", "ICD", "Claimed", "Risk"])


def search_claims(db):
    header("Search claims")
    term = ask("Claim number, patient or hospital name")
    if not term:
        return
    import re
    rx = {"$regex": re.escape(term), "$options": "i"}
    rows = list(db.claims.find({"$or": [{"claim_number": rx}, {"snapshot.member_name": rx},
                                        {"snapshot.hospital_name": rx}]}).sort("intimation_date", -1).limit(15))
    print_claims(rows)
    if rows:
        number = ask("Open which claim (number, blank to skip)", rows[0]["claim_number"] if len(rows) == 1 else "")
        if number:
            show_claim(db, number.upper())


def show_claim(db, number):
    try:
        claim = services.get_claim(db, number)
    except services.WorkflowError as exc:
        print(f"{R}{exc}")
        return None
    header(f"{claim['claim_number']}  -  {claim['status']}")
    s = claim["snapshot"]
    print(f"  Patient : {s['member_name']} ({s['member_age']} yrs, {s['relation']})   policy {claim['policy_number']}")
    print(f"  Hospital: {s['hospital_name']}, {s['hospital_city']}  ({'network' if s['network_hospital'] else 'non-network'})")
    print(f"  Stay    : {fmt_date(claim['admission_date'])} -> {fmt_date(claim['discharge_date'])}  "
          f"{claim['length_of_stay']} day(s), {claim['room_category']} @ {inr(claim['room_rent_per_day'])}/day")
    print(f"  Diagnosis: {claim['diagnosis'][0]['icd_code']} {claim['diagnosis'][0]['description']}  |  "
          f"{claim['treatment']['procedure']}")
    result = claim.get("adjudication") or services.preview_adjudication(db, claim)
    label = "Recorded decision" if claim.get("adjudication") else "Adjudication preview (not saved)"
    print(f"\n  {BOLD}{label}: {result['decision']}{Style.RESET_ALL}")
    table([(k.replace("_", " ").title(), inr(v)) for k, v in claim["bill"].items() if v], ["Bill head", "Amount"])
    if result["eligible"]:
        if result["deductions"]:
            table([(d["category"], d["description"][:60], "-" + inr(d["amount"])) for d in result["deductions"]],
                  ["Deduction", "Reason", "Amount"])
        print(f"  Payable: {G}{BOLD}{inr(result['payable_amount'])}{Style.RESET_ALL}   member pays {inr(result['member_payable'])}")
    else:
        print(f"  {R}Rejected: {result['rejection_code']} - {result['rejection_reason']}")
    f = claim.get("fraud") or {}
    print(f"  Fraud score {f.get('score', 0)} ({f.get('risk_level', '-')}): "
          + (", ".join(fl["code"] for fl in f.get("flags", [])) or "no red flags"))
    print(f"\n  {BOLD}Timeline{Style.RESET_ALL}")
    for h in claim["status_history"]:
        print(f"   {DIM}{fmt_date(h['at'], True):>20}{Style.RESET_ALL}  {h['status']:<19} {h['by_name'][:26]:<26} {h['remarks'][:60]}")
    return claim


def work_queue(db):
    header("Work queue")
    rows = list(db.claims.aggregate([
        {"$match": {"status": {"$in": OPEN_STATUSES + AWAITING_PAYMENT}}},
        {"$group": {"_id": "$status", "claims": {"$sum": 1}, "amount": {"$sum": "$claimed_amount"}}},
        {"$sort": {"claims": -1}}]))
    table([(r["_id"], r["claims"], inr(r["amount"])) for r in rows], ["Status", "Claims", "Amount claimed"])
    status = ask("Show claims in which status", UNDER_REVIEW)
    print_claims(list(db.claims.find({"status": status}).sort("intimation_date", 1).limit(20)))


def process_claim(db):
    header("Process a claim")
    users = list(db.users.find({"role": {"$ne": "Fraud Analyst"}}, {"_id": 0}).sort("approval_limit", 1))
    table([(i + 1, u["name"], u["role"], inr(u["approval_limit"])) for i, u in enumerate(users)],
          ["#", "User", "Role", "Approval limit"])
    pick = ask("Act as user #", "1")
    user = users[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(users) else users[0]
    sample = db.claims.find_one({"status": {"$in": [UNDER_REVIEW, ESCALATED] + AWAITING_PAYMENT}})
    number = ask("Claim number", sample["claim_number"] if sample else "").upper()
    claim = show_claim(db, number)
    if not claim:
        return
    actions = services.available_actions(claim, user)
    options = [(t, "status") for t in actions["transitions"]]
    if actions["can_adjudicate"]:
        options += [("Adjudicate (accept engine result)", "adjudicate")]
        options += [(f"Reject: {REJECTION_REASONS[c]}", c) for c in MANUAL_REJECTION_CODES]
    if actions["can_settle"]:
        options += [("Settle (ACID transaction)", "settle"), ("Settle with simulated failure", "settle_fail")]
    if not options:
        print(f"{Y}No actions available for a {claim['status']} claim as {user['role']}.")
        return
    print()
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}. {label}")
    choice = ask("Action #")
    if not choice.isdigit() or not 0 < int(choice) <= len(options):
        return
    label, kind = options[int(choice) - 1]
    try:
        if kind == "status":
            remarks = ask("Remarks / query text", "")
            services.change_status(db, number, label, user, remarks)
            print(f"{G}Moved to {label}")
        elif kind in ("settle", "settle_fail"):
            out = services.settle_claim(db, number, user, simulate_failure=kind == "settle_fail")
            for step, detail in out["steps"]:
                color = R if step in ("ROLLBACK",) else (G if step == "COMMIT" else "")
                print(f"  {color}{step:<20}{Style.RESET_ALL} {detail}")
        else:
            out = services.adjudicate_claim(db, number, user, None if kind == "adjudicate" else kind)
            print((Y if out["escalated"] else G) + out["message"])
    except services.WorkflowError as exc:
        print(f"{R}{exc}")


def new_claim(db):
    header("Intimate a new claim")
    sample = db.policies.find_one({"status": "Active", "policy_type": "Family Floater"})
    number = ask("Policy number", sample["policy_number"]).upper()
    policy = db.policies.find_one({"policy_number": number})
    if not policy:
        print(f"{R}Policy not found")
        return
    table([(i + 1, m["name"], m["relation"], m["member_id"]) for i, m in enumerate(policy["insured_members"])],
          ["#", "Insured", "Relation", "Member id"])
    idx = ask("Patient #", "1")
    member = policy["insured_members"][int(idx) - 1 if idx.isdigit() and 0 < int(idx) <= len(policy["insured_members"]) else 0]
    city = db.members.find_one({"member_id": member["member_id"]})["address"]["city"]
    hospitals = list(db.hospitals.find({"address.city": city}).limit(8)) or list(db.hospitals.find().limit(8))
    table([(i + 1, h["name"], h["address"]["city"], "yes" if policy["insurer_id"] in h["network"]["insurer_ids"] else "no")
           for i, h in enumerate(hospitals)], ["#", "Hospital", "City", "Network"])
    hidx = ask("Hospital #", "1")
    hospital = hospitals[int(hidx) - 1 if hidx.isdigit() and 0 < int(hidx) <= len(hospitals) else 0]
    term = ask("Diagnosis search (e.g. dengue, appendicitis, cataract)", "dengue")
    import re
    icds = list(db.icd_codes.find({"description": {"$regex": re.escape(term), "$options": "i"}}).limit(8)) \
        or list(db.icd_codes.find().limit(8))
    table([(i + 1, x["code"], x["description"], inr(x["avg_cost"]), x["avg_los"]) for i, x in enumerate(icds)],
          ["#", "ICD", "Description", "Benchmark", "Avg stay"])
    iidx = ask("Diagnosis #", "1")
    icd = icds[int(iidx) - 1 if iidx.isdigit() and 0 < int(iidx) <= len(icds) else 0]
    from datetime import datetime, timedelta
    los = max(icd["avg_los"], 1)
    adm = datetime.now() - timedelta(days=los + 2)
    admission = ask("Admission date (YYYY-MM-DD)", adm.strftime("%Y-%m-%d"))
    discharge = ask("Discharge date (YYYY-MM-DD)", (adm + timedelta(days=los)).strftime("%Y-%m-%d"))
    total = int(ask("Total bill amount (Rs)", str(icd["avg_cost"])) or icd["avg_cost"])
    network = policy["insurer_id"] in hospital["network"]["insurer_ids"]
    split = {"room_charges": .2, "nursing_charges": .06, "doctor_fees": .08, "surgeon_ot_charges": .2 if icd["is_surgical"] else 0,
             "investigations": .14, "medicines": .22, "consumables": .07, "admin_charges": .02}
    norm = sum(split.values())
    form = {k: int(total * v / norm) for k, v in split.items()}
    form.update(policy_number=number, member_id=member["member_id"], hospital_id=hospital["hospital_id"],
                primary_icd=icd["code"], claim_type="Cashless" if network else "Reimbursement",
                admission_type="Planned", admission_date=admission, discharge_date=discharge,
                room_category="Twin Sharing", documents_complete="1")
    try:
        number = services.intimate_claim(db, form, acting_user(db))
        print(f"\n{G}{BOLD}Claim {number} created.{Style.RESET_ALL} Use 'Process a claim' to review and decide it.")
    except Exception as exc:
        print(f"{R}{exc}")


def reports_menu(db):
    header("Analytics reports (MongoDB aggregation pipelines)")
    keys = list(analytics.REPORTS)
    for i, k in enumerate(keys, 1):
        print(f"  {i:2d}. {analytics.REPORTS[k]['title']}")
    choice = ask("Report #", "1")
    if not choice.isdigit() or not 0 < int(choice) <= len(keys):
        return
    rep = print_report(db, keys[int(choice) - 1])
    if ask("Show the aggregation pipeline? (y/N)", "n").lower() == "y":
        from bson import json_util
        for p in rep["pipelines"]:
            print(json_util.dumps(p, indent=2))
    print(f"\n  {DIM}{rep['ms']} ms{Style.RESET_ALL}")


MONEY_FIELDS = {"claimed", "settled", "paid", "amount", "premium", "avg_claimed", "avg_bill", "benchmark", "avg_cover"}


def print_report(db, key):
    rep = analytics.run_report(db, key)
    header(rep["title"])
    print(f"  {DIM}{rep['description']}{Style.RESET_ALL}\n")
    if rep["rows"]:
        table([[inr(v) if k in MONEY_FIELDS else v for k, v in r.items()] for r in rep["rows"]],
              [k.replace("_", " ") for k in rep["rows"][0]])
    return rep


def fraud_menu(db):
    header("Fraud watch-list (score >= 60)")
    print_claims(list(db.claims.find({"fraud.score": {"$gte": 60}}).sort("fraud.score", -1).limit(20)))
    rep = analytics.run_report(db, "fraud_flags")
    table([(r["rule"], r["claims"], r["avg_score"], r["description"]) for r in rep["rows"]],
          ["Rule", "Claims", "Avg score", "Description"])


def benchmark_menu(db):
    header("Index benchmark" + (" (simulated plans - mock mode)" if is_mock() else " (explain executionStats)"))
    rows = indexes.benchmark(db)
    table([(r["title"], r["before"]["stages"], r["after"]["stages"], r["after"]["index"],
            f"{r['before']['docs_examined']:,} -> {r['after']['docs_examined']:,}", f"{r['reduction']}%") for r in rows],
          ["Query", "Without index", "With index", "Index", "Docs examined", "Saved"])


def transaction_menu(db):
    header("ACID transaction demo - claim settlement")
    claim = db.claims.find_one({"status": {"$in": AWAITING_PAYMENT}})
    if not claim:
        print(f"{Y}No approved claims are waiting for payment.")
        return
    user = services.get_user(db, "USR003")
    policy = db.policies.find_one({"policy_number": claim["policy_number"]})
    print(f"  Claim {claim['claim_number']} approved for {inr(claim['approved_amount'])}; "
          f"policy {policy['policy_number']} available cover {inr(policy['available_cover'])}\n")
    for fail in (True, False):
        print(f"{BOLD}{'Run 1: payment gateway fails midway' if fail else 'Run 2: normal settlement'}{Style.RESET_ALL}")
        out = services.settle_claim(db, claim["claim_number"], user, simulate_failure=fail)
        for step, detail in out["steps"]:
            color = R if step == "ROLLBACK" else (G if step in ("COMMIT", "Verify") else "")
            print(f"  {color}{step:<20}{Style.RESET_ALL} {detail}")
        print()


def validation_menu(db):
    header("$jsonSchema validation demo")
    bad = {"claim_number": "CLAIM-42", "claim_type": "Cashless", "status": "Paid", "claimed_amount": -5000,
           "diagnosis": [], "bill": {"total": -5000}}
    print(f"  Inserting: {bad}\n")
    for e in validate("claims", bad):
        print(f"  {R}x{Style.RESET_ALL} {e}")


def launch_web(open_browser=True):
    global _web_thread
    import uvicorn
    from web.app import app
    if _web_thread and _web_thread.is_alive():
        print(f"{G}Web dashboard already running at http://127.0.0.1:{WEB_PORT}")
    else:
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=WEB_PORT, log_level="warning"))
        _web_thread = threading.Thread(target=server.run, daemon=True)
        _web_thread.start()
        time.sleep(1.5)
        print(f"{G}Web dashboard running at http://127.0.0.1:{WEB_PORT} "
              f"(shares this console's database - changes appear in both)")
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")


MENU = [
    ("1", "Database summary", show_summary),
    ("2", "Search claims", search_claims),
    ("3", "Work queue", work_queue),
    ("4", "Process a claim (review / decide / settle)", process_claim),
    ("5", "Intimate a new claim", new_claim),
    ("6", "Analytics reports", reports_menu),
    ("7", "Fraud watch-list", fraud_menu),
    ("8", "Index benchmark", benchmark_menu),
    ("9", "ACID transaction demo", transaction_menu),
    ("10", "Schema validation demo", validation_menu),
]


def interactive():
    db = get_db()
    ensure_seeded(db, verbose=True)
    while True:
        print(f"\n{B}{BOLD}{'=' * 78}")
        print(f"  {APP_NAME} v{VERSION}  -  {APP_TAGLINE}")
        print(f"{'=' * 78}{Style.RESET_ALL}")
        print(f"  {Y}{'MOCK MODE (mongomock, in-memory)' if is_mock() else 'Connected to MongoDB'}{Style.RESET_ALL}\n")
        for key, label, _ in MENU:
            print(f"  {Y}{key:>2}.{Style.RESET_ALL} {label}")
        print(f"  {Y} W.{Style.RESET_ALL} Launch web dashboard")
        print(f"  {Y} 0.{Style.RESET_ALL} Exit")
        choice = ask("\nSelect").lower()
        if choice == "0":
            print(f"{G}Goodbye!")
            return
        if choice == "w":
            launch_web()
            pause()
            continue
        action = next((fn for key, _, fn in MENU if key == choice), None)
        if not action:
            print(f"{R}Invalid option")
            continue
        try:
            action(db)
        except KeyboardInterrupt:
            print()
        pause()


def main():
    parser = argparse.ArgumentParser(description=f"{APP_NAME} - {APP_TAGLINE}")
    parser.add_argument("command", nargs="?", default="menu",
                        choices=["menu", "web", "seed", "init-db", "generate", "report", "benchmark", "test"])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    global WEB_PORT
    WEB_PORT = args.port

    if args.command == "menu":
        interactive()
    elif args.command == "web":
        import uvicorn
        if not args.no_browser:
            threading.Timer(2.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
        print(f"{G}Starting {APP_NAME} on http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
        uvicorn.run("web.app:app", host="127.0.0.1", port=args.port, log_level="warning")
    elif args.command in ("seed", "init-db"):
        if args.command == "init-db" and is_mock():
            print(f"{Y}CLAIMSURE_MONGO_URI is not set - running against mongomock (data is not persisted).")
        seed(get_db(), verbose=True)
    elif args.command == "generate":
        import runpy
        runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "generate_data.py"),
                       run_name="__main__")
    elif args.command == "report":
        db = get_db()
        ensure_seeded(db)
        show_summary(db)
        for key in ("insurer_performance", "plan_loss_ratio", "rejection_reasons"):
            print_report(db, key)
    elif args.command == "benchmark":
        db = get_db()
        ensure_seeded(db)
        benchmark_menu(db)
    elif args.command == "test":
        import subprocess
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", "tests"]))


if __name__ == "__main__":
    main()
