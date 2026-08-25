# -*- coding: utf-8 -*-
"""
SokIFynddata.pyt

Söker i SLU Artdatabankens Species Observation System (SOS) och skriver
resultatet till en punkt-featureklass. Sökfiltren följer webbapplikationen
Fynddata (https://fynddata.artdatabanken.se), grupperade som i dess flikar:
Taxa, Tid, Geografi, Fyndegenskaper och Dataset.

API      : https://api.artdatabanken.se/species-observation-system/v1
Nyckel   : https://api-portal.artdatabanken.se/ (Ocp-Apim-Subscription-Key)
Filter   : SearchFilterDto i https://github.com/biodiversitydata-se/SOS

Endpoints som används (verifierade mot v1 2026-08-25):
    POST /Observations/Count           - antal träffar, för progressorn
    POST /Observations/SearchByCursor  - hämtar alla poster, ?cursor=<nextCursor>
    GET  /Areas?areaTypes=<typ>        - områdesnamn -> featureId
    GET  /TaxonLists                   - artlistornas id
    GET  /DataProviders                - datasetens id

Begränsningar i det publika API:et:
  - Skyddade (skyddsklassade) fynd kräver personlig inloggning (OAuth). Med enbart
    en API-nyckel returneras det publika urvalet.
  - Fynddatas Artportalen-specifika fyndegenskaper (aktiviteter, ospontan,
    IAS-åtgärder, sök på period över flera år) ligger i SOS interna filter och går
    inte att nå med en API-nyckel. De saknas därför här.
"""

import arcpy
import datetime
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# =============================================================================
# Konstanter
# =============================================================================

API_BASE = "https://api.artdatabanken.se/species-observation-system/v1"

TAKE = 1000                 # API:ets maxvärde per anrop
HTTP_RETRIES = 4
HTTP_TIMEOUT = 180
USER_AGENT = "ArcGIS-Pro-SokIFynddata/1.0"

SWEREF99TM = 3006
WGS84 = 4326

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                       "ArcGIS Fynddata")
PRESET_DIR = os.path.join(APP_DIR, "presets")
CACHE_DIR = os.path.join(APP_DIR, "cache")

# --- Rödlistekategorier ------------------------------------------------------

REDLIST = [
    ("RE", "Nationellt utdöd (RE)"),
    ("CR", "Akut hotad (CR)"),
    ("EN", "Starkt hotad (EN)"),
    ("VU", "Sårbar (VU)"),
    ("NT", "Nära hotad (NT)"),
    ("DD", "Kunskapsbrist (DD)"),
    ("LC", "Livskraftig (LC)"),
    ("NA", "Ej tillämplig (NA)"),
    ("NE", "Ej bedömd (NE)"),
]

# --- Artlistor. Id:n från GET /TaxonLists, kontrollerade 2026-08-25 ----------

TAXON_LISTS = [
    (1,  "Fridlysta arter"),
    (2,  "Signalarter"),
    (4,  "Främmande arter i Sverige"),
    (5,  "EU-förordning 1143/2014, invasiva främmande arter"),
    (42, "Nationell förteckning över invasiva främmande arter"),
    (19, "Risklista - Mycket hög risk (SE)"),
    (20, "Risklista - Hög risk (HI)"),
    (21, "Risklista - Potentiellt hög risk (PH)"),
    (22, "Risklista - Låg risk (LO)"),
    (23, "Risklista - Ingen känd risk (NK)"),
    (9,  "Habitatdirektivets bilaga 2"),
    (10, "Habitatdirektivets bilaga 2 (prioriterad art)"),
    (11, "Habitatdirektivets bilaga 4"),
    (12, "Habitatdirektivets bilaga 5"),
    (15, "Fågeldirektivet - bilaga 1"),
    (16, "Fågeldirektivet - bilaga 2"),
    (14, "Prioriterade fåglar i Skogsvårdslagen"),
    (17, "Åtgärdsprogram"),
    (18, "Skogsstyrelsens naturvårdsarter"),
    (24, "Rödlistade - Nära hotad (NT)"),
    (25, "Rödlistade - Sårbar (VU)"),
    (26, "Rödlistade - Starkt hotad (EN)"),
    (27, "Rödlistade - Akut hotad (CR)"),
    (28, "Rödlistade - Nationellt utdöd (RE)"),
    (29, "Rödlistade - Kunskapsbrist (DD)"),
]

TAXON_LIST_OPERATOR = [
    ("Merge", "Komplettera urvalet (Merge)"),
    ("Filter", "Begränsa urvalet (Filter)"),
]

# --- Dataset. Id:n från GET /DataProviders, kontrollerade 2026-08-25 ---------

PROVIDERS = [
    (1,  "Artportalen"),
    (3,  "Databasen för provfiske vid kusten KUL"),
    (4,  "Miljödata MVM"),
    (5,  "Nationellt Register över Sjöprovfisken NORS"),
    (6,  "Svenskt Elfiskeregister SERS"),
    (7,  "Virtuella Herbariet"),
    (8,  "SHARK Svenskt HavsARKiv"),
    (9,  "Ringmärkningscentralen, via GBIF"),
    (10, "Entomologiska samlingarna (NHRS), via GBIF"),
    (11, "Svenska Malaisefälle projektet (SMTP), via GBIF"),
    (12, "Tumlare Observationsdatabasen, via GBIF"),
    (13, "Svensk Dagfjärilsövervakning, via GBIF"),
    (16, "Riksskogstaxeringen: vegetationsförekomst"),
    (17, "Observationsdatabasen"),
    (18, "Biologg"),
    (19, "iNaturalist Sverige"),
    (20, "Svensk Fågeltaxering: Nationell kustfågelövervakning, via GBIF"),
    (21, "Svensk Fågeltaxering: Sommarpunktrutter, via GBIF"),
    (22, "Svensk Fågeltaxering: Standardrutter, via GBIF"),
    (23, "GDP Häckande Kustfåglar i Bottniska Viken, via GBIF"),
    (24, "Nationell ängs- och betesmarksinventering (TUVA), via GBIF"),
    (25, "Nationella inventeringar av landskapet i Sverige (NILS)"),
    (26, "Samlingar från Göteborgs naturhistoriska museum (GNM), via GBIF"),
    (27, "Lunds universitets biologiska museum - Faunistiska samlingar"),
    (28, "Riksskogstaxeringen: vegetationsförekomst, upphört stickprov"),
    (29, "Kusttrålning"),
]

# --- Områdestyper ------------------------------------------------------------

AREA_TYPES = [
    ("County", "Län"),
    ("Municipality", "Kommun"),
    ("Province", "Provins"),
    ("Parish", "Socken"),
    ("WaterArea", "Vattenområde"),
    ("BirdValidationArea", "Fågelområde"),
    ("CountryRegion", "Region"),
    ("ProtectedNature", "Skyddad natur"),
    ("Spa", "Spa"),
    ("Sci", "Sci"),
    ("SwedishForestAgencyDistricts", "Skogsstyrelsens distrikt"),
    ("Ramsar", "Ramsar"),
    ("NatureType", "Naturtyp"),
]

# Områdestyper som är små och stabila nog att ligga inbakade, så att
# områdesväljaren fungerar direkt utan nätanrop i valideringen.
# Övriga typer hämtas av verktyget "Uppdatera referenslistor" och cachas.

COUNTIES = {
    "10": "Blekinge",
    "20": "Dalarna",
    "9": "Gotland",
    "21": "Gävleborg",
    "13": "Halland",
    "23": "Jämtland",
    "6": "Jönköping",
    "8": "Kalmar",
    "7": "Kronoberg",
    "25": "Norrbotten",
    "12": "Skåne",
    "1": "Stockholm",
    "4": "Södermanland",
    "3": "Uppsala",
    "17": "Värmland",
    "24": "Västerbotten",
    "22": "Västernorrland",
    "19": "Västmanland",
    "14": "Västra Götaland",
    "18": "Örebro",
    "5": "Östergötland",
}

PROVINCES = {
    "2": "Blekinge",
    "7": "Bohuslän",
    "31": "Bottenhavet",
    "30": "Bottenviken",
    "16": "Dalarna",
    "8": "Dalsland",
    "5": "Gotland",
    "17": "Gästrikland",
    "6": "Halland",
    "18": "Hälsingland",
    "23": "Härjedalen",
    "24": "Jämtland",
    "33": "Kattegatt",
    "28": "Lule lappmark",
    "26": "Lycksele lappmark",
    "19": "Medelpad",
    "22": "Norrbotten",
    "10": "Närke",
    "27": "Pite lappmark",
    "34": "Skagerrak",
    "1": "Skåne",
    "3": "Småland",
    "12": "Södermanland",
    "29": "Torne lappmark",
    "13": "Uppland",
    "15": "Värmland",
    "21": "Västerbotten",
    "9": "Västergötland",
    "14": "Västmanland",
    "20": "Ångermanland",
    "25": "Åsele lappmark",
    "4": "Öland",
    "11": "Östergötland",
    "32": "Östersjön",
}

MUNICIPALITIES = {
    "1440": "Ale",
    "1489": "Alingsås",
    "764": "Alvesta",
    "604": "Aneby",
    "1984": "Arboga",
    "2506": "Arjeplog",
    "2505": "Arvidsjaur",
    "1784": "Arvika",
    "1882": "Askersund",
    "2084": "Avesta",
    "1460": "Bengtsfors",
    "2326": "Berg",
    "2403": "Bjurholm",
    "1260": "Bjuv",
    "2582": "Boden",
    "1443": "Bollebygd",
    "2183": "Bollnäs",
    "885": "Borgholm",
    "2081": "Borlänge",
    "1490": "Borås",
    "127": "Botkyrka",
    "560": "Boxholm",
    "1272": "Bromölla",
    "2305": "Bräcke",
    "1231": "Burlöv",
    "1278": "Båstad",
    "1438": "Dals-Ed",
    "162": "Danderyd",
    "1862": "Degerfors",
    "2425": "Dorotea",
    "1730": "Eda",
    "125": "Ekerö",
    "686": "Eksjö",
    "862": "Emmaboda",
    "381": "Enköping",
    "484": "Eskilstuna",
    "1285": "Eslöv",
    "1445": "Essunga",
    "1982": "Fagersta",
    "1382": "Falkenberg",
    "1499": "Falköping",
    "2080": "Falun",
    "1782": "Filipstad",
    "562": "Finspång",
    "482": "Flen",
    "1763": "Forshaga",
    "1439": "Färgelanda",
    "2026": "Gagnef",
    "662": "Gislaved",
    "461": "Gnesta",
    "617": "Gnosjö",
    "980": "Gotland",
    "1764": "Grums",
    "1444": "Grästorp",
    "1447": "Gullspång",
    "2523": "Gällivare",
    "2180": "Gävle",
    "1480": "Göteborg",
    "1471": "Götene",
    "643": "Habo",
    "1783": "Hagfors",
    "1861": "Hallsberg",
    "1961": "Hallstahammar",
    "1380": "Halmstad",
    "1761": "Hammarö",
    "136": "Haninge",
    "2583": "Haparanda",
    "331": "Heby",
    "2083": "Hedemora",
    "1283": "Helsingborg",
    "1466": "Herrljunga",
    "1497": "Hjo",
    "2104": "Hofors",
    "126": "Huddinge",
    "2184": "Hudiksvall",
    "860": "Hultsfred",
    "1315": "Hylte",
    "1863": "Hällefors",
    "2361": "Härjedalen",
    "2280": "Härnösand",
    "1401": "Härryda",
    "1293": "Hässleholm",
    "305": "Håbo",
    "1284": "Höganäs",
    "821": "Högsby",
    "1266": "Hörby",
    "1267": "Höör",
    "2510": "Jokkmokk",
    "123": "Järfälla",
    "680": "Jönköping",
    "2514": "Kalix",
    "880": "Kalmar",
    "1446": "Karlsborg",
    "1082": "Karlshamn",
    "1883": "Karlskoga",
    "1080": "Karlskrona",
    "1780": "Karlstad",
    "483": "Katrineholm",
    "1715": "Kil",
    "513": "Kinda",
    "2584": "Kiruna",
    "1276": "Klippan",
    "330": "Knivsta",
    "2282": "Kramfors",
    "1290": "Kristianstad",
    "1781": "Kristinehamn",
    "2309": "Krokom",
    "1881": "Kumla",
    "1384": "Kungsbacka",
    "1960": "Kungsör",
    "1482": "Kungälv",
    "1261": "Kävlinge",
    "1983": "Köping",
    "1381": "Laholm",
    "1282": "Landskrona",
    "1860": "Laxå",
    "1814": "Lekeberg",
    "2029": "Leksand",
    "1441": "Lerum",
    "761": "Lessebo",
    "186": "Lidingö",
    "1494": "Lidköping",
    "1462": "Lilla Edet",
    "1885": "Lindesberg",
    "580": "Linköping",
    "781": "Ljungby",
    "2161": "Ljusdal",
    "1864": "Ljusnarsberg",
    "1262": "Lomma",
    "2085": "Ludvika",
    "2580": "Luleå",
    "1281": "Lund",
    "2481": "Lycksele",
    "1484": "Lysekil",
    "1280": "Malmö",
    "2023": "Malung-Sälen",
    "2418": "Malå",
    "1493": "Mariestad",
    "1463": "Mark",
    "767": "Markaryd",
    "1461": "Mellerud",
    "586": "Mjölby",
    "2062": "Mora",
    "583": "Motala",
    "642": "Mullsjö",
    "1430": "Munkedal",
    "1762": "Munkfors",
    "1481": "Mölndal",
    "861": "Mönsterås",
    "840": "Mörbylånga",
    "182": "Nacka",
    "1884": "Nora",
    "1962": "Norberg",
    "2132": "Nordanstig",
    "2401": "Nordmaling",
    "581": "Norrköping",
    "188": "Norrtälje",
    "2417": "Norsjö",
    "881": "Nybro",
    "140": "Nykvarn",
    "480": "Nyköping",
    "192": "Nynäshamn",
    "682": "Nässjö",
    "2101": "Ockelbo",
    "1060": "Olofström",
    "2034": "Orsa",
    "1421": "Orust",
    "1273": "Osby",
    "882": "Oskarshamn",
    "2121": "Ovanåker",
    "481": "Oxelösund",
    "2521": "Pajala",
    "1402": "Partille",
    "1275": "Perstorp",
    "2581": "Piteå",
    "2303": "Ragunda",
    "2409": "Robertsfors",
    "1081": "Ronneby",
    "2031": "Rättvik",
    "1981": "Sala",
    "128": "Salem",
    "2181": "Sandviken",
    "191": "Sigtuna",
    "1291": "Simrishamn",
    "1265": "Sjöbo",
    "1495": "Skara",
    "2482": "Skellefteå",
    "1904": "Skinnskatteberg",
    "1264": "Skurup",
    "1496": "Skövde",
    "2061": "Smedjebacken",
    "2283": "Sollefteå",
    "163": "Sollentuna",
    "184": "Solna",
    "2422": "Sorsele",
    "1427": "Sotenäs",
    "1230": "Staffanstorp",
    "1415": "Stenungsund",
    "180": "Stockholm",
    "1760": "Storfors",
    "2421": "Storuman",
    "486": "Strängnäs",
    "1486": "Strömstad",
    "2313": "Strömsund",
    "183": "Sundbyberg",
    "2281": "Sundsvall",
    "1766": "Sunne",
    "1907": "Surahammar",
    "1214": "Svalöv",
    "1263": "Svedala",
    "1465": "Svenljunga",
    "1785": "Säffle",
    "2082": "Säter",
    "684": "Sävsjö",
    "2182": "Söderhamn",
    "582": "Söderköping",
    "181": "Södertälje",
    "1083": "Sölvesborg",
    "1435": "Tanum",
    "1472": "Tibro",
    "1498": "Tidaholm",
    "360": "Tierp",
    "2262": "Timrå",
    "763": "Tingsryd",
    "1419": "Tjörn",
    "1270": "Tomelilla",
    "1737": "Torsby",
    "834": "Torsås",
    "1452": "Tranemo",
    "687": "Tranås",
    "1287": "Trelleborg",
    "1488": "Trollhättan",
    "488": "Trosa",
    "138": "Tyresö",
    "160": "Täby",
    "1473": "Töreboda",
    "1485": "Uddevalla",
    "1491": "Ulricehamn",
    "2480": "Umeå",
    "114": "Upplands Väsby",
    "139": "Upplands-Bro",
    "380": "Uppsala",
    "760": "Uppvidinge",
    "584": "Vadstena",
    "665": "Vaggeryd",
    "563": "Valdemarsvik",
    "115": "Vallentuna",
    "2021": "Vansbro",
    "1470": "Vara",
    "1383": "Varberg",
    "187": "Vaxholm",
    "1233": "Vellinge",
    "685": "Vetlanda",
    "2462": "Vilhelmina",
    "884": "Vimmerby",
    "2404": "Vindeln",
    "428": "Vingåker",
    "1487": "Vänersborg",
    "2460": "Vännäs",
    "120": "Värmdö",
    "683": "Värnamo",
    "883": "Västervik",
    "1980": "Västerås",
    "780": "Växjö",
    "1442": "Vårgårda",
    "512": "Ydre",
    "1286": "Ystad",
    "765": "Älmhult",
    "2039": "Älvdalen",
    "319": "Älvkarleby",
    "2560": "Älvsbyn",
    "1292": "Ängelholm",
    "1492": "Åmål",
    "2260": "Ånge",
    "2321": "Åre",
    "1765": "Årjäng",
    "2463": "Åsele",
    "1277": "Åstorp",
    "561": "Åtvidaberg",
    "1407": "Öckerö",
    "509": "Ödeshög",
    "1880": "Örebro",
    "1257": "Örkelljunga",
    "2284": "Örnsköldsvik",
    "2380": "Östersund",
    "117": "Österåker",
    "382": "Östhammar",
    "1256": "Östra Göinge",
    "2513": "Överkalix",
    "2518": "Övertorneå",
}

BUILTIN_AREAS = {
    "County": COUNTIES,
    "Province": PROVINCES,
    "Municipality": MUNICIPALITIES,
}

# --- Val i dialogen ----------------------------------------------------------

DATE_PRESETS = [
    "Alla år",
    "Innevarande år",
    "Föregående år",
    "Senaste 5 åren",
    "Senaste 25 åren",
    "Anpassat",
]

DATE_FILTER_TYPES = [
    ("OverlappingStartDateAndEndDate", "Fyndet överlappar perioden (inkludera periodfynd)"),
    ("BetweenStartDateAndEndDate", "Fyndet ligger helt inom perioden"),
    ("OnlyStartDate", "Endast startdatum inom perioden"),
    ("OnlyEndDate", "Endast slutdatum inom perioden"),
]

TIME_RANGES = [
    ("Morning", "Morgon"),
    ("Forenoon", "Förmiddag"),
    ("Afternoon", "Eftermiddag"),
    ("Evening", "Kväll"),
]

OCCURRENCE_STATUS = [
    ("present", "Endast observerad"),
    ("absent", "Endast ej observerad"),
    ("BothPresentAndAbsent", "Både observerad och ej observerad"),
]

NOT_RECOVERED = [
    ("NoFilter", "Ingen filtrering"),
    ("DontIncludeNotRecovered", "Uteslut ej återfunna"),
    ("OnlyNotRecovered", "Endast ej återfunna"),
    ("IncludeNotRecovered", "Inkludera ej återfunna"),
]

DETERMINATION = [
    ("NoFilter", "Ingen filtrering"),
    ("NotUnsureDetermination", "Endast säker artbestämning"),
    ("OnlyUnsureDetermination", "Endast osäker artbestämning"),
]

VERIFICATION = [
    ("BothVerifiedAndNotVerified", "Ingen filtrering"),
    ("Verified", "Endast verifierade"),
    ("NotVerified", "Endast ej verifierade"),
]

BIRD_NEST = [
    (1,  "bo, ägg/ungar"),
    (2,  "bo, hörda ungar"),
    (3,  "misslyckad häckning"),
    (4,  "ruvande"),
    (5,  "äggskal"),
    (6,  "föda åt ungar"),
    (7,  "bär exkrementsäck"),
    (8,  "besöker bebott bo"),
    (9,  "pulli/nyligen flygga ungar"),
    (10, "nyligen använt bo"),
    (11, "avledningsbeteende"),
    (12, "bobygge"),
    (13, "ruvfläckar"),
    (14, "upprörd, varnande"),
    (15, "bobesök?"),
    (16, "parning/parningsceremonier"),
    (17, "permanent revir"),
    (18, "par i lämplig häckbiotop"),
    (19, "spel/sång"),
    (20, "obs i häcktid, lämplig biotop"),
]

# =============================================================================
# Fältkarta
# =============================================================================
# (json_path, fältnamn, typ, längd, alias)
# Sökvägarna är kontrollerade mot 1000 riktiga poster från flera dataset
# 2026-08-25. API:et utelämnar tomma fält, så att ett fält som saknas på en post
# betyder att posten inte har värdet, inte att sökvägen är fel.

FIELD_MAP = [
    ("occurrence.occurrenceId", "occurrence_id", "TEXT", 255, "Observation GUID"),
    ("occurrence.occurrenceStatus.value", "patraffad", "TEXT", 50, "Påträffad"),
    ("taxon.id", "taxonid", "LONG", None, "Taxon ID"),
    ("taxon.scientificName", "vetnamn", "TEXT", 255, "Vetenskapligt namn"),
    ("taxon.vernacularName", "svenamn", "TEXT", 255, "Svenskt namn"),
    ("taxon.attributes.taxonCategory.value", "taxkat", "TEXT", 50, "Taxonkategori"),
    ("taxon.attributes.organismGroup", "organismgrp", "TEXT", 100, "Organismgrupp"),
    ("taxon.attributes.redlistCategory", "redcat", "TEXT", 20, "Rödlistekategori"),
    ("occurrence.sensitivityCategory", "skyddsklass", "LONG", None, "Fyndets skyddsklass"),
    ("event.startDate", "startdat", "DATE", None, "Startdatum"),
    ("event.endDate", "slutdat", "DATE", None, "Slutdatum"),
    ("event.plainStartTime", "starttid", "TEXT", 20, "Starttid"),
    ("event.plainEndTime", "sluttid", "TEXT", 20, "Sluttid"),
    ("location.sweref99TmX", "swe99_x", "LONG", None, "SWEREF99TM X (öst)"),
    ("location.sweref99TmY", "swe99_y", "LONG", None, "SWEREF99TM Y (nord)"),
    ("location.decimalLongitude", "wgs84_lon", "DOUBLE", None, "Longitud (WGS84)"),
    ("location.decimalLatitude", "wgs84_lat", "DOUBLE", None, "Latitud (WGS84)"),
    ("location.coordinateUncertaintyInMeters", "noggrann", "LONG", None, "Koordinatnoggrannhet (m)"),
    ("location.locality", "lokal", "TEXT", 500, "Lokal"),
    ("location.locationRemarks", "lokalkom", "TEXT", 1000, "Lokalkommentar"),
    ("location.county.name", "lan", "TEXT", 100, "Län"),
    ("location.municipality.name", "kommun", "TEXT", 100, "Kommun"),
    ("location.province.name", "provins", "TEXT", 100, "Provins"),
    ("location.parish.name", "socken", "TEXT", 100, "Socken"),
    ("occurrence.recordedBy", "observator", "TEXT", 500, "Observatör"),
    ("occurrence.reportedBy", "rapportor", "TEXT", 500, "Rapportör"),
    ("occurrence.individualCount", "antind", "TEXT", 100, "Antal individer"),
    ("occurrence.organismQuantity", "kvantitet", "TEXT", 100, "Organismkvantitet"),
    ("occurrence.organismQuantityUnit.value", "kvantenhet", "TEXT", 100, "Organismkvantitetsenhet"),
    ("occurrence.lifeStage.value", "stadium", "TEXT", 100, "Stadium/ålder"),
    ("occurrence.sex.value", "kon", "TEXT", 50, "Kön"),
    ("occurrence.activity.value", "aktivitet", "TEXT", 100, "Aktivitet"),
    ("occurrence.behavior.value", "beteende", "TEXT", 100, "Beteende"),
    ("occurrence.occurrenceRemarks", "kommentar", "TEXT", 2000, "Kommentar"),
    ("identification.verificationStatus.value", "verstat", "TEXT", 100, "Verifieringsstatus"),
    ("identification.uncertainIdentification", "osaker_best", "TEXT", 10, "Osäker artbestämning"),
    ("occurrence.isNeverFoundObservation", "ald_funnen", "TEXT", 10, "Aldrig funnen"),
    ("occurrence.isNotRediscoveredObservation", "ej_aterfunnen", "TEXT", 10, "Ej återfunnen"),
    ("occurrence.isNaturalOccurrence", "spontan", "TEXT", 10, "Spontan"),
    ("datasetName", "dataset", "TEXT", 255, "Dataset"),
    ("event.habitat", "habitat", "TEXT", 2000, "Habitat"),
    ("occurrence.biotope.value", "biotop", "TEXT", 255, "Biotop"),
    ("occurrence.substrate.description", "substrat", "TEXT", 1000, "Substrat"),
    ("taxon.attributes.protectedByLaw", "fridlyst", "TEXT", 10, "Fridlyst"),
    ("taxon.attributes.actionPlan", "agp", "TEXT", 100, "Åtgärdsprogram"),
    ("taxon.birdDirectiveAnnex1", "fagdir_b1", "TEXT", 10, "Fågeldirektivet bilaga 1"),
    ("taxon.attributes.natura2000HabitatsDirectiveArticle2", "habdir_b2", "TEXT", 10, "Habitatdirektivet bilaga 2"),
    ("taxon.attributes.natura2000HabitatsDirectiveArticle4", "habdir_b4", "TEXT", 10, "Habitatdirektivet bilaga 4"),
    ("taxon.attributes.natura2000HabitatsDirectiveArticle5", "habdir_b5", "TEXT", 10, "Habitatdirektivet bilaga 5"),
    ("taxon.attributes.isInvasiveInSweden", "frammande", "TEXT", 10, "Främmande i Sverige"),
    ("occurrence.catalogNumber", "urs_id", "TEXT", 255, "Ursprungskällans observations-id"),
    ("occurrence.reportedDate", "rapdat", "DATE", None, "Rapporterad (datum)"),
    ("modified", "andrad", "DATE", None, "Ändrad"),
    ("ownerInstitutionCode", "dataagare", "TEXT", 255, "Dataägare"),
    ("projectsSummary.project1Name", "projekt", "TEXT", 255, "Projekt"),
    ("occurrence.url", "url", "TEXT", 500, "Länk"),
    ("isGeneralized", "diffuserad", "TEXT", 10, "Diffuserad"),
]


# =============================================================================
# Småhjälpare
# =============================================================================

def _labels(pairs):
    """Etiketterna ur en lista med (kod, etikett)."""
    return [label for _, label in pairs]


def _code_for(pairs, label):
    """Koden bakom en etikett. None om etiketten inte finns."""
    for code, text in pairs:
        if text == label:
            return code
    return None


def _codes_for(pairs, labels):
    if not labels:
        return []
    out = []
    for label in labels:
        code = _code_for(pairs, label)
        if code is not None:
            out.append(code)
    return out


def _get_nested(obj, path):
    """Hämta ett värde ur en nästlad dict med punktnotation."""
    cur = obj
    for key in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def _multi_values(parameter):
    """Värdena ur en multiValue-parameter som en lista med strängar."""
    if parameter.value is None:
        return []
    try:
        return [str(v) for v in parameter.valueAsText.split(";") if v]
    except AttributeError:
        return []


def _clean_multi(parameter):
    """Som _multi_values men tar bort citattecken som Pro lägger på värden
    med blanksteg i."""
    out = []
    for value in _multi_values(parameter):
        value = value.strip()
        if len(value) > 1 and value[0] == "'" and value[-1] == "'":
            value = value[1:-1]
        out.append(value)
    return out


def _parse_date(value):
    """GPDate ger ett datetime. Returnera ISO-datum eller None."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(text[:19], fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10]


def _to_datetime(value):
    """ISO-sträng från API:et till datetime, eller None."""
    if not value:
        return None
    text = str(value)
    # "2020-10-14T19:35:25+02:00" -> släpp tidszonen, arcpy vill ha naiv tid
    text = text.replace("Z", "")
    if "+" in text[10:]:
        text = text[:10] + text[10:].split("+")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def _spatial_reference(parameter):
    """GPCoordinateSystem ger WKT2, som arcpy.SpatialReference(text) inte klarar.
    loadFromString läser både WKT2 och gammal WKT."""
    if parameter is None or not parameter.valueAsText:
        return arcpy.SpatialReference(SWEREF99TM)
    text = parameter.valueAsText
    sr = arcpy.SpatialReference()
    try:
        sr.loadFromString(text)
    except Exception:
        try:
            sr = arcpy.SpatialReference(text)
        except Exception:
            raise ValueError(
                "Kunde inte tolka det valda koordinatsystemet. "
                "Välj ett annat system eller lämna fältet tomt för SWEREF99TM."
            )
    if not (sr.factoryCode or sr.exportToString()):
        raise ValueError("Det valda koordinatsystemet saknar definition.")
    return sr


def _log(messages, text):
    if messages is not None:
        messages.addMessage(text)
    else:
        print(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)
    else:
        print("VARNING: " + text)


# =============================================================================
# HTTP
# =============================================================================

def _headers(api_key):
    return {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }


def _request(url, api_key, body=None, timeout=HTTP_TIMEOUT):
    """GET om body är None, annars POST. Gör om vid tillfälliga fel."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    last = None

    for attempt in range(HTTP_RETRIES):
        request = urllib.request.Request(url, data=data, headers=_headers(api_key))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # HTTPError ärver både URLError och OSError, så den måste fångas först.
            if exc.code in (408, 429) or exc.code >= 500:
                last = exc
            else:
                raise ValueError(_http_message(exc))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc

        if attempt < HTTP_RETRIES - 1:
            time.sleep(2 ** attempt)

    raise ValueError(
        "Anropet till SOS API misslyckades efter {} försök: {}".format(HTTP_RETRIES, last)
    )


def _http_message(exc):
    """Läsbart svenskt felmeddelande ur ett HTTPError."""
    try:
        body = exc.read().decode("utf-8")[:400]
    except Exception:
        body = ""

    if exc.code == 401:
        return ("API-nyckeln avvisades (401). Kontrollera nyckeln på "
                "https://api-portal.artdatabanken.se/")
    if exc.code == 403:
        return ("API-nyckeln saknar behörighet för anropet (403). "
                "Skyddade fynd kräver personlig inloggning och går inte att "
                "hämta med enbart en API-nyckel.")
    if exc.code == 404:
        return "Ändpunkten hittades inte (404). API:et kan ha ändrats: {}".format(exc.url)
    return "SOS API svarade {} {}. {}".format(exc.code, exc.reason, body)


# =============================================================================
# Referenslistor: inbakade eller cachade under %LOCALAPPDATA%
# =============================================================================

def _cache_path(area_type):
    return os.path.join(CACHE_DIR, "areas_{}.json".format(area_type))


def _load_areas(area_type):
    """Lista med (featureId, namn) för en områdestyp. Cachen vinner över de
    inbakade listorna, så en uppdatering slår igenom. Aldrig nätanrop härifrån:
    den här funktionen anropas från updateParameters."""
    path = _cache_path(area_type)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                return [(str(a["id"]), a["name"]) for a in data]
            # aldre cacheformat: {featureId: namn}
            return sorted(data.items(), key=lambda item: item[1])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    builtin = BUILTIN_AREAS.get(area_type)
    if builtin:
        return sorted(builtin.items(), key=lambda item: item[1])
    return []


def _area_labels(pairs):
    """{etikett: featureId} for en lista med (featureId, namn).

    Områdesnamn är inte unika. 187 av Sveriges 2326 socknar delar namn med
    minst en annan socken, och för skyddad natur är det 857 av 7224. Att slå
    upp ett område på namn väljer då godtyckligt ett av dem och söker tyst på
    fel plats. Namn som förekommer flera gånger får därför sitt featureId i
    etiketten."""
    counts = {}
    for _, name in pairs:
        counts[name] = counts.get(name, 0) + 1
    labels = {}
    for feature_id, name in pairs:
        if counts[name] == 1:
            labels[name] = feature_id
        else:
            labels["{} ({})".format(name, feature_id)] = feature_id
    return labels


def _download_areas(area_type, api_key):
    """Hämta alla områden av en typ och skriv till cachen. Returnerar antalet.

    Hämtas i ETT anrop med stort take. /Areas sidindelas inte stabilt: samma
    fråga med skip och take=500 gav 1931 unika featureId och take=1000 gav
    2315, av 2433 faktiska socknar. Sidorna är alltså inte konsekvent
    sorterade, så skip hoppar över poster och upprepar andra utan att fela.
    Ett enda anrop ger hela mängden och inga dubbletter. Ingen av de
    områdestyper verktyget erbjuder är större än 7 224 poster.
    """
    probe = _request("{}/Areas?areaTypes={}&take=1".format(API_BASE, area_type),
                     api_key, timeout=60)
    total = probe.get("totalCount") or 0
    if total == 0:
        return 0

    url = "{}/Areas?areaTypes={}&take={}".format(API_BASE, area_type, total)
    data = _request(url, api_key, timeout=180)
    records = data.get("records") or []
    if len(records) < total:
        raise ValueError(
            "API:et returnerade {} av {} områden för {}. Försök igen."
            .format(len(records), total, area_type)
        )

    areas = [{"id": str(r["featureId"]), "name": r["name"]} for r in records]
    areas.sort(key=lambda area: area["name"])

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(area_type), "w", encoding="utf-8") as handle:
        json.dump(areas, handle, ensure_ascii=False, indent=0)
    return len(areas)


# =============================================================================
# Förinställningar
# =============================================================================
# En förinställning är en JSON-fil med värdena för sökparametrarna. Utdata och
# API-nyckel sparas aldrig: nyckeln är en hemlighet och utdata är per körning.

def _preset_path(name):
    safe = "".join(c for c in name if c.isalnum() or c in " -_()").strip()
    if not safe:
        raise ValueError("Namnet på förinställningen innehåller inga giltiga tecken.")
    return os.path.join(PRESET_DIR, safe + ".json")


def _list_presets():
    try:
        names = [f[:-5] for f in os.listdir(PRESET_DIR) if f.lower().endswith(".json")]
    except OSError:
        return []
    return sorted(names)


def _save_preset(name, parameters, first, last):
    """Spara parametrarna first..last (inklusive) under ett namn."""
    values = {}
    for index in range(first, last + 1):
        parameter = parameters[index]
        if parameter.datatype == "Feature Set":
            continue                      # en ritad polygon går inte att serialisera
        values[parameter.name] = parameter.valueAsText

    os.makedirs(PRESET_DIR, exist_ok=True)
    payload = {
        "name": name,
        "saved": datetime.datetime.now().isoformat(timespec="seconds"),
        "values": values,
    }
    path = _preset_path(name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def _apply_preset(name, parameters, first, last):
    """Läs en förinställning och skriv in värdena i parametrarna."""
    path = _preset_path(name)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            values = (json.load(handle) or {}).get("values") or {}
    except (OSError, ValueError):
        return False

    for index in range(first, last + 1):
        parameter = parameters[index]
        if parameter.name not in values:
            continue
        if parameter.datatype == "Feature Set":
            continue
        text = values[parameter.name]
        if text in (None, ""):
            parameter.value = None
        elif parameter.datatype == "Boolean":
            parameter.value = str(text).lower() == "true"
        else:
            parameter.value = text
    return True


def _delete_preset(name):
    path = _preset_path(name)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


# =============================================================================
# Sökområde
# =============================================================================

def _aoi_geometries(aoi_value, messages=None):
    """Polygonerna i sökområdet som GeoJSON i WGS84.

    SOS vill ha longitud/latitud. Ett lager utan koordinatsystem går inte att
    projicera: projectAs() returnerar då indata oförändrat i stället för att
    fela, vilket ger tyst fel geografi. Därför avbryter vi i stället.
    """
    if aoi_value is None:
        return []

    wgs84 = arcpy.SpatialReference(WGS84)
    geometries = []

    describe = arcpy.Describe(aoi_value)
    source_sr = getattr(describe, "spatialReference", None)
    if source_sr is None or not (source_sr.factoryCode or source_sr.exportToString()):
        raise ValueError(
            "Sökområdet saknar koordinatsystem. Ange ett koordinatsystem för "
            "lagret innan sökningen körs."
        )

    with arcpy.da.SearchCursor(aoi_value, ["SHAPE@"]) as cursor:
        for (shape,) in cursor:
            if shape is None:
                continue
            shape = shape.projectAs(wgs84)
            for part in shape:
                ring = [[round(point.X, 7), round(point.Y, 7)]
                        for point in part if point is not None]
                if len(ring) < 3:
                    continue
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                geometries.append({"type": "polygon", "coordinates": [ring]})

    if not geometries:
        raise ValueError("Sökområdet innehåller inga giltiga polygoner.")

    _log(messages, "  Sökområde: {} polygon(er), {} hörn totalt.".format(
        len(geometries), sum(len(g["coordinates"][0]) for g in geometries)))
    return geometries


# =============================================================================
# Sökfilter
# =============================================================================
# Varning: SOS API tar emot okända egenskaper utan att klaga och struntar i dem.
# Ett felstavat eller felplacerat fältnamn ger alltså inget fel, bara ett
# resultat utan det filtret. Ändra inget här utan att kontrollera mot ett
# faktiskt antal träffar.

def _date_range(preset, start_date, end_date):
    """(startdatum, slutdatum) som ISO-strängar för en vald tidsperiod."""
    today = datetime.date.today()

    if preset == "Alla år":
        return None, None
    if preset == "Innevarande år":
        return "{}-01-01".format(today.year), today.isoformat()
    if preset == "Föregående år":
        return "{}-01-01".format(today.year - 1), "{}-12-31".format(today.year - 1)
    if preset == "Senaste 5 åren":
        return "{}-01-01".format(today.year - 4), today.isoformat()
    if preset == "Senaste 25 åren":
        return "{}-01-01".format(today.year - 24), today.isoformat()

    # Anpassat
    return start_date, end_date


def build_filter(taxon_ids=None, include_underlying=True, only_species=False,
                 redlist_categories=None, taxon_list_ids=None,
                 taxon_list_operator="Merge", only_invasive=False,
                 date_preset="Alla år", start_date=None, end_date=None,
                 date_filter_type="OverlappingStartDateAndEndDate",
                 time_ranges=None, modified_from=None, modified_to=None,
                 geometries=None, areas=None, consider_accuracy=False,
                 outside_sweden=False, max_accuracy=None,
                 occurrence_status="present", not_recovered="NoFilter",
                 determination="NoFilter",
                 verification="BothVerifiedAndNotVerified",
                 bird_nest_limit=None, provider_ids=None):
    """Bygg en SearchFilterDto. Tar bara vanliga pythonvärden, så att den går
    att testa utan geoprocessing."""
    search = {}

    # --- Taxa ---------------------------------------------------------------
    taxon = {}
    if taxon_ids:
        taxon["ids"] = list(taxon_ids)
        taxon["includeUnderlyingTaxa"] = bool(include_underlying)
    if redlist_categories:
        taxon["redListCategories"] = list(redlist_categories)
    if taxon_list_ids:
        taxon["taxonListIds"] = list(taxon_list_ids)
        taxon["taxonListOperator"] = taxon_list_operator or "Merge"
    if only_invasive:
        taxon["isInvasiveInSweden"] = True
    if only_species:
        taxon["taxonCategories"] = [17]       # 17 = Art i vokabuläret TaxonCategory
    if taxon:
        search["taxon"] = taxon

    # --- Tid ----------------------------------------------------------------
    start, end = _date_range(date_preset, start_date, end_date)
    date = {}
    if start:
        date["startDate"] = start
    if end:
        date["endDate"] = end
    if start or end:
        date["dateFilterType"] = date_filter_type
    if time_ranges:
        date["timeRanges"] = list(time_ranges)
    if date:
        search["date"] = date

    # modifiedDate ligger på toppnivå. Under "date" tas den emot men ignoreras.
    if modified_from or modified_to:
        modified = {}
        if modified_from:
            modified["from"] = modified_from
        if modified_to:
            modified["to"] = modified_to
        search["modifiedDate"] = modified

    # --- Geografi -----------------------------------------------------------
    geographics = {}
    if geometries:
        geographics["geometries"] = list(geometries)
    if areas:
        geographics["areas"] = list(areas)
    if max_accuracy:
        geographics["maxAccuracy"] = int(max_accuracy)
    if consider_accuracy:
        geographics["considerObservationAccuracy"] = True
    if outside_sweden:
        geographics["includeObservationsOutsideSweden"] = True
    if geographics:
        search["geographics"] = geographics

    # --- Fyndegenskaper -----------------------------------------------------
    if occurrence_status and occurrence_status != "present":
        search["occurrenceStatus"] = occurrence_status
    if not_recovered and not_recovered != "NoFilter":
        search["notRecoveredFilter"] = not_recovered
    if determination and determination != "NoFilter":
        search["determinationFilter"] = determination
    if verification and verification != "BothVerifiedAndNotVerified":
        search["verificationStatus"] = verification
    if bird_nest_limit:
        search["birdNestActivityLimit"] = int(bird_nest_limit)

    # --- Dataset ------------------------------------------------------------
    if provider_ids:
        search["dataProvider"] = {"ids": list(provider_ids)}

    search["output"] = {"fieldSet": "All"}
    return search


# =============================================================================
# Hämtning
# =============================================================================

def _count(search_filter, api_key):
    url = "{}/Observations/Count".format(API_BASE)
    result = _request(url, api_key, search_filter)
    if isinstance(result, dict):
        return result.get("totalCount") or 0
    return int(result or 0)


def _fetch(search_filter, api_key, max_records, messages):
    """Hämta alla poster via SearchByCursor.

    /Observations/Search klarar bara skip + take <= 50 000. SearchByCursor har
    ingen sådan gräns och sidindelas med ?cursor=<nextCursor> ur föregående svar.
    Frågeparametern heter cursor. Andra namn tas emot utan fel och ger första
    sidan om och om igen.
    """
    observations = []
    cursor = None
    page = 0

    while True:
        url = "{}/Observations/SearchByCursor?take={}".format(API_BASE, TAKE)
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor)

        data = _request(url, api_key, search_filter)
        records = data.get("records") or []
        cursor = data.get("nextCursor")
        page += 1

        observations.extend(records)
        arcpy.SetProgressorLabel(
            "Hämtar observationer: {:,} av {:,}".format(
                len(observations), data.get("totalCount") or 0).replace(",", " "))
        arcpy.SetProgressorPosition(len(observations))

        if max_records and len(observations) >= max_records:
            observations = observations[:max_records]
            break
        if not records or not cursor or len(records) < TAKE:
            break

    _log(messages, "  {} sidor hämtade.".format(page))
    return observations


# =============================================================================
# Featureklass
# =============================================================================

def _coerce(value, field_type):
    if value is None:
        return None
    if field_type == "LONG":
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None
    if field_type == "DOUBLE":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if field_type == "DATE":
        return _to_datetime(value)
    if isinstance(value, bool):
        return "Ja" if value else "Nej"
    text = str(value)
    return text if text != "" else None


def _build_feature_class(out_fc, observations, out_sr, messages):
    """Skapa featureklassen och skriv in observationerna."""
    workspace = os.path.dirname(out_fc)
    name = os.path.basename(out_fc)

    if not workspace or not arcpy.Exists(workspace):
        raise ValueError("Arbetsytan '{}' finns inte.".format(workspace))

    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)

    arcpy.management.CreateFeatureclass(workspace, name, "POINT",
                                        spatial_reference=out_sr)

    is_shapefile = name.lower().endswith(".shp")
    field_names = []
    for _, field, field_type, length, alias in FIELD_MAP:
        if is_shapefile:
            field = field[:10]
            length = min(length, 254) if length else length
        arcpy.management.AddField(out_fc, field, field_type,
                                  field_length=length, field_alias=alias)
        field_names.append(field)

    wgs84 = arcpy.SpatialReference(WGS84)
    same_sr = out_sr.factoryCode == WGS84

    written = 0
    skipped = 0
    arcpy.SetProgressor("step", "Skriver poster...", 0, len(observations), 1)

    with arcpy.da.InsertCursor(out_fc, ["SHAPE@"] + field_names) as cursor:
        for index, observation in enumerate(observations):
            lon = _get_nested(observation, "location.decimalLongitude")
            lat = _get_nested(observation, "location.decimalLatitude")
            if lon is None or lat is None:
                skipped += 1
                continue

            point = arcpy.PointGeometry(arcpy.Point(float(lon), float(lat)), wgs84)
            if not same_sr:
                point = point.projectAs(out_sr)

            row = [point]
            for path, _, field_type, _, _ in FIELD_MAP:
                row.append(_coerce(_get_nested(observation, path), field_type))
            cursor.insertRow(row)
            written += 1

            if index % 500 == 0:
                arcpy.SetProgressorPosition(index)

    arcpy.SetProgressorPosition(len(observations))

    if skipped:
        _warn(messages,
              "{} observationer saknade koordinater och hoppades över.".format(skipped))
    return written


# =============================================================================
# Körning
# =============================================================================
# Parameterindex. FIRST_SAVED..LAST_SAVED är det som en förinställning omfattar.
# Utdata och API-nyckel ligger utanför med flit.

P_OUT, P_KEY = 0, 1
P_PRESET, P_SAVE_PRESET, P_DELETE_PRESET = 2, 3, 4
P_TAXON_IDS, P_UNDERLYING, P_ONLY_SPECIES = 5, 6, 7
P_REDLIST, P_TAXON_LISTS, P_LIST_OPERATOR, P_INVASIVE = 8, 9, 10, 11
P_DATE_PRESET, P_START, P_END, P_DATE_TYPE = 12, 13, 14, 15
P_TIME_RANGES, P_MODIFIED_FROM, P_MODIFIED_TO = 16, 17, 18
P_AOI, P_AREA_TYPE, P_AREA_NAMES = 19, 20, 21
P_CONSIDER_ACC, P_OUTSIDE_SWEDEN = 22, 23
P_MAX_ACC, P_OCCURRENCE, P_NOT_RECOVERED = 24, 25, 26
P_DETERMINATION, P_VERIFICATION, P_BIRD_NEST = 27, 28, 29
P_PROVIDERS = 30
P_OUT_SR, P_MAX_RECORDS = 31, 32

FIRST_SAVED, LAST_SAVED = P_TAXON_IDS, P_MAX_RECORDS


def _parse_taxon_ids(text):
    """Taxon-id ur en fritextruta. Komma, semikolon, blanksteg och radbrytning
    fungerar alla som avgränsare, så att en urklippt lista kan klistras in."""
    if not text:
        return []
    cleaned = text.replace(",", " ").replace(";", " ").replace("\n", " ").replace("\t", " ")
    ids = []
    bad = []
    for token in cleaned.split():
        try:
            ids.append(int(token))
        except ValueError:
            bad.append(token)
    if bad:
        raise ValueError(
            "Dessa taxon-id kunde inte tolkas som heltal: {}".format(", ".join(bad[:10]))
        )
    return ids


def _collect(parameters):
    """Läs dialogen och returnera argumenten till build_filter."""
    area_type = parameters[P_AREA_TYPE].valueAsText
    area_code = _code_for(AREA_TYPES, area_type) if area_type else None
    areas = []
    if area_code:
        lookup = _area_labels(_load_areas(area_code))
        missing = []
        for name in _clean_multi(parameters[P_AREA_NAMES]):
            feature_id = lookup.get(name)
            if feature_id is None:
                missing.append(name)
            else:
                areas.append({"areaType": area_code, "featureId": feature_id})
        if missing:
            raise ValueError(
                "Dessa områden känns inte igen för områdestypen {}: {}. "
                "Kör verktyget Uppdatera referenslistor om listan är gammal."
                .format(area_type, ", ".join(missing[:5]))
            )

    bird_label = parameters[P_BIRD_NEST].valueAsText
    bird_limit = None
    if bird_label:
        for number, text in BIRD_NEST:
            if text == bird_label:
                bird_limit = number
                break

    return dict(
        taxon_ids=_parse_taxon_ids(parameters[P_TAXON_IDS].valueAsText),
        include_underlying=bool(parameters[P_UNDERLYING].value),
        only_species=bool(parameters[P_ONLY_SPECIES].value),
        redlist_categories=_codes_for(REDLIST, _clean_multi(parameters[P_REDLIST])),
        taxon_list_ids=_codes_for(TAXON_LISTS, _clean_multi(parameters[P_TAXON_LISTS])),
        taxon_list_operator=_code_for(TAXON_LIST_OPERATOR,
                                      parameters[P_LIST_OPERATOR].valueAsText) or "Merge",
        only_invasive=bool(parameters[P_INVASIVE].value),
        date_preset=parameters[P_DATE_PRESET].valueAsText or "Alla år",
        start_date=_parse_date(parameters[P_START].value),
        end_date=_parse_date(parameters[P_END].value),
        date_filter_type=_code_for(DATE_FILTER_TYPES, parameters[P_DATE_TYPE].valueAsText)
                         or "OverlappingStartDateAndEndDate",
        time_ranges=_codes_for(TIME_RANGES, _clean_multi(parameters[P_TIME_RANGES])),
        modified_from=_parse_date(parameters[P_MODIFIED_FROM].value),
        modified_to=_parse_date(parameters[P_MODIFIED_TO].value),
        areas=areas,
        consider_accuracy=bool(parameters[P_CONSIDER_ACC].value),
        outside_sweden=bool(parameters[P_OUTSIDE_SWEDEN].value),
        max_accuracy=parameters[P_MAX_ACC].value,
        occurrence_status=_code_for(OCCURRENCE_STATUS,
                                    parameters[P_OCCURRENCE].valueAsText) or "present",
        not_recovered=_code_for(NOT_RECOVERED,
                                parameters[P_NOT_RECOVERED].valueAsText) or "NoFilter",
        determination=_code_for(DETERMINATION,
                                parameters[P_DETERMINATION].valueAsText) or "NoFilter",
        verification=_code_for(VERIFICATION, parameters[P_VERIFICATION].valueAsText)
                     or "BothVerifiedAndNotVerified",
        bird_nest_limit=bird_limit,
        provider_ids=_codes_for(PROVIDERS, _clean_multi(parameters[P_PROVIDERS])),
    )


def _describe_filter(search_filter):
    """Sökfiltret som läsbara rader i loggen. Gör det synligt vad som faktiskt
    skickades, eftersom API:et tyst struntar i det det inte känner igen."""
    lines = []
    taxon = search_filter.get("taxon") or {}
    if taxon.get("ids"):
        lines.append("  Taxon-id: {} st (underliggande taxa: {})".format(
            len(taxon["ids"]), "ja" if taxon.get("includeUnderlyingTaxa") else "nej"))
    if taxon.get("redListCategories"):
        lines.append("  Rödlistekategorier: " + ", ".join(taxon["redListCategories"]))
    if taxon.get("taxonListIds"):
        names = {i: n for i, n in TAXON_LISTS}
        lines.append("  Artlistor ({}): {}".format(
            taxon.get("taxonListOperator", "Merge"),
            ", ".join(names.get(i, str(i)) for i in taxon["taxonListIds"])))
    if taxon.get("taxonCategories"):
        lines.append("  Endast arter")
    if taxon.get("isInvasiveInSweden"):
        lines.append("  Endast främmande arter i Sverige")

    date = search_filter.get("date") or {}
    if date.get("startDate") or date.get("endDate"):
        lines.append("  Period: {} till {} ({})".format(
            date.get("startDate", "-"), date.get("endDate", "-"),
            date.get("dateFilterType")))
    if date.get("timeRanges"):
        lines.append("  Tid på dygnet: " + ", ".join(date["timeRanges"]))
    if search_filter.get("modifiedDate"):
        modified = search_filter["modifiedDate"]
        lines.append("  Registrerad/ändrad: {} till {}".format(
            modified.get("from", "-"), modified.get("to", "-")))

    geographics = search_filter.get("geographics") or {}
    if geographics.get("geometries"):
        lines.append("  Ritat sökområde: {} polygon(er)".format(
            len(geographics["geometries"])))
    if geographics.get("areas"):
        lines.append("  Områden: {} st".format(len(geographics["areas"])))
    if geographics.get("maxAccuracy"):
        lines.append("  Max koordinatnoggrannhet: {} m".format(
            geographics["maxAccuracy"]))
    if geographics.get("considerObservationAccuracy"):
        lines.append("  Tar hänsyn till fyndets noggrannhet mot området")
    if geographics.get("includeObservationsOutsideSweden"):
        lines.append("  Inkluderar fynd utanför Sverige")

    for key, label in (("occurrenceStatus", "Förekomst"),
                       ("notRecoveredFilter", "Ej återfunna"),
                       ("determinationFilter", "Artbestämning"),
                       ("verificationStatus", "Verifiering"),
                       ("birdNestActivityLimit", "Lägsta häckningskriterium")):
        if key in search_filter:
            lines.append("  {}: {}".format(label, search_filter[key]))

    if search_filter.get("dataProvider"):
        names = {i: n for i, n in PROVIDERS}
        ids = search_filter["dataProvider"]["ids"]
        lines.append("  Dataset: " + ", ".join(names.get(i, str(i)) for i in ids))

    return lines or ["  (inga filter, hela databasen)"]


def _run(parameters, messages):
    """Hela körningen. Skild från execute() så att den går att driva direkt."""
    api_key = (parameters[P_KEY].valueAsText or "").strip()
    out_fc = parameters[P_OUT].valueAsText
    max_records = parameters[P_MAX_RECORDS].value or 0
    out_sr = _spatial_reference(parameters[P_OUT_SR])

    # Förinställningar sköts innan sökningen, så att de sparas även om
    # sökningen sedan inte ger några träffar.
    if parameters[P_SAVE_PRESET].valueAsText:
        path = _save_preset(parameters[P_SAVE_PRESET].valueAsText.strip(),
                            parameters, FIRST_SAVED, LAST_SAVED)
        _log(messages, "Förinställning sparad: {}".format(path))
    if parameters[P_DELETE_PRESET].value and parameters[P_PRESET].valueAsText:
        if _delete_preset(parameters[P_PRESET].valueAsText):
            _log(messages, "Förinställning borttagen: {}".format(
                parameters[P_PRESET].valueAsText))

    arguments = _collect(parameters)
    arguments["geometries"] = _aoi_geometries(parameters[P_AOI].value, messages)
    search_filter = build_filter(**arguments)

    _log(messages, "Sökfilter:")
    for line in _describe_filter(search_filter):
        _log(messages, line)

    arcpy.SetProgressorLabel("Räknar träffar...")
    total = _count(search_filter, api_key)
    _log(messages, "Antal träffar i SOS: {:,}".format(total).replace(",", " "))

    if total == 0:
        _log(messages, "Sökningen gav inga träffar. Ingen featureklass skapades.")
        return 0

    if max_records and total > max_records:
        _warn(messages,
              "Träffarna ({:,}) överstiger max antal poster ({:,}). "
              "Resultatet blir avkortat.".format(total, max_records).replace(",", " "))

    arcpy.SetProgressor("step", "Hämtar observationer...", 0,
                        min(total, max_records) if max_records else total, TAKE)
    observations = _fetch(search_filter, api_key, max_records, messages)
    _log(messages, "{} observationer hämtade.".format(len(observations)))

    _log(messages, "Skapar featureklass: {}".format(out_fc))
    written = _build_feature_class(out_fc, observations, out_sr, messages)
    _log(messages, "{} punkter skrivna i {}.".format(
        written, out_sr.name if out_sr.name else "valt koordinatsystem"))
    return written


# =============================================================================
# Toolbox
# =============================================================================

class Toolbox:
    def __init__(self):
        self.label = "Sök i Fynddata"
        self.alias = "fynddata"
        self.tools = [SokIFynddata, UppdateraReferenslistor]


class SokIFynddata:

    def __init__(self):
        self.label = "Sök i Fynddata"
        self.description = (
            "Söker i SLU Artdatabankens Species Observation System och skriver "
            "resultatet till en punkt-featureklass. Filtren följer webb"
            "applikationen Fynddata: Taxa, Tid, Geografi, Fyndegenskaper och "
            "Dataset. Inställningar kan sparas som förinställningar och "
            "återanvändas."
        )
        self.canRunInBackground = False
        self._preset_memo = None
        self._area_memo = None

    # -- parametrar ----------------------------------------------------------

    def getParameterInfo(self):
        def parameter(name, label, datatype, kind="Optional", category=None,
                      multi=False, direction="Input"):
            item = arcpy.Parameter(displayName=label, name=name, datatype=datatype,
                                   parameterType=kind, direction=direction,
                                   multiValue=multi)
            if category:
                item.category = category
            return item

        out_fc = parameter("out_fc", "Utdata featureklass", "DEFeatureClass",
                           "Required", direction="Output")
        api_key = parameter("api_key", "API-nyckel (Ocp-Apim-Subscription-Key)",
                            "GPString", "Required")

        # -- Förinställningar --
        cat = "Förinställningar"
        preset = parameter("preset", "Använd förinställning", "GPString", category=cat)
        preset.filter.type = "ValueList"
        preset.filter.list = _list_presets()
        save_preset = parameter("save_preset", "Spara inställningarna som",
                                "GPString", category=cat)
        delete_preset = parameter("delete_preset", "Ta bort vald förinställning",
                                  "GPBoolean", category=cat)
        delete_preset.value = False

        # -- 1. Taxa --
        cat = "1. Taxa"
        taxon_ids = parameter("taxon_ids", "Taxon-id (ett eller flera)",
                              "GPString", category=cat)
        underlying = parameter("include_underlying", "Inkludera underliggande taxa",
                               "GPBoolean", category=cat)
        underlying.value = True
        only_species = parameter("only_species", "Inkludera endast arter",
                                 "GPBoolean", category=cat)
        only_species.value = False
        redlist = parameter("redlist_categories", "Rödlistekategorier", "GPString",
                            category=cat, multi=True)
        redlist.filter.type = "ValueList"
        redlist.filter.list = _labels(REDLIST)
        lists = parameter("taxon_lists", "Artlistor", "GPString",
                          category=cat, multi=True)
        lists.filter.type = "ValueList"
        lists.filter.list = _labels(TAXON_LISTS)
        operator = parameter("taxon_list_operator", "Artlistornas roll",
                             "GPString", category=cat)
        operator.filter.type = "ValueList"
        operator.filter.list = _labels(TAXON_LIST_OPERATOR)
        operator.value = _labels(TAXON_LIST_OPERATOR)[0]
        invasive = parameter("only_invasive", "Endast främmande arter i Sverige",
                             "GPBoolean", category=cat)
        invasive.value = False

        # -- 2. Tid --
        cat = "2. Tid"
        date_preset = parameter("date_preset", "Tidsperiod", "GPString", category=cat)
        date_preset.filter.type = "ValueList"
        date_preset.filter.list = DATE_PRESETS
        date_preset.value = DATE_PRESETS[0]
        start = parameter("start_date", "Från", "GPDate", category=cat)
        end = parameter("end_date", "Till", "GPDate", category=cat)
        date_type = parameter("date_filter_type", "Hur perioden jämförs",
                              "GPString", category=cat)
        date_type.filter.type = "ValueList"
        date_type.filter.list = _labels(DATE_FILTER_TYPES)
        date_type.value = _labels(DATE_FILTER_TYPES)[0]
        time_ranges = parameter("time_ranges", "Tid på dygnet", "GPString",
                                category=cat, multi=True)
        time_ranges.filter.type = "ValueList"
        time_ranges.filter.list = _labels(TIME_RANGES)
        modified_from = parameter("modified_from", "Registrerad/ändrad från",
                                  "GPDate", category=cat)
        modified_to = parameter("modified_to", "Registrerad/ändrad till",
                                "GPDate", category=cat)

        # -- 3. Geografi --
        cat = "3. Geografi"
        aoi = parameter("aoi", "Sökområde (rita polygon i kartan)",
                        "GPFeatureRecordSetLayer", category=cat)
        aoi.filter.list = ["Polygon"]
        area_type = parameter("area_type", "Områdestyp", "GPString", category=cat)
        area_type.filter.type = "ValueList"
        area_type.filter.list = _labels(AREA_TYPES)
        area_names = parameter("area_names", "Områden", "GPString",
                               category=cat, multi=True)
        area_names.filter.type = "ValueList"
        area_names.filter.list = []
        consider_acc = parameter("consider_accuracy",
                                 "Ta hänsyn till fyndets noggrannhet mot området",
                                 "GPBoolean", category=cat)
        consider_acc.value = False
        outside = parameter("outside_sweden", "Inkludera fynd utanför Sverige",
                            "GPBoolean", category=cat)
        outside.value = False

        # -- 4. Fyndegenskaper --
        cat = "4. Fyndegenskaper"
        max_acc = parameter("max_accuracy", "Max koordinatnoggrannhet (m)",
                            "GPLong", category=cat)
        occurrence = parameter("occurrence_status", "Förekomst", "GPString",
                               category=cat)
        occurrence.filter.type = "ValueList"
        occurrence.filter.list = _labels(OCCURRENCE_STATUS)
        occurrence.value = _labels(OCCURRENCE_STATUS)[0]
        not_recovered = parameter("not_recovered", "Ej återfunna fynd", "GPString",
                                  category=cat)
        not_recovered.filter.type = "ValueList"
        not_recovered.filter.list = _labels(NOT_RECOVERED)
        not_recovered.value = _labels(NOT_RECOVERED)[0]
        determination = parameter("determination", "Artbestämning", "GPString",
                                  category=cat)
        determination.filter.type = "ValueList"
        determination.filter.list = _labels(DETERMINATION)
        determination.value = _labels(DETERMINATION)[0]
        verification = parameter("verification", "Verifieringsstatus", "GPString",
                                 category=cat)
        verification.filter.type = "ValueList"
        verification.filter.list = _labels(VERIFICATION)
        verification.value = _labels(VERIFICATION)[0]
        bird_nest = parameter("bird_nest", "Lägsta häckningskriterium (fåglar)",
                              "GPString", category=cat)
        bird_nest.filter.type = "ValueList"
        bird_nest.filter.list = [text for _, text in BIRD_NEST]

        # -- 5. Dataset --
        cat = "5. Dataset"
        providers = parameter("providers", "Dataset", "GPString",
                              category=cat, multi=True)
        providers.filter.type = "ValueList"
        providers.filter.list = _labels(PROVIDERS)

        # -- 6. Utdata --
        cat = "6. Utdata"
        out_sr = parameter("out_sr", "Koordinatsystem", "GPCoordinateSystem",
                           category=cat)
        out_sr.value = arcpy.SpatialReference(SWEREF99TM).exportToString()
        max_records = parameter("max_records", "Max antal poster", "GPLong",
                                category=cat)

        return [out_fc, api_key,
                preset, save_preset, delete_preset,
                taxon_ids, underlying, only_species, redlist, lists, operator, invasive,
                date_preset, start, end, date_type, time_ranges,
                modified_from, modified_to,
                aoi, area_type, area_names, consider_acc, outside,
                max_acc, occurrence, not_recovered, determination, verification,
                bird_nest,
                providers,
                out_sr, max_records]

    def isLicensed(self):
        return True

    # -- dialoglogik ---------------------------------------------------------

    def updateParameters(self, parameters):
        # Håll listan över förinställningar aktuell utan att röra valet.
        presets = _list_presets()
        if parameters[P_PRESET].filter.list != presets:
            parameters[P_PRESET].filter.list = presets

        # Läs in en förinställning först när valet ändras, annars skrivs
        # användarens egna ändringar över vid varje tangenttryckning.
        chosen = parameters[P_PRESET].valueAsText
        if chosen and chosen != self._preset_memo:
            self._preset_memo = chosen
            _apply_preset(chosen, parameters, FIRST_SAVED, LAST_SAVED)
        elif not chosen:
            self._preset_memo = None

        # Områdeslistan byts när områdestypen byts. Läses från disk, aldrig
        # över nätet: updateParameters körs vid varje tangenttryckning.
        area_type = parameters[P_AREA_TYPE].valueAsText
        if area_type != self._area_memo:
            self._area_memo = area_type
            code = _code_for(AREA_TYPES, area_type) if area_type else None
            names = sorted(_area_labels(_load_areas(code))) if code else []
            parameters[P_AREA_NAMES].filter.list = names
            # Behåll de valda områdena som finns kvar i den nya listan. Att
            # blint nolla valet här tömde parametern när verktyget anropas från
            # ett skript, där områdestyp och områden sätts i samma anrop.
            chosen = _clean_multi(parameters[P_AREA_NAMES])
            keep = [name for name in chosen if name in names]
            if keep != chosen:
                parameters[P_AREA_NAMES].value = ";".join(keep) if keep else None

        # Från- och tilldatum är bara meningsfulla för Anpassat.
        custom = parameters[P_DATE_PRESET].valueAsText == "Anpassat"
        parameters[P_START].enabled = custom
        parameters[P_END].enabled = custom
        return

    def updateMessages(self, parameters):
        if parameters[P_DATE_PRESET].valueAsText == "Anpassat":
            if not parameters[P_START].value and not parameters[P_END].value:
                parameters[P_DATE_PRESET].setErrorMessage(
                    "Ange Från och/eller Till när tidsperioden är Anpassat.")

        start = _parse_date(parameters[P_START].value)
        end = _parse_date(parameters[P_END].value)
        if start and end and start > end:
            parameters[P_END].setErrorMessage("Till-datumet ligger före Från-datumet.")

        modified_from = _parse_date(parameters[P_MODIFIED_FROM].value)
        modified_to = _parse_date(parameters[P_MODIFIED_TO].value)
        if modified_from and modified_to and modified_from > modified_to:
            parameters[P_MODIFIED_TO].setErrorMessage(
                "Till-datumet ligger före Från-datumet.")

        if parameters[P_MAX_ACC].value is not None and parameters[P_MAX_ACC].value < 0:
            parameters[P_MAX_ACC].setErrorMessage(
                "Koordinatnoggrannheten kan inte vara negativ.")

        if parameters[P_MAX_RECORDS].value is not None and parameters[P_MAX_RECORDS].value < 1:
            parameters[P_MAX_RECORDS].setErrorMessage(
                "Max antal poster måste vara minst 1.")

        if parameters[P_TAXON_IDS].valueAsText:
            try:
                _parse_taxon_ids(parameters[P_TAXON_IDS].valueAsText)
            except ValueError as exc:
                parameters[P_TAXON_IDS].setErrorMessage(str(exc))

        if parameters[P_AREA_NAMES].value and not parameters[P_AREA_TYPE].valueAsText:
            parameters[P_AREA_NAMES].setErrorMessage(
                "Välj en områdestyp först.")

        if parameters[P_AREA_TYPE].valueAsText and not parameters[P_AREA_NAMES].filter.list:
            parameters[P_AREA_TYPE].setWarningMessage(
                "Det finns ingen nedladdad lista för den här områdestypen. "
                "Kör verktyget Uppdatera referenslistor först.")

        if parameters[P_DELETE_PRESET].value and not parameters[P_PRESET].valueAsText:
            parameters[P_DELETE_PRESET].setErrorMessage(
                "Välj vilken förinställning som ska tas bort.")

        # En sökning helt utan avgränsning laddar ner hela databasen.
        taxon_filter = any([
            parameters[P_TAXON_IDS].valueAsText,
            parameters[P_REDLIST].value,
            parameters[P_TAXON_LISTS].value,
            parameters[P_INVASIVE].value,
        ])
        geo_filter = any([
            parameters[P_AOI].value,
            parameters[P_AREA_NAMES].value,
        ])
        if not taxon_filter and not geo_filter:
            parameters[P_OUT].setWarningMessage(
                "Sökningen saknar både art- och områdesavgränsning och kommer "
                "att matcha hela databasen. Lägg till ett filter eller sätt "
                "Max antal poster.")
        return

    # -- körning -------------------------------------------------------------

    def execute(self, parameters, messages):
        saved_overwrite = arcpy.env.overwriteOutput
        saved_sr = arcpy.env.outputCoordinateSystem
        arcpy.env.overwriteOutput = True
        try:
            _run(parameters, messages)
        except ValueError as exc:
            messages.addErrorMessage(str(exc))
            raise arcpy.ExecuteError
        finally:
            arcpy.env.overwriteOutput = saved_overwrite
            arcpy.env.outputCoordinateSystem = saved_sr
            arcpy.ResetProgressor()

    def postExecute(self, parameters):
        return


# =============================================================================
# Uppdatera referenslistor
# =============================================================================

class UppdateraReferenslistor:

    def __init__(self):
        self.label = "Uppdatera referenslistor"
        self.description = (
            "Hämtar områdesnamn från SOS API och sparar dem lokalt, så att "
            "områdesväljaren i Sök i Fynddata fungerar utan nätanrop. Län, "
            "kommun och provins är inbakade i verktyget och behöver bara "
            "uppdateras om de har ändrats. Övriga områdestyper måste hämtas "
            "en gång innan de går att använda."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        api_key = arcpy.Parameter(
            displayName="API-nyckel (Ocp-Apim-Subscription-Key)",
            name="api_key", datatype="GPString", parameterType="Required",
            direction="Input")

        area_types = arcpy.Parameter(
            displayName="Områdestyper att hämta", name="area_types",
            datatype="GPString", parameterType="Required", direction="Input",
            multiValue=True)
        area_types.filter.type = "ValueList"
        area_types.filter.list = _labels(AREA_TYPES)
        area_types.value = ";".join(["Län", "Kommun", "Provins", "Socken"])

        return [api_key, area_types]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        api_key = (parameters[0].valueAsText or "").strip()
        labels = _clean_multi(parameters[1])
        try:
            arcpy.SetProgressor("step", "Hämtar områden...", 0, len(labels), 1)
            for index, label in enumerate(labels):
                code = _code_for(AREA_TYPES, label)
                if not code:
                    continue
                arcpy.SetProgressorLabel("Hämtar {}...".format(label))
                count = _download_areas(code, api_key)
                messages.addMessage("{}: {} områden sparade.".format(label, count))
                arcpy.SetProgressorPosition(index + 1)
            messages.addMessage("Listorna ligger i {}".format(CACHE_DIR))
        except ValueError as exc:
            messages.addErrorMessage(str(exc))
            raise arcpy.ExecuteError
        finally:
            arcpy.ResetProgressor()

    def postExecute(self, parameters):
        return
