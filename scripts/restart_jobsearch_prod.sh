#!/opt/bin/bash
# Thin env wrapper (prod). All logic lives in _jobsearch_server_lib.sh.
#   app_restart <env> <appdir> <pid_file> <port> <prefix> <log>
DIR="$(dirname "$(readlink -f "$0")")"
. "$DIR/_jobsearch_server_lib.sh"
app_restart prod \
    /volume1/Web/JobSearch \
    /volume1/Web/JobSearch/jobsearch_prod.pid \
    7787 \
    /JobSearch \
    /volume1/Web/JobSearch/logs/jobsearch_prod.log
