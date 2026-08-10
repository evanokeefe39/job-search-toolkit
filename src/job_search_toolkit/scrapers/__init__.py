"""Board scrapers — raw job listings into canonical JSON/CSV."""

from job_search_toolkit.scrapers.freework import app as freework_app
from job_search_toolkit.scrapers.hiringcafe import app as hiringcafe_app

__all__ = ["freework_app", "hiringcafe_app"]
