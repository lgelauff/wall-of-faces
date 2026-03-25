# Periodic review items

## Privacy policy
Review `templates/privacy.html` to ensure it still accurately lists all data collected and processed.

**Trigger:** monthly, OR whenever data ingestion changes (new fields fetched from OAuth, new API sources added to the gather pipeline, new data stored on `UserProfile`).

**How:** Cross-check the bullet list in the privacy statement against `src/db.py` (UserProfile columns) and `src/gather.py` (what is fetched and from where).

## Gather progress percentages
Re-evaluate the progress percentages assigned to each gather step in `src/gather.py`.

**Trigger:** monthly, OR after 50 new profiles have been gathered, OR after significant changes to the gather pipeline.

**How:** Check the admin page timing stats (count/avg/min/max per step) and adjust percentages to reflect actual relative durations.
