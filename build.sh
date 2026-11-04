#!/usr/bin/env bash
# Render build step: install dependencies and collect static files.
# Migrations run in render.yaml's preDeployCommand.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --noinput
