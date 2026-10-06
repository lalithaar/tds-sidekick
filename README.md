# TDS Sidekick

Your study-group sidekick — opt in, split the work, solve, review, and verify together. We're all cooked, let's get cooked together efficiently.

A Django app for study groups to run "Give an Assignment" (GA) game rounds: students opt into an open GA, get questions assigned to them, submit step-by-step solutions, review each other's work, and unlock verified answers once their own submissions pass peer review.

## Tech

- Django (Python), SQLite, server-rendered templates
- Python-Markdown + Pygments for solution authoring with live preview and code highlighting
- Peer review as thresholds (working marks) before verified answers unlock

## Run locally

```bash
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt  # or create venv yourself
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py seed_demo        # demo data (optional)
.\.venv\Scripts\python manage.py runserver
```