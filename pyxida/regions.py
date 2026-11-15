"""Search regions: per-country languages, currency, work-authorization options and portal presets.

Portal presets are starting points for discovery. Users can switch each one off,
add hints, mark favourites, or add their own; listings are always read on their
own pages. Add a country by adding an entry here.
"""

DRAFT_LANGUAGES = ("English", "German", "French", "Italian", "Spanish", "Portuguese", "Dutch",
                   "Danish", "Swedish", "Norwegian", "Finnish", "Polish")

EU_PERMITS = ("Citizen of {country}", "EU/EEA citizen (free movement)", "Permanent resident",
              "Holder of a work permit or visa", "Non-EU citizen, needs sponsorship")
GENERAL_PERMITS = ("Citizen of {country}", "Permanent resident", "Holder of a work permit or visa",
                   "Needs visa sponsorship")


def region(name, languages, currency, portals, permits=EU_PERMITS, settled=""):
    return {"name": name, "languages": languages, "currency": currency,
            "portals": [{"name": n, "domain": d} for n, d in portals],
            "permits": [p.replace("{country}", name) for p in permits], "settled": settled}


LINKEDIN = ("LinkedIn Jobs", "linkedin.com")

REGIONS = {
    "CH": region("Switzerland", ["German", "French", "Italian", "English"], "CHF", [
        ("jobs.ch", "jobs.ch"), ("jobup.ch (Romandie)", "jobup.ch"), ("Job-Room (SECO / RAV)", "job-room.ch"),
        LINKEDIN, ("Indeed Switzerland", "ch.indeed.com"), ("eFinancialCareers", "efinancialcareers.ch"),
        ("JobScout24", "jobscout24.ch")],
        permits=("Swiss citizen", "C permit (settled)", "B permit (residence)", "G permit (cross-border commuter)",
                 "EU/EFTA citizen, permit not yet issued", "Non-EU/EFTA citizen, needs sponsorship"),
        settled="a Swiss citizen or C permit holder"),
    "DE": region("Germany", ["German", "English"], "EUR", [
        ("StepStone", "stepstone.de"), ("Jobbörse (Bundesagentur für Arbeit)", "arbeitsagentur.de"),
        ("Indeed Germany", "de.indeed.com"), LINKEDIN, ("XING", "xing.com"), ("eFinancialCareers", "efinancialcareers.de")]),
    "AT": region("Austria", ["German", "English"], "EUR", [
        ("karriere.at", "karriere.at"), ("AMS alle jobs", "jobs.ams.at"), ("StepStone Austria", "stepstone.at"),
        ("Indeed Austria", "at.indeed.com"), LINKEDIN]),
    "FR": region("France", ["French", "English"], "EUR", [
        ("France Travail", "francetravail.fr"), ("APEC (cadres)", "apec.fr"), ("Welcome to the Jungle", "welcometothejungle.com"),
        ("HelloWork", "hellowork.com"), ("Indeed France", "fr.indeed.com"), LINKEDIN]),
    "IT": region("Italy", ["Italian", "English"], "EUR", [
        ("InfoJobs", "infojobs.it"), ("Indeed Italy", "it.indeed.com"), LINKEDIN]),
    "LU": region("Luxembourg", ["French", "German", "English"], "EUR", [
        ("jobs.lu", "jobs.lu"), ("Moovijob", "moovijob.com"), ("Indeed Luxembourg", "lu.indeed.com"),
        ("eFinancialCareers", "efinancialcareers.lu"), LINKEDIN]),
    "BE": region("Belgium", ["French", "Dutch", "English"], "EUR", [
        ("VDAB", "vdab.be"), ("StepStone Belgium", "stepstone.be"), ("Indeed Belgium", "be.indeed.com"), LINKEDIN]),
    "NL": region("Netherlands", ["Dutch", "English"], "EUR", [
        ("Nationale Vacaturebank", "nationalevacaturebank.nl"), ("werk.nl (UWV)", "werk.nl"),
        ("Indeed Netherlands", "nl.indeed.com"), LINKEDIN]),
    "GB": region("United Kingdom", ["English"], "GBP", [
        ("Reed", "reed.co.uk"), ("Totaljobs", "totaljobs.com"), ("Find a job (GOV.UK)", "findajob.dwp.gov.uk"),
        ("CV-Library", "cv-library.co.uk"), ("Indeed UK", "uk.indeed.com"), ("eFinancialCareers", "efinancialcareers.co.uk"), LINKEDIN],
        permits=GENERAL_PERMITS),
    "IE": region("Ireland", ["English"], "EUR", [
        ("IrishJobs", "irishjobs.ie"), ("Jobs.ie", "jobs.ie"), ("Indeed Ireland", "ie.indeed.com"), LINKEDIN]),
    "ES": region("Spain", ["Spanish", "English"], "EUR", [
        ("InfoJobs", "infojobs.net"), ("Indeed Spain", "es.indeed.com"), LINKEDIN]),
    "DK": region("Denmark", ["Danish", "English"], "DKK", [
        ("Jobindex", "jobindex.dk"), ("Jobnet", "jobnet.dk"), LINKEDIN]),
    "SE": region("Sweden", ["Swedish", "English"], "SEK", [
        ("Platsbanken (Arbetsförmedlingen)", "arbetsformedlingen.se"), ("Indeed Sweden", "se.indeed.com"), LINKEDIN]),
    "NO": region("Norway", ["Norwegian", "English"], "NOK", [
        ("FINN jobb", "finn.no"), ("arbeidsplassen (NAV)", "arbeidsplassen.nav.no"), LINKEDIN]),
    "US": region("United States", ["English"], "USD", [
        ("Indeed", "indeed.com"), LINKEDIN, ("Glassdoor", "glassdoor.com"), ("USAJOBS", "usajobs.gov")],
        permits=GENERAL_PERMITS),
    "CA": region("Canada", ["English", "French"], "CAD", [
        ("Job Bank", "jobbank.gc.ca"), ("Indeed Canada", "ca.indeed.com"), LINKEDIN], permits=GENERAL_PERMITS),
    "AU": region("Australia", ["English"], "AUD", [
        ("SEEK", "seek.com.au"), ("Indeed Australia", "au.indeed.com"), LINKEDIN], permits=GENERAL_PERMITS),
    "SG": region("Singapore", ["English"], "SGD", [
        ("MyCareersFuture", "mycareersfuture.gov.sg"), ("JobStreet", "jobstreet.com.sg"), LINKEDIN], permits=GENERAL_PERMITS),
}
DEFAULT_REGION = "CH"


def get_region(code):
    return REGIONS.get(code) or REGIONS[DEFAULT_REGION]


def preset_portals(code):
    return [{**p, "enabled": True, "builtin": True, "country": code, "hint": "", "preferred": False}
            for p in get_region(code)["portals"]]


def summary():
    """What the browser needs to render region-aware forms."""
    return {code: {k: r[k] for k in ("name", "languages", "currency", "permits")} for code, r in REGIONS.items()}
