"""
Słownik lotnisk i miast używany do:
  * filtrowania geograficznego (czy wylot jest z Polski / okolic),
  * rozpoznawania nazw miast w tekście postów (Telegram, RSS, Facebook),
  * klasyfikacji kierunku: Europa vs. daleki dystans (long-haul).
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Set

# Lotniska „domowe”: kod IATA -> lista nazw/odmian występujących w tekstach.
# Uwzględniamy polskie odmiany (Warszawy, Krakowa, Gdańska...).
POLISH_AIRPORTS: Dict[str, list] = {
    "WAW": ["warszawa", "warszawy", "warszawie", "warsaw", "chopin", "okęcie", "okecie"],
    "WMI": ["modlin", "modlina", "modlinie"],
    "KRK": ["kraków", "krakow", "krakowa", "krakowie", "cracow", "balice"],
    "GDN": ["gdańsk", "gdansk", "gdańska", "gdanska", "gdańsku", "gdansku", "rębiechowo"],
    "KTW": ["katowice", "katowic", "katowicach", "pyrzowice"],
    "WRO": ["wrocław", "wroclaw", "wrocławia", "wroclawia", "wrocławiu", "wroclawiu"],
    "POZ": ["poznań", "poznan", "poznania", "poznaniu", "ławica", "lawica"],
    "RZE": ["rzeszów", "rzeszow", "rzeszowa", "rzeszowie", "jasionka"],
    "LUZ": ["lublin", "lublina", "lublinie"],
    "SZZ": ["szczecin", "szczecina", "szczecinie", "goleniów", "goleniow"],
    "BZG": ["bydgoszcz", "bydgoszczy"],
    "LCJ": ["łódź", "lodz", "łodzi", "lodzi"],
    "RDO": ["radom", "radomia"],
    "OSR": ["olsztyn", "olsztyna", "szymany"],
    "BER": ["berlin", "berlina", "berlinie"],  # blisko granicy – opcjonalnie
}

# Kierunki uznawane za „Europę” (w tym basen Morza Śródziemnego / Bliski Wschód
# w zasięgu tanich linii). Reszta świata = daleki dystans.
EUROPE_COUNTRIES: Set[str] = {
    "PL", "DE", "FR", "ES", "PT", "IT", "GB", "IE", "NL", "BE", "LU", "CH", "AT",
    "CZ", "SK", "HU", "RO", "BG", "GR", "CY", "MT", "HR", "SI", "RS", "BA", "ME",
    "MK", "AL", "XK", "DK", "SE", "NO", "FI", "IS", "EE", "LV", "LT", "UA", "MD",
    "TR", "GE", "AM", "MA", "TN", "IL", "JO", "EG", "FO",
}

# Popularne kierunki: nazwy w tekście -> (kod IATA/miasta, kraj).
# Wystarczy lista najczęściej pojawiających się w ofertach; brak dopasowania
# nie blokuje powiadomienia (używamy wtedy samej analizy ceny).
DESTINATIONS: Dict[str, tuple] = {
    # Europa
    "barcelona": ("BCN", "ES"), "barcelony": ("BCN", "ES"), "madryt": ("MAD", "ES"),
    "madrytu": ("MAD", "ES"), "malaga": ("AGP", "ES"), "malagi": ("AGP", "ES"),
    "alicante": ("ALC", "ES"), "majorka": ("PMI", "ES"), "majorki": ("PMI", "ES"),
    "palma": ("PMI", "ES"), "teneryfa": ("TFS", "ES"), "teneryfy": ("TFS", "ES"),
    "lanzarote": ("ACE", "ES"), "fuerteventura": ("FUE", "ES"), "gran canaria": ("LPA", "ES"),
    "lizbona": ("LIS", "PT"), "lizbony": ("LIS", "PT"), "porto": ("OPO", "PT"),
    "madera": ("FNC", "PT"), "madery": ("FNC", "PT"), "faro": ("FAO", "PT"),
    "rzym": ("ROM", "IT"), "rzymu": ("ROM", "IT"), "mediolan": ("MIL", "IT"),
    "mediolanu": ("MIL", "IT"), "neapol": ("NAP", "IT"), "neapolu": ("NAP", "IT"),
    "wenecja": ("VCE", "IT"), "wenecji": ("VCE", "IT"), "bolonia": ("BLQ", "IT"),
    "sycylia": ("CTA", "IT"), "sycylii": ("CTA", "IT"), "katania": ("CTA", "IT"),
    "sardynia": ("CAG", "IT"), "sardynii": ("CAG", "IT"), "bari": ("BRI", "IT"),
    "paryż": ("PAR", "FR"), "paryża": ("PAR", "FR"), "paris": ("PAR", "FR"),
    "nicea": ("NCE", "FR"), "nicei": ("NCE", "FR"), "marsylia": ("MRS", "FR"),
    "londyn": ("LON", "GB"), "londynu": ("LON", "GB"), "london": ("LON", "GB"),
    "edynburg": ("EDI", "GB"), "manchester": ("MAN", "GB"), "dublin": ("DUB", "IE"),
    "dublina": ("DUB", "IE"), "amsterdam": ("AMS", "NL"), "amsterdamu": ("AMS", "NL"),
    "bruksela": ("BRU", "BE"), "brukseli": ("BRU", "BE"), "wiedeń": ("VIE", "AT"),
    "wiednia": ("VIE", "AT"), "praga": ("PRG", "CZ"), "pragi": ("PRG", "CZ"),
    "budapeszt": ("BUD", "HU"), "budapesztu": ("BUD", "HU"), "ateny": ("ATH", "GR"),
    "aten": ("ATH", "GR"), "kreta": ("HER", "GR"), "krety": ("HER", "GR"),
    "rodos": ("RHO", "GR"), "korfu": ("CFU", "GR"), "zakynthos": ("ZTH", "GR"),
    "saloniki": ("SKG", "GR"), "cypr": ("PFO", "CY"), "cypru": ("PFO", "CY"),
    "larnaka": ("LCA", "CY"), "malta": ("MLA", "MT"), "malty": ("MLA", "MT"),
    "oslo": ("OSL", "NO"), "bergen": ("BGO", "NO"), "sztokholm": ("STO", "SE"),
    "sztokholmu": ("STO", "SE"), "kopenhaga": ("CPH", "DK"), "kopenhagi": ("CPH", "DK"),
    "helsinki": ("HEL", "FI"), "islandia": ("KEF", "IS"), "islandii": ("KEF", "IS"),
    "reykjavik": ("KEF", "IS"), "stambuł": ("IST", "TR"), "stambułu": ("IST", "TR"),
    "antalya": ("AYT", "TR"), "antalyi": ("AYT", "TR"), "gruzja": ("TBS", "GE"),
    "gruzji": ("TBS", "GE"), "tbilisi": ("TBS", "GE"), "kutaisi": ("KUT", "GE"),
    "maroko": ("RAK", "MA"), "maroka": ("RAK", "MA"), "marrakesz": ("RAK", "MA"),
    "agadir": ("AGA", "MA"), "tunezja": ("TUN", "TN"), "tunezji": ("TUN", "TN"),
    "izrael": ("TLV", "IL"), "izraela": ("TLV", "IL"), "tel awiw": ("TLV", "IL"),
    "jordania": ("AMM", "JO"), "jordanii": ("AMM", "JO"), "amman": ("AMM", "JO"),
    "egipt": ("HRG", "EG"), "egiptu": ("HRG", "EG"), "hurghada": ("HRG", "EG"),
    "kair": ("CAI", "EG"), "kairu": ("CAI", "EG"), "chorwacja": ("SPU", "HR"),
    "chorwacji": ("SPU", "HR"), "split": ("SPU", "HR"), "dubrownik": ("DBV", "HR"),
    "albania": ("TIA", "AL"), "albanii": ("TIA", "AL"), "tirana": ("TIA", "AL"),
    "czarnogóra": ("TGD", "ME"), "czarnogóry": ("TGD", "ME"), "podgorica": ("TGD", "ME"),
    "bułgaria": ("SOF", "BG"), "bułgarii": ("SOF", "BG"), "sofia": ("SOF", "BG"),
    "burgas": ("BOJ", "BG"), "warna": ("VAR", "BG"), "rumunia": ("OTP", "RO"),
    "rumunii": ("OTP", "RO"), "bukareszt": ("OTP", "RO"), "azory": ("PDL", "PT"),
    "azorów": ("PDL", "PT"), "wyspy kanaryjskie": ("TFS", "ES"),
    # Daleki dystans
    "nowy jork": ("NYC", "US"), "nowego jorku": ("NYC", "US"), "new york": ("NYC", "US"),
    "usa": ("NYC", "US"), "stany": ("NYC", "US"), "stanów": ("NYC", "US"),
    "los angeles": ("LAX", "US"), "miami": ("MIA", "US"), "chicago": ("CHI", "US"),
    "kanada": ("YTO", "CA"), "kanady": ("YTO", "CA"), "toronto": ("YTO", "CA"),
    "meksyk": ("CUN", "MX"), "meksyku": ("CUN", "MX"), "cancun": ("CUN", "MX"),
    "kuba": ("HAV", "CU"), "kuby": ("HAV", "CU"), "dominikana": ("PUJ", "DO"),
    "dominikany": ("PUJ", "DO"), "brazylia": ("SAO", "BR"), "brazylii": ("SAO", "BR"),
    "argentyna": ("BUE", "AR"), "argentyny": ("BUE", "AR"), "peru": ("LIM", "PE"),
    "kolumbia": ("BOG", "CO"), "kolumbii": ("BOG", "CO"), "chile": ("SCL", "CL"),
    "tajlandia": ("BKK", "TH"), "tajlandii": ("BKK", "TH"), "bangkok": ("BKK", "TH"),
    "bangkoku": ("BKK", "TH"), "phuket": ("HKT", "TH"), "wietnam": ("SGN", "VN"),
    "wietnamu": ("SGN", "VN"), "bali": ("DPS", "ID"), "indonezja": ("DPS", "ID"),
    "indonezji": ("DPS", "ID"), "singapur": ("SIN", "SG"), "singapuru": ("SIN", "SG"),
    "malezja": ("KUL", "MY"), "malezji": ("KUL", "MY"), "kuala lumpur": ("KUL", "MY"),
    "japonia": ("TYO", "JP"), "japonii": ("TYO", "JP"), "tokio": ("TYO", "JP"),
    "osaka": ("OSA", "JP"), "korea": ("SEL", "KR"), "korei": ("SEL", "KR"),
    "seul": ("SEL", "KR"), "chiny": ("PEK", "CN"), "chin": ("PEK", "CN"),
    "pekin": ("PEK", "CN"), "szanghaj": ("SHA", "CN"), "hongkong": ("HKG", "HK"),
    "tajwan": ("TPE", "TW"), "tajwanu": ("TPE", "TW"), "filipiny": ("MNL", "PH"),
    "filipin": ("MNL", "PH"), "indie": ("DEL", "IN"), "indii": ("DEL", "IN"),
    "delhi": ("DEL", "IN"), "goa": ("GOI", "IN"), "sri lanka": ("CMB", "LK"),
    "sri lanki": ("CMB", "LK"), "malediwy": ("MLE", "MV"), "malediwów": ("MLE", "MV"),
    "nepal": ("KTM", "NP"), "nepalu": ("KTM", "NP"), "dubaj": ("DXB", "AE"),
    "dubaju": ("DXB", "AE"), "abu dhabi": ("AUH", "AE"), "katar": ("DOH", "QA"),
    "kataru": ("DOH", "QA"), "doha": ("DOH", "QA"), "oman": ("MCT", "OM"),
    "omanu": ("MCT", "OM"), "zanzibar": ("ZNZ", "TZ"), "zanzibaru": ("ZNZ", "TZ"),
    "kenia": ("NBO", "KE"), "kenii": ("NBO", "KE"), "rpa": ("JNB", "ZA"),
    "kapsztad": ("CPT", "ZA"), "etiopia": ("ADD", "ET"), "etiopii": ("ADD", "ET"),
    "mauritius": ("MRU", "MU"), "seszele": ("SEZ", "SC"), "seszeli": ("SEZ", "SC"),
    "australia": ("SYD", "AU"), "australii": ("SYD", "AU"), "sydney": ("SYD", "AU"),
    "nowa zelandia": ("AKL", "NZ"), "nowej zelandii": ("AKL", "NZ"),
    "hawaje": ("HNL", "US"), "hawajów": ("HNL", "US"), "uzbekistan": ("TAS", "UZ"),
    "uzbekistanu": ("TAS", "UZ"), "kazachstan": ("ALA", "KZ"), "kazachstanu": ("ALA", "KZ"),
}

# Kod IATA -> kraj (dla wyników z API, gdzie mamy kod, a nie nazwę).
IATA_COUNTRY: Dict[str, str] = {code: country for code, country in DESTINATIONS.values()}
IATA_COUNTRY.update({code: "PL" for code in POLISH_AIRPORTS if code != "BER"})
IATA_COUNTRY["BER"] = "DE"

# Odwrotne mapowanie: nazwa miasta (pierwsza z listy) dla kodu – do e-maili.
IATA_NAME: Dict[str, str] = {}
for _code, _names in POLISH_AIRPORTS.items():
    IATA_NAME[_code] = _names[0].title()
for _name, (_code, _) in DESTINATIONS.items():
    IATA_NAME.setdefault(_code, _name.title())


def normalize(text: str) -> str:
    """Małe litery + pojedyncze spacje, żeby dopasowania były stabilne."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def find_origin(text: str, home_airports: list) -> Optional[str]:
    """
    Zwróć kod polskiego lotniska wylotu, jeśli jakieś pojawia się w tekście.
    Sprawdzamy zarówno kody IATA (np. 'WAW'), jak i nazwy miast w odmianach.
    """
    norm = normalize(text)
    upper = text or ""
    for code in home_airports:
        # Kod IATA jako osobne słowo (np. "WAW-BCN", "z WAW")
        if re.search(rf"\b{code}\b", upper):
            return code
        for name in POLISH_AIRPORTS.get(code, []):
            if re.search(rf"\b{re.escape(name)}\b", norm):
                return code
    return None


def find_destination(text: str) -> Optional[tuple]:
    """Zwróć (kod, kraj) pierwszego rozpoznanego kierunku w tekście lub None."""
    norm = normalize(text)
    # Dłuższe nazwy najpierw (np. 'nowy jork' przed 'york').
    for name in sorted(DESTINATIONS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", norm):
            return DESTINATIONS[name]
    # Druga próba: dopasowanie po temacie wyrazu, żeby złapać odmiany,
    # których nie ma w słowniku (np. 'Lizbonie', 'Rzymie', 'Chinach').
    for name in sorted(DESTINATIONS, key=len, reverse=True):
        if len(name) >= 5 and " " not in name:
            stem = re.escape(name[:-1])
            if re.search(rf"\b{stem}\w{{0,3}}\b", norm):
                return DESTINATIONS[name]
    # Kody IATA (3 wielkie litery) – pomijamy polskie lotniska.
    for code in re.findall(r"\b([A-Z]{3})\b", text or ""):
        if code in IATA_COUNTRY and IATA_COUNTRY[code] != "PL":
            return code, IATA_COUNTRY[code]
    return None


def is_longhaul(country_code: Optional[str]) -> bool:
    """True, jeśli kraj docelowy leży poza Europą (szeroko rozumianą)."""
    if not country_code:
        return False
    return country_code.upper() not in EUROPE_COUNTRIES
