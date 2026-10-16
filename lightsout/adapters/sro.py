"""SRO's other championships (IGTC, GT World Challenge America / Asia / Australia, British GT): same site template as
GT World Challenge Europe, so the same calendar, timetable, entry-list and results code (gtwce.SITES has their sites).
Last race and qualifying only: their standings pages differ per championship. IGTC and Australia publish results as
PDFs, so nothing there."""
from .gtwce import fetch, standings  # noqa: F401

NO_STANDINGS = {"igtc", "gtwcau"}
