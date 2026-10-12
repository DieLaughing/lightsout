"""The series Lights Out knows about. Order here is display order."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Series:
    id: str
    name: str
    short: str
    format: str      # laps | stages | heats
    color: str       # chip colour in the web app
    adapter: str     # module in lightsout.adapters
    category: str = "single"  # single | sportscar | stock | rally: the type filter on Upcoming
    site: str = ""            # official site
    timing: str = ""          # official live timing page, where there is a public one
    default_on: bool = True


SERIES = [
    Series("f1", "Formula 1", "F1", "laps", "#e10600", "f1", category="single", site="https://www.formula1.com", timing="https://www.formula1.com/en/timing/f1-live"),
    Series("f2", "FIA Formula 2", "F2", "laps", "#0090d0", "fia_feeder", category="single", site="https://www.fiaformula2.com", timing="https://www.fiaformula2.com/livetiming/index.html"),
    Series("f3", "FIA Formula 3", "F3", "laps", "#e4007c", "fia_feeder", category="single", site="https://www.fiaformula3.com", timing="https://www.fiaformula3.com/livetiming/index.html"),
    Series("f1a", "F1 Academy", "F1A", "laps", "#7b3fe4", "f1academy", category="single", site="https://www.f1academy.com", timing="https://www.f1academy.com/livetiming/index.html"),
    Series("fe", "Formula E", "FE", "laps", "#14b8d4", "formulae", category="single", site="https://www.fiaformulae.com/en"),
    Series("indycar", "IndyCar", "INDY", "laps", "#1f4fa3", "indycar", category="single", site="https://www.indycar.com", timing="https://www.indycar.com/leaderboard"),
    Series("wec", "FIA WEC", "WEC", "laps", "#00a19a", "wec", category="sportscar", site="https://www.fiawec.com", timing="https://fiawec.alkamelsystems.com/"),
    Series("cup", "NASCAR Cup Series", "CUP", "laps", "#f4c000", "nascar", category="stock", site="https://www.nascar.com", timing="https://www.nascar.com/leaderboard"),
    Series("noaps", "NASCAR O'Reilly Auto Parts Series", "NOAPS", "laps", "#2b9348", "nascar", category="stock", site="https://www.nascar.com", timing="https://www.nascar.com/leaderboard"),
    Series("truck", "NASCAR Craftsman Truck Series", "TRUCK", "laps", "#c1121f", "nascar", category="stock", site="https://www.nascar.com", timing="https://www.nascar.com/leaderboard"),
    Series("gtwce", "GT World Challenge Europe", "GTWCE", "laps", "#00539f", "gtwce", category="sportscar", site="https://www.gt-world-challenge-europe.com", timing="https://www.gt-world-challenge-europe.com/watch-live#live-timing"),
    Series("nls", "Nürburgring Langstrecken-Serie", "NLS", "laps", "#2e8b3d", "nls", category="sportscar", site="https://www.nuerburgring-langstrecken-serie.de/language/en/"),
    Series("gtwc", "FIA GT World Cup", "GTWC", "laps", "#c8a200", "manual", category="sportscar", site="https://www.macau.grandprix.gov.mo/en/"),
    Series("frwc", "FIA FR World Cup", "FRWC", "laps", "#d9822b", "manual", category="single", site="https://www.macau.grandprix.gov.mo/en/"),
    Series("f4wc", "FIA F4 World Cup", "F4WC", "laps", "#8a8f98", "manual", category="single", site="https://www.macau.grandprix.gov.mo/en/"),
    Series("wrc", "WRC", "WRC", "stages", "#ffcc00", "wrc", category="rally", site="https://www.wrc.com", timing="https://www.wrc.com/live-timing"),
    Series("ara", "ARA National", "ARA", "stages", "#b22234", "ara", category="rally", site="https://www.americanrallyassociation.org"),
    Series("erx", "Euro RX", "RX", "heats", "#5ab031", "erx", category="rally", site="https://www.fia-rallycross.com"),
    Series("w2rc", "World Rally-Raid", "W2RC", "stages", "#c2733f", "manual", category="rally", site="https://www.worldrallyraidchampionship.com/en"),
]

BY_ID = {s.id: s for s in SERIES}
