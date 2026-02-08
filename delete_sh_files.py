#!/usr/bin/env python3
import os

os.chdir("/media/data/Dev/Python/mirror downloader")

files_to_delete = [
    'celery_systemd.sh',
    'setup.sh',
    'setup_cron.sh',
    'setup_nginx.sh',
    'setup_ngrok.sh',
    'start_celery.sh',
    'start_celery_beat.sh',
    'start_with_ngrok.sh'
]

for f in files_to_delete:
    try:
        os.remove(f)
        print(f'Deleted: {f}')
    except FileNotFoundError:
        print(f'Not found: {f}')
    except Exception as e:
        print(f'Error deleting {f}: {e}')

print('\n--- Remaining .sh files: ---')
for f in os.listdir('.'):
    if f.endswith('.sh'):
        print(f'  {f}')
