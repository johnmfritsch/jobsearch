#!/opt/bin/python3
"""DEV operator utility: issue a one-time imported-profile claim code.

Run on the NAS: python3 issue_imported_user_claim_code.py <directory-name>
Give the resulting code directly to the intended user; it expires in 72 hours.
"""
import sys
from auth_store import issue_import_claim_code

if len(sys.argv) != 2:
    raise SystemExit("Usage: issue_imported_user_claim_code.py <directory-name>")
print(issue_import_claim_code(sys.argv[1]))
