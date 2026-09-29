#!/opt/bin/bash
set -e
/opt/bin/bash /volume1/Web/JobSearch_dev/scripts/stop_application_worker_dev.sh
/volume1/.@plugins/AppCentral/python3/bin/python3 /volume1/Web/JobSearch_dev/scripts/start_application_worker_dev.py
