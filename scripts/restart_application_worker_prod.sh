#!/opt/bin/bash
set -e
/opt/bin/bash /volume1/Web/JobSearch/scripts/stop_application_worker_prod.sh
/volume1/.@plugins/AppCentral/python3/bin/python3 /volume1/Web/JobSearch/scripts/start_application_worker_prod.py
