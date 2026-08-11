"""Board scrapers — raw job listings into canonical JSON/CSV."""

from job_search_toolkit.scrapers.faruse import app as faruse_app
from job_search_toolkit.scrapers.englishjobs import app as englishjobs_app
from job_search_toolkit.scrapers.freework import app as freework_app
from job_search_toolkit.scrapers.hellowork import app as hellowork_app
from job_search_toolkit.scrapers.hiringcafe import app as hiringcafe_app
__all__ = ["englishjobs_app", "faruse_app", "freework_app", "hellowork_app", "hiringcafe_app"]
