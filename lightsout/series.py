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
    default_on: bool = True


SERIES = [
    Series("f1", "Formula 1", "F1", "laps", "#e10600", "f1"),
    Series("f2", "FIA Formula 2", "F2", "laps", "#0090d0", "fia_feeder"),
    Series("f3", "FIA Formula 3", "F3", "laps", "#e4007c", "fia_feeder"),
    Series("f1a", "F1 Academy", "F1A", "laps", "#7b3fe4", "f1academy"),
    Series("fe", "Formula E", "FE", "laps", "#14b8d4", "formulae"),
    Series("indycar", "IndyCar", "INDY", "laps", "#1f4fa3", "indycar"),
    Series("wec", "FIA WEC", "WEC", "laps", "#00a19a", "wec"),
    Series("cup", "NASCAR Cup Series", "CUP", "laps", "#f4c000", "nascar"),
    Series("noaps", "NASCAR O'Reilly Auto Parts Series", "NOAPS", "laps", "#2b9348", "nascar"),
    Series("truck", "NASCAR Craftsman Truck Series", "TRUCK", "laps", "#c1121f", "nascar"),
    Series("gtwce", "GT World Challenge Europe", "GTWCE", "laps", "#00539f", "gtwce"),
    Series("nls", "Nürburgring Langstrecken-Serie", "NLS", "laps", "#2e8b3d", "nls"),
    Series("gtwc", "FIA GT World Cup", "GTWC", "laps", "#c8a200", "manual"),
    Series("frwc", "FIA FR World Cup", "FRWC", "laps", "#d9822b", "manual"),
    Series("f4wc", "FIA F4 World Cup", "F4WC", "laps", "#8a8f98", "manual"),
    Series("wrc", "WRC", "WRC", "stages", "#ffcc00", "wrc"),
    Series("ara", "ARA National", "ARA", "stages", "#b22234", "ara"),
    Series("erx", "Euro RX", "RX", "heats", "#5ab031", "erx"),
    Series("w2rc", "World Rally-Raid", "W2RC", "stages", "#c2733f", "manual"),
]

BY_ID = {s.id: s for s in SERIES}
