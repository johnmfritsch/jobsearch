#!/opt/bin/bash
# Thin env wrapper (prod). All logic lives in _jobsearch_server_lib.sh.
DIR="$(dirname "$(readlink -f "$0")")"
. "$DIR/_jobsearch_server_lib.sh"
app_stop /volume1/Web/JobSearch/jobsearch_prod.pid 7787
