# Sök i Fynddata

ArcGIS Pro-verktygslåda som söker i SLU Artdatabankens Species Observation System (SOS) och
skriver resultatet till en punkt-featureklass. Sökfiltren följer webbapplikationen
[Fynddata](https://fynddata.artdatabanken.se) och är grupperade som dess flikar: Taxa, Tid,
Geografi, Fyndegenskaper och Dataset.

## Krav

- ArcGIS Pro 3.x. Utvecklad och testad mot 3.6 med Python 3.13.
- Inga andra bibliotek än `arcpy` och standardbiblioteket.
- En API-nyckel till Species Observation System, hämtas på
  [api-portal.artdatabanken.se](https://api-portal.artdatabanken.se/).

## Lägga till i ArcGIS Pro

Katalogfönstret, högerklicka på Toolboxes, Add Toolbox, och peka ut `SokIFynddata.pyt`.

## Verktyg

### Sök i Fynddata

Kör sökningen och skapar featureklassen. Parametrarna är grupperade i kategorier.

| Kategori | Parameter | Kommentar |
| --- | --- | --- |
| | Utdata featureklass | Punktlager, skrivs över om det finns |
| | API-nyckel | Ocp-Apim-Subscription-Key |
| Förinställningar | Använd förinställning | Läser in sparade inställningar |
| Förinställningar | Spara inställningarna som | Sparar allt utom utdata och API-nyckel |
| Förinställningar | Ta bort vald förinställning | |
| 1. Taxa | Taxon-id | Ett eller flera, avgränsade med komma, semikolon, blanksteg eller radbrytning |
| 1. Taxa | Inkludera underliggande taxa | |
| 1. Taxa | Inkludera endast arter | |
| 1. Taxa | Rödlistekategorier | RE, CR, EN, VU, NT, DD, LC, NA, NE |
| 1. Taxa | Artlistor | Fridlysta, signalarter, främmande arter, risklista, direktivbilagor, ÅGP, naturvårdsarter |
| 1. Taxa | Artlistornas roll | Komplettera urvalet (Merge) eller begränsa det (Filter) |
| 1. Taxa | Endast främmande arter i Sverige | |
| 2. Tid | Tidsperiod | Alla år, innevarande, föregående, senaste 5, senaste 25, anpassat |
| 2. Tid | Från och Till | Aktiva när tidsperioden är Anpassat |
| 2. Tid | Hur perioden jämförs | Överlappande (periodfynd), helt inom, endast start-, endast slutdatum |
| 2. Tid | Tid på dygnet | Morgon, förmiddag, eftermiddag, kväll |
| 2. Tid | Registrerad/ändrad från och till | Filtrerar på när fyndet registrerades, inte när det observerades |
| 3. Geografi | Avgränsa området med | Ingen polygon eller utbredning (förval), Polygoner eller Utbredning |
| 3. Geografi | Polygoner | Polygonlager (urval respekteras) eller polygoner ritade i kartan. Aktiv vid Polygoner |
| 3. Geografi | Utbredning | Kartvyns utbredning, ett lagers utbredning, ritad rektangel eller koordinater. Aktiv vid Utbredning |
| 3. Geografi | Områdestyp och Områden | Län, kommun, provins, socken, vattenområde med flera |
| 3. Geografi | Ta hänsyn till fyndets noggrannhet | Tar med fynd vars osäkerhetsradie når in i området |
| 3. Geografi | Inkludera fynd utanför Sverige | |
| 4. Fyndegenskaper | Max koordinatnoggrannhet (m) | |
| 4. Fyndegenskaper | Förekomst | Observerad, ej observerad, eller båda |
| 4. Fyndegenskaper | Ej återfunna fynd | |
| 4. Fyndegenskaper | Artbestämning | Endast säker eller endast osäker |
| 4. Fyndegenskaper | Verifieringsstatus | |
| 4. Fyndegenskaper | Lägsta häckningskriterium | Endast fåglar i Artportalen. Tar med valt kriterium och alla säkrare |
| 5. Dataset | Dataset | 26 valbara källor, förvalt är alla |
| 6. Utdata | Koordinatsystem | Tomt ger SWEREF99TM |
| 6. Utdata | Max antal poster | Tomt ger alla träffar |

Geografin är frivillig. Polygoner eller utbredning kan kombineras med områdesval, och då måste
ett fynd ligga i båda. Utan någon avgränsning alls matchar sökningen hela databasen, och
verktyget varnar för det i dialogen.

Polygoner och utbredning skickas till SOS som polygoner i WGS84. Hål i polygoner följer med som
hål. En utbredning förtätas till 16 punkter per sida innan den projiceras, så att rektangeln
behåller sin form. Inskrivna koordinater har inget eget koordinatsystem och tolkas i den aktiva
kartans system, eller i SWEREF 99 TM när verktyget körs utan karta. Kommer utbredningen från ett
lager används lagrets system. Vilket som användes, och sökområdets hörn och omslutande rektangel
i WGS84, skrivs i meddelandena. Ett polygonlager utan koordinatsystem avbryter körningen i
stället för att söka på fel plats.

Från skript accepteras även den äldre etiketten `Polygoner i ett lager` för Polygoner.

### Uppdatera referenslistor

Hämtar områdesnamn från API:et och sparar dem lokalt under
`%LOCALAPPDATA%\ArcGIS Fynddata\cache`. Län, kommun och provins ligger inbakade i verktyget
och fungerar direkt. Övriga områdestyper, till exempel socken och vattenområde, måste hämtas
en gång innan de går att välja. Verktyget varnar i dialogen när en lista saknas.

## Förinställningar

En förinställning är en JSON-fil i `%LOCALAPPDATA%\ArcGIS Fynddata\presets`. Den omfattar alla
sökparametrar men aldrig utdata eller API-nyckeln. Filerna går att kopiera mellan datorer.

Ritade polygoner och valt polygonlager sparas inte. Valet av avgränsning och en utbredning
sparas, utbredningen med sitt koordinatsystem.

## Om datakällan

- Verktyget använder `POST /Observations/Count` för antalet träffar och
  `POST /Observations/SearchByCursor` för att hämta dem. Cursor-varianten har ingen övre gräns,
  till skillnad från `/Observations/Search` som bara klarar `skip + take <= 50 000`.
- **Skyddade fynd kommer inte med.** De kräver personlig inloggning med behörighet i Artportalen.
  Med enbart en API-nyckel returneras det publika urvalet. Fynd med diffuserad koordinat märks
  i kolumnen `diffuserad`.
- Följande filter i Fynddata saknas här, för att de ligger i SOS interna API och inte går att nå
  med en API-nyckel: aktiviteter, ospontan, IAS-åtgärder och sök på period över flera år.
  Häckningskriterier för fåglar finns däremot, som Lägsta häckningskriterium.
- Områdesnamn är inte unika. 187 socknar och 857 skyddade naturområden delar namn med minst ett
  annat område. Sådana namn visas med sitt featureId inom parentes, till exempel
  `Bälinge (212)` och `Bälinge (3071)`, som ligger i Uppsala respektive Västra Götaland.

## Utdata

Featureklassen får 57 attributfält, bland annat taxonuppgifter, rödlistekategori, datum,
koordinater i både SWEREF99TM och WGS84, administrativ tillhörighet, observatör, verifierings
status, direktivbilagor och länk till fyndet. Punkterna byggs från fyndets WGS84-koordinat och
projiceras till det valda koordinatsystemet. Fynd utan koordinat hoppas över, och antalet
rapporteras som en varning.

## Licens

Data från SOS omfattas av SLU Artdatabankens villkor. Se
[artfakta.se/metadata/dataset-artobservationer](https://artfakta.se/metadata/dataset-artobservationer).
