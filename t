python manage.py migrate
python manage.py tailwind build --force
python manage.py collectstatic --no-input
DEBUG=1 gunicorn --bind 127.0.0.1:8000 trackme.wsgi:application
