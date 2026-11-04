#!/usr/bin/env bash
# Render pre-deploy step: runs before each new version goes live.
set -o errexit

python manage.py migrate --noinput
python manage.py ensure_superuser

# Demo catalogue and accounts (password bonovela1). Only for test/demo
# environments: set SEED_DEMO=true on the service, deploy once, then turn it
# off again.
if [ "${SEED_DEMO:-false}" = "true" ]; then
  python manage.py seed_bonovela
fi
