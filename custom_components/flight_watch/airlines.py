"""Airline names people type, mapped to IATA codes."""

import re

# IATA code, display name, extra names/ICAO codes people might type (upper case)
AIRLINES = [
    ("AS", "Alaska Airlines", ["ALASKA", "ASA"]),
    ("QX", "Horizon Air", ["HORIZON", "QXE"]),
    ("WN", "Southwest", ["SOUTHWEST", "SWA"]),
    ("DL", "Delta", ["DELTA", "DAL"]),
    ("UA", "United", ["UNITED", "UAL"]),
    ("AA", "American", ["AMERICAN", "AAL"]),
    ("B6", "JetBlue", ["JETBLUE", "JBU"]),
    ("F9", "Frontier", ["FRONTIER", "FFT"]),
    ("NK", "Spirit", ["SPIRIT", "NKS"]),
    ("G4", "Allegiant", ["ALLEGIANT", "AAY"]),
    ("HA", "Hawaiian", ["HAWAIIAN", "HAL"]),
    ("SY", "Sun Country", ["SUN COUNTRY", "SCX"]),
    ("MX", "Breeze", ["BREEZE", "MXY"]),
    ("XP", "Avelo", ["AVELO", "VXP"]),
    ("OO", "SkyWest", ["SKYWEST", "SKW"]),
    ("AC", "Air Canada", ["AIR CANADA", "ACA"]),
    ("WS", "WestJet", ["WESTJET", "WJA"]),
    ("PD", "Porter", ["PORTER", "POE"]),
    ("AM", "Aeroméxico", ["AEROMEXICO", "AMX"]),
    ("Y4", "Volaris", ["VOLARIS", "VOI"]),
    ("CM", "Copa", ["COPA", "CMP"]),
    ("AV", "Avianca", ["AVIANCA", "AVA"]),
    ("LA", "LATAM", ["LATAM", "LAN"]),
    ("BA", "British Airways", ["BRITISH", "BAW"]),
    ("VS", "Virgin Atlantic", ["VIRGIN ATLANTIC", "VIR"]),
    ("EI", "Aer Lingus", ["AER LINGUS", "EIN"]),
    ("FI", "Icelandair", ["ICELANDAIR", "ICE"]),
    ("LH", "Lufthansa", ["LUFTHANSA", "DLH"]),
    ("DE", "Condor", ["CONDOR", "CFG"]),
    ("LX", "Swiss", ["SWISS", "SWR"]),
    ("AF", "Air France", ["AIR FRANCE", "AFR"]),
    ("KL", "KLM", ["KLM"]),
    ("IB", "Iberia", ["IBERIA", "IBE"]),
    ("TP", "TAP Air Portugal", ["TAP"]),
    ("SK", "SAS", ["SAS"]),
    ("TK", "Turkish Airlines", ["TURKISH", "THY"]),
    ("EK", "Emirates", ["EMIRATES", "UAE"]),
    ("QR", "Qatar Airways", ["QATAR", "QTR"]),
    ("EY", "Etihad", ["ETIHAD", "ETD"]),
    ("JL", "Japan Airlines", ["JAPAN", "JAL"]),
    ("NH", "ANA", ["ANA", "ALL NIPPON"]),
    ("KE", "Korean Air", ["KOREAN", "KAL"]),
    ("CX", "Cathay Pacific", ["CATHAY", "CPA"]),
    ("SQ", "Singapore Airlines", ["SINGAPORE", "SIA"]),
    ("BR", "EVA Air", ["EVA"]),
    ("CI", "China Airlines", ["CHINA AIRLINES", "CAL"]),
    ("JX", "Starlux", ["STARLUX", "SJX"]),
    ("QF", "Qantas", ["QANTAS", "QFA"]),
    ("NZ", "Air New Zealand", ["AIR NEW ZEALAND", "ANZ"]),
    ("FJ", "Fiji Airways", ["FIJI", "FJI"]),
]

_PART = re.compile(r"\s*([A-Z][A-Z .]*?[A-Z]|[A-Z][0-9]|[0-9][A-Z])?\s*(\d{1,4}[A-Z]?)\s*")


def airline_code(prefix: str) -> str:
    """'Alaska Airlines' / 'ASA' / 'AS' -> 'AS'; any other 2-char prefix is the code itself; '' if unknown."""
    p = re.sub(r"\b(AIRLINES|AIRWAYS|AIR LINES)\b", "", prefix.upper()).strip()
    for code, name, aliases in AIRLINES:
        if p in aliases or p == name.upper() or p == code:
            return code
    return p if len(p) == 2 else ""


def parse_flights(text: str) -> list[str]:
    """'Southwest 1570/1875', 'AS300, AA590', 'AA 2104 + 590' -> ['WN1570', 'WN1875'].

    A part with no airline reuses the previous one. Raises ValueError naming the bad part.
    """
    out: list[str] = []
    code = ""
    for part in re.split(r"[/,+]", text.upper()):
        if not part.strip():
            continue
        m = _PART.fullmatch(part)
        if m and m.group(1):
            code = airline_code(m.group(1))
        if not m or not code:
            raise ValueError(part.strip())
        out.append(code + m.group(2))
    if not out:
        raise ValueError(text)
    return out
