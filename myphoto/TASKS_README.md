# Background Tasks with Django-Q2

The photo import and H3 indexing logic has been refactored into service classes that can be called from:
- Management commands (synchronous)
- Django-Q2 background tasks (asynchronous)
- Celery workers (if you use Celery)
- Any Python code

## Architecture

```
myphoto/
├── services/                    # Core business logic (reusable)
│   ├── photo_import_service.py  # Import photos from CSV
│   └── h3_indexing_service.py   # Calculate H3 spatial indexes
├── management/commands/         # CLI interface
│   ├── import_photos.py         # Wraps PhotoImportService
│   └── calculate_h3_indexes.py  # Wraps H3IndexingService
└── tasks.py                     # Background task wrappers for django-q2
```

## Using Services Directly (Synchronous)

```python
from myphoto.services.photo_import_service import PhotoImportService
from myphoto.services.h3_indexing_service import H3IndexingService

# Import photos
service = PhotoImportService()
stats = service.import_from_csv('/path/to/photos.csv')
print(f"Imported {stats['new_photos']} photos")

# Calculate H3 indexes
h3_service = H3IndexingService()
h3_stats = h3_service.calculate_h3_indexes()
print(f"Indexed {h3_stats['processed']} photos")
```

## Using Background Tasks (Django-Q2)

### Setup Django-Q2

1. Install django-q2:
```bash
uv add django-q2
```

2. Add to `INSTALLED_APPS` in settings.py:
```python
INSTALLED_APPS = [
    # ...
    'django_q',
    'myphoto',
]
```

3. Configure Django-Q2 in settings.py:
```python
Q_CLUSTER = {
    'name': 'photo_tasks',
    'workers': 2,
    'timeout': 600,  # 10 minutes
    'retry': 720,
    'queue_limit': 50,
    'bulk': 10,
    'orm': 'default',  # Use database as broker
}
```

4. Run migrations:
```bash
python manage.py migrate django_q
```

5. Start the worker:
```bash
python manage.py qcluster
```

### Queue Tasks

#### From Django shell or views:

```python
from django_q.tasks import async_task, result

# Queue photo import task
task_id = async_task(
    'myphoto.tasks.import_photos_task',
    '/path/to/photos.csv',
    batch_size=1000,
    clear_existing=False
)

# Queue H3 indexing task
task_id = async_task(
    'myphoto.tasks.calculate_h3_indexes_task',
    batch_size=1000,
    recalculate=False
)

# Queue single photo H3 calculation (for real-time updates)
task_id = async_task(
    'myphoto.tasks.calculate_h3_for_single_photo_task',
    photo_id=123
)

# Chain tasks: import then index
task_id = async_task(
    'myphoto.tasks.import_and_index_task',
    '/path/to/photos.csv'
)

# Check task status
task_result = result(task_id)
if task_result is not None:
    print(f"Task completed: {task_result}")
```

#### Schedule recurring tasks:

```python
from django_q.models import Schedule

# Auto-index new photos every hour
Schedule.objects.create(
    func='myphoto.tasks.calculate_h3_indexes_task',
    schedule_type=Schedule.HOURLY,
    name='Auto H3 Indexing'
)

# Import from a network location daily
Schedule.objects.create(
    func='myphoto.tasks.import_photos_task',
    args='/mnt/photos/export.csv',
    schedule_type=Schedule.DAILY,
    name='Daily Photo Import'
)
```

## Signal-Based Auto-Indexing

Create `myphoto/signals.py`:

```python
from django.db.models.signals import post_save
from django.dispatch import receiver
from django_q.tasks import async_task

from myphoto.models import Photo

@receiver(post_save, sender=Photo)
def auto_calculate_h3(sender, instance, created, **kwargs):
    """Auto-calculate H3 indexes when a photo is created/updated."""
    if instance.has_gps and not instance.has_h3_indexes:
        # Queue background task
        async_task(
            'myphoto.tasks.calculate_h3_for_single_photo_task',
            photo_id=instance.id
        )
```

Register in `myphoto/apps.py`:
```python
from django.apps import AppConfig

class MyphotoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'myphoto'

    def ready(self):
        import myphoto.signals  # noqa
```

## Available Task Functions

### `import_photos_task(csv_file_path, batch_size=1000, clear_existing=False, update_existing=False)`
Import photos from CSV in the background.

**Returns:** `{'new_photos': int, 'updated_photos': int, 'duplicates': int, 'skipped': int, 'errors': list}`

### `calculate_h3_indexes_task(batch_size=1000, recalculate=False)`
Calculate H3 spatial indexes for all photos with GPS.

**Returns:** `{'total': int, 'processed': int, 'updated': int, 'errors': int, 'statistics': dict}`

### `calculate_h3_for_single_photo_task(photo_id)`
Calculate H3 indexes for a single photo (useful for real-time updates).

**Returns:** `bool` (True if successful)

### `import_and_index_task(csv_file_path)`
Chain task: import photos then calculate H3 indexes.

**Returns:** `{'import': {...}, 'h3_indexing': {...}}`

## Monitoring Tasks

```python
from django_q.models import Task, Success, Failure

# Check recent successes
recent_successes = Success.objects.all()[:10]

# Check failures
failures = Failure.objects.all()

# Get specific task
task = Task.objects.get(id=task_id)
```

## Web UI

Django-Q2 comes with a web UI. Add to `urls.py`:

```python
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('django-q/', include('django_q.urls')),  # Django-Q2 web UI
]
```

Access at: `http://localhost:8000/django-q/`

## Production Notes

- Use Redis or RabbitMQ as broker instead of database for better performance
- Adjust `workers` count based on your server
- Set appropriate `timeout` for long-running tasks
- Monitor failed tasks and set up alerts
- Use `retry` for transient failures

Example with Redis:
```python
Q_CLUSTER = {
    'name': 'photo_tasks',
    'workers': 4,
    'timeout': 600,
    'retry': 720,
    'redis': {
        'host': '127.0.0.1',
        'port': 6379,
        'db': 0,
    }
}
```
