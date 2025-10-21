#!/bin/sh

cd "$(dirname "$0")"

python manage.py collectstatic --no-input > /dev/null
python manage.py migrate > /dev/null

gunicorn --workers 1 --bind 0.0.0.0:8000 track_me.wsgi:application
