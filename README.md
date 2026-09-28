# Skredmonitor for Vestland

Samler skredsaker fra lokale og nasjonale medier, pluss jordskredvarsler fra NVE/Varsom, på én nettside.
Inndelt i Sogn og Fjordane, Hordaland og Alle. Oppdateres automatisk hvert 15. minutt, gratis.

## Slik setter du det opp (ca. 10 minutter)

1. Lag en konto på github.com hvis du ikke har en.
2. Opprett et nytt **offentlig** repository (f.eks. `skredmonitor`). Offentlig gir ubegrenset gratis kjøretid.
3. Last opp alle filene i denne mappen, inkludert mappene `docs` og `.github`.
   - Enklest: «Add file → Upload files» på repo-siden. Mappen `.github` er skjult på noen systemer,
     så sjekk at `.github/workflows/oppdater.yml` faktisk ligger i repoet.
4. Gå til **Settings → Actions → General → Workflow permissions** og velg **Read and write permissions**.
5. Gå til **Actions**-fanen, velg «Oppdater skredsaker» og trykk **Run workflow**. Sjekk at kjøringen blir grønn.
6. Gå til **Settings → Pages**. Velg «Deploy from a branch», branch `main`, mappe `/docs`. Lagre.
7. Etter et par minutter ligger siden på `https://<brukernavn>.github.io/skredmonitor/`.
8. Åpne adressen i Chrome på Android og velg **⋮ → Legg til på startskjermen** for å få en app-lignende snarvei.

## Tilpasning

Alt ligger i `config.json`:
- `local_sources`: lokalaviser. Fjern eller legg til linjer. Domenene er skrevet fra hukommelsen og
  noen kan være feil, så sjekk i Actions-loggen om en kilde aldri gir treff.
- `sf_queries` / `ho_queries`: kommunenavn det søkes på.
- `keep_days`: hvor lenge saker beholdes (standard 21 dager).

Du kan også skjule enkeltkilder direkte på nettsiden (åpne «Kilder»). Valget lagres i nettleseren.

## Prøv lokalt

```
python3 fetch.py
cd docs && python3 -m http.server 8000
```

## Kjente begrensninger

- Mediesakene hentes via Google Nyheter. Lenkene går via Google og videresender til artikkelen.
  Betalingsmurer i lokalavisene gjelder som vanlig.
- Filteret ser bare på tittelen. Noen saker uten ordet skred/ras i tittelen fanges ikke.
- GitHub kjører timeplanlagte jobber med noen minutters forsinkelse, og kan hoppe over enkelte kjøringer.
- GitHub slår av planlagte kjøringer hvis repoet er helt uten aktivitet i 60 dager.
  Så lenge det kommer nye saker, committer jobben selv og holder det våkent.
- NVE-delen dekker jordskred (og flom-relatert skredfare). Snøskredvarsler er ikke med ennå.
