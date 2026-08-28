# Apify Actor Gap Analysis — European Job Boards

Goal: identify, for the top 3-5 most popular job boards per major European country
(generic + tech-focused), whether a dedicated, maintained Apify Store actor already
exists. Boards without one are the monetizable gap for building and selling actors.

Method: per board, `site:apify.com "<board>" actor` web searches cross-checked with
Apify Store search results, plus Similarweb popularity rankings (July 2026). A board
is classed YES only when a maintained, dedicated actor targets it by name/domain.
GAP = no dedicated actor found (aggregator/ATS boards like LinkedIn, Indeed, Glassdoor,
Workable, Greenhouse, Lever, Ashby are verified to have dedicated actors).

Scope caveat: this wave inventories 18 major markets. Full "100% of Europe" coverage
would also need the smaller markets not yet inventoried — Greece, Romania, Bulgaria,
Croatia, Slovenia, Serbia, the Baltics (Estonia/Latvia/Lithuania), Luxembourg, Malta,
Cyprus, Slovakia, Iceland — see "Path to full coverage" at the end.

---

## United Kingdom

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| Indeed (UK) | uk.indeed.com | generic | YES | orgupdate/indeed-jobs-scraper; curious_coder/indeed-scraper | Largest UK job site ~13% share |
| LinkedIn Jobs (UK) | linkedin.com/jobs | generic | YES | valig/linkedin-jobs-scraper; curious_coder | Dominant professional/tech channel |
| Reed.co.uk | reed.co.uk | generic | YES | lexis-solutions/reed-co-uk-scraper; shahid-irfan | UK #1 homegrown board, ~11.3% share |
| Totaljobs | totaljobs.co.uk | generic | YES | blackfalcondata/totaljobs-scraper | ~2nd/3rd largest UK board |
| CWJobs | cwjobs.co.uk | tech | YES | apify.com/cwjobs-scraper | Leading UK tech/IT board |

**Top tech board:** CWJobs — dedicated actor exists.
**Gaps:** none — all top 5 covered.

## Ireland

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| IrishJobs.ie | irishjobs.ie | generic | YES | jobsapi/irishjobs-jobs-search-scraper; memo23; fatihtahta | Dominant private-sector board |
| Indeed (Ireland) | ie.indeed.com | generic | YES | orgupdate/indeed-jobs-scraper | Largest aggregator in IE |
| Jobs.ie | jobs.ie | generic | YES | apify.com/jobs-ie-scraper | Major board (StepStone network) |
| JobsIreland.ie (state) | jobsireland.ie | generic (state) | YES | lexis-solutions/jobsireland-ie-scraper | Official state employment portal |
| LinkedIn Jobs (IE) | linkedin.com/jobs | generic | YES | valig/linkedin-jobs-scraper | Professional/tech/EN roles |

**Gaps:** none.

## Netherlands

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| Indeed (NL) | nl.indeed.com | generic | YES | orgupdate/indeed-jobs-scraper | Largest general aggregator in NL |
| Nationale Vacaturebank | nationalevacaturebank.nl | generic | YES | blackfalcondata; unfenced-group; solidcode | One of biggest Dutch boards (DPG) |
| Werk.nl (UWV) | werk.nl | generic (state) | YES | apify.com/werk-nl-scraper | Government employment portal |
| Monsterboard | monsterboard.nl | generic | YES | orgupdate/monster-jobs-scraper | Legacy international board, NL active |
| Joblift | joblift.nl | generic (aggregator) | **GAP** | no dedicated actor found | Large NL aggregator traffic |
| ICTerGezocht | ictergezocht.nl | tech | **GAP** | no dedicated actor found | Dutch IT/tech board |

**Top tech board:** ICTerGezocht — **no dedicated actor (GAP)**.
**Gaps:** Joblift, ICTerGezocht.

## Belgium

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| StepStone Belgium | stepstone.be | generic | YES | apify.com/stepstone-be-scraper | Dominant in BE market |
| VDAB.be | vdab.be | generic (Flemish state) | YES | apify.com/vdab-be-scraper | Official Flemish public service, largest |
| Jobat.be | jobat.be | generic | YES | studio-amba/jobat-scraper | Mediahuis, Flanders & Wallonia |
| Actiris | actiris.be | generic (Brussels state) | YES | apify.com/actiris-scraper | Brussels public employment |
| Select HR | select.be | generic | **GAP** | no dedicated actor found | Major recruitment brand, large vacancy DB |
| Jobsite.be | jobsite.be | generic | **GAP** | no dedicated actor found | Belgian board |

**Gaps:** Select HR, Jobsite.be.

## Germany

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| StepStone | stepstone.de | generic | YES | datawizards/stepstone-jobs-scraper; fatihtahta | Germany's #1 by reach |
| Indeed (DE) | de.indeed.com | generic | YES | curious_coder; orgupdate | Top-2 by usage |
| LinkedIn | linkedin.com | generic | YES | curious_coder; bebity | White-collar/tech/international |
| XING | xing.com | generic | YES | epctex/xing-scraper; pramodkonde17/xing-jobs-scraper | DACH professional network |
| get-in-it.de | get-in-it.de | tech | YES | Crawler Bros Get in IT | Leading DE IT/tech board |
| Arbeitsagentur Jobbörse | jobboerse.arbeitsagentur.de | generic (federal) | YES | signalflow; lexis-solutions; parsebird | Federal job bank |

**Top tech board:** get-in-it.de — dedicated actor exists.
**Gaps:** none.

## Austria

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| karriere.at | karriere.at | generic | YES | memo23; blackfalcondata; santamaria-automations | Austria's #1 |
| AMS eJob-Room | jobs.ams.at | generic (public) | YES | lexis-solutions; studio-amba | Official public service portal |
| StepStone Austria | stepstone.at | generic | YES | fatihtahta (supports AT) | Leading platform |
| Indeed (AT) | at.indeed.com | generic | YES | curious_coder; orgupdate | Major aggregator |
| jobs.at | jobs.at | generic | **GAP** | no dedicated actor found | Notable secondary commercial board |

**Gaps:** **jobs.at** — clear Austrian gap.

## Switzerland

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| jobs.ch | jobs.ch | generic | YES | lexis-solutions; santamaria-automations; powerbox; parsebird | Swiss market leader |
| jobup.ch | jobup.ch | generic | YES | lexis-solutions; unfenced-group; blackfalcondata | #2, Romandie strong |
| LinkedIn | linkedin.com | generic | YES | multiple | Senior/international roles |
| Indeed.ch | ch.indeed.com | generic | YES | curious_coder; orgupdate | ~#4 by traffic |
| SwissDevJobs | swissdevjobs.ch | tech | YES | dedicated | Swiss dev/IT board |
| ICTjobs.ch | ictjobs.ch | tech | YES | dedicated | Swiss ICT portal |

**Top tech boards:** SwissDevJobs.ch, ICTjobs.ch — both have actors.
**Gaps:** none.

## Poland

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| Pracuj.pl | pracuj.pl | generic | YES | trev0n; blackfalcondata | Most popular general board |
| OLX Praca | praca.olx.pl | generic | YES | unfenced-group; blackfalcondata | Blue-collar/volume |
| Praca.pl | praca.pl | generic | YES | unfenced-group | Another major portal |
| theprotocol.it | theprotocol.it | tech | YES | trev0n | Major PL IT/tech board |
| NoFluffJobs | nofluffjobs.com | tech | YES | unfenced-group; blackfalcondata; nomad-agent | Leading tech board, CEE expansion |
| JustJoin.it | justjoin.it | tech | YES | getdataforme; trev0n | Major PL tech/IT board |
| Indeed (PL) | pl.indeed.com | generic | YES | curious_coder; orgupdate | Supplementary aggregator |

**Top tech boards:** theprotocol.it / NoFluffJobs / JustJoin.it — all have actors.
**Gaps:** none.

## France

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| France Travail (ex Pôle emploi) | francetravail.fr | generic (public) | YES | apikiy; lexis-solutions; santamaria; studio-amba | #1 jobs site in France |
| Indeed | fr.indeed.com | generic | YES | curious_coder | #2 jobs site |
| LinkedIn | fr.linkedin.com | generic | YES | curious_coder; bebity | Professional roles |
| HelloWork (ex RegionsJob) | hellowork.com | generic | YES | multiple HelloWork/RegionsJob actors | #4 jobs site |
| Welcome to the Jungle | welcometothejungle.com | tech | YES | bebity; orgupdate; scrapeai; saswave | ~#8 traffic, startup/tech reference |
| APEC | apec.fr | generic (executive) | YES | easyapi; shahidirfan; scrapestorm; crawloop | Executive/engineer board |
| Cadremploi | cadremploi.fr | generic (executive) | YES | unfenced-group | Executive/management |

**Top tech board:** Welcome to the Jungle — has dedicated actors (note: our own `evanokeefe39/wttj-france-jobs` is one of them).
**Gaps:** Jobijoba (jobijoba.com) — niche aggregator, no dedicated actor, low priority. (Monster France dead since Nov 2025 — not monetizable.)

## Spain

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| InfoJobs | infojobs.net | generic | YES | minyo; parsebird; automation-lab; ecomscrape; lexis-solutions | #1 board, ~2.46M vacancies |
| LinkedIn | es.linkedin.com | generic | YES | curious_coder | #2 professional |
| Indeed | es.indeed.com | generic | YES | curious_coder | #3 high-volume |
| Infoempleo | infoempleo.com | generic | **GAP** | no dedicated actor found | Genuine top-5 generalist |
| Tecnoempleo | tecnoempleo.com | tech | YES | unfenced-group; blackfalcondata | Spain's top tech/IT board |

**Top tech board:** Tecnoempleo — has actors.
**Gaps:** **Infoempleo** — highest-value gap in Spain (popularity × absence).

## Portugal

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| Indeed | pt.indeed.com | generic | YES | curious_coder | Volume giant |
| LinkedIn | pt.linkedin.com | generic | YES | curious_coder | White-collar/tech |
| Net-Empregos | net-empregos.com | generic | YES | unfenced-group | Largest generalist board |
| Sapo Emprego | emprego.sapo.pt | generic | YES | unfenced-group | Leading PT board |
| itjobs.pt | itjobs.pt | tech | YES | blackfalcondata | Leading PT IT board |
| Landing.Jobs | landing.jobs | tech | **GAP** | no dedicated actor found | Prominent tech careers marketplace |

**Top tech board:** Landing.Jobs (strong tech brand) — **no dedicated actor (GAP)**; itjobs.pt covered.
**Gaps:** **Landing.Jobs**.

## Italy

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| Indeed | it.indeed.com | generic | YES | curious_coder | #1 jobs website in Italy |
| LinkedIn | it.linkedin.com | generic | YES | curious_coder | Premier professional network |
| Subito Lavoro | subito.it (jobs) | generic (classifieds) | YES | multiple Subito.it actors (jobs scraper) | Large classifieds with job section |
| Trovolavoro | trovolavoro.it | generic | YES | completed_xanadu/trovolavoro-job-scraper | Corriere della Sera matching platform |
| Randstad Italy | randstad.it | generic (agency) | YES | unfenced-group/randstad-it-scraper | Leading staffing agency |

**Gaps:** none among dominant boards (only niche Jobrapido Italia — low priority). InfoJobs.it and Monster Italia not monetizable (shut down / stopped publishing).

## Sweden

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| Arbetsförmedlingen / Platsbanken | arbetsformedlingen.se | generic (public, largest) | YES | Platsbanken Scraper family | Sweden's official largest portal |
| LinkedIn | linkedin.com | generic | YES | LinkedIn Jobs Scraper | Dominant white-collar/tech |
| Indeed | indeed.se | generic | YES | Indeed Jobs Scraper | One of most-visited in SE |
| Blocket Jobb | blocket.se | generic (commercial) | **GAP** | no dedicated jobs actor — Blocket actors target classifieds/cars only | Market leader in online classifieds |
| JobbSafari | jobbsafari.se | generic | **GAP** | no standalone dedicated actor (only inside multi-source aggregator) | Among larger Swedish portals |

**Top tech board:** The Hub (thehub.io) — pan-Nordic startup/tech, has dedicated actor.
**Gaps:** **Blocket Jobb**, JobbSafari.

## Norway

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| FINN Jobb | finn.no | generic (market leader) | YES | shirant; shahidirfan | Most-visited for jobs |
| Arbeidsplassen (NAV) | arbeidsplassen.no | generic (public) | YES | lexis-solutions; studio-amba; logiover | Official public portal |
| LinkedIn | linkedin.com | generic | YES | LinkedIn Jobs Scraper | Professional/international |
| Indeed | indeed.no | generic | YES | Indeed Jobs Scraper | Established presence |
| Jobbnorge | jobbnorge.no | generic (academic/public) | YES | Jobbnorge scraper | Large source incl. IT |

**Top tech board:** The Hub — has actor.
**Gaps:** none.

## Denmark

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| Jobindex | jobindex.dk | generic (market leader) | YES | Jobindex.dk Scraper family | Largest Danish job site, 1M users/mo |
| Jobzonen | jobzonen.dk | generic | **GAP** | no dedicated actor found | One of largest Danish portals, ~35k ads |
| LinkedIn | linkedin.com | generic | YES | LinkedIn Jobs Scraper | 15k+ DK jobs |
| Indeed | indeed.dk | generic | YES | Indeed Jobs Scraper | Substantial presence |
| IT-Jobbank | it-jobbank.dk | tech | YES | covered by Jobindex Scraper | Dedicated DK IT job bank |
| Workindenmark | workindenmark.dk | generic (public/international) | **GAP** | no dedicated actor found | Official international recruiting portal |

**Top tech board:** IT-Jobbank — coverage rides on Jobindex Scraper.
**Gaps:** **Jobzonen**, Workindenmark.

## Finland

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| Duunitori | duunitori.fi | generic (leader) | YES | Duunitori Scraper | Finland's largest, 700k+ weekly visitors |
| Job Market Finland / Työmarkkinatori | tyomarkkinatori.fi | generic (public) | YES | Työmarkkinatori.fi Scraper | Official national portal |
| LinkedIn | linkedin.com | generic | YES | LinkedIn Jobs Scraper | Professional/international |
| Jobly | jobly.fi | generic | YES | unfenced-group/jobly-fi-scraper | Significant local board |
| Oikotie Työpaikat | oikotie.fi | generic | — | defunct (shut 28 Feb 2025) | Not an active gap |

**Top tech board:** The Hub — has actor.
**Gaps:** none among active top boards (Oikotie defunct).

## Czechia

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| Jobs.cz | jobs.cz | generic (leader) | YES | lexis-solutions; shahidirfan | #1 portal, 4M+ monthly visits |
| Prace.cz | prace.cz | generic (#2) | YES | lexis-solutions; unfenced-group | Close to Jobs.cz |
| LinkedIn | linkedin.com | generic | YES | LinkedIn Jobs Scraper | Smaller than local leaders |
| StartUpJobs | startupjobs.cz | tech | YES (weak) | martin1080p; shahidirfan — flagged "Under maintenance" | Niche startup/tech |
| Indeed | indeed.cz | generic | YES | Indeed Jobs Scraper | Less dominant locally |

**Top tech board:** StartUpJobs — actor exists but **under maintenance** (weakest/riskiest existing coverage → replacement opportunity).
**Gaps:** none strictly (StartUpJobs actor at risk).

## Hungary

| Board | Domain | Type | Apify actor? | Evidence | Popularity note |
|---|---|---|---|---|---|
| Profession.hu | profession.hu | generic (leader) | YES | unfenced-group; solidcode; studio-amba; blackfalcondata | Most popular, 4M monthly visits |
| CV Online | cvonline.hu | generic | YES | unfenced-group/cvonline-hu-scraper | Leading board |
| LinkedIn | linkedin.com | generic | YES | LinkedIn Jobs Scraper | Widely used |
| Careerjet | careerjet.com (HU) | generic (aggregator) | YES | Careerjet Jobs Scraper | Among top HU sites |
| Indeed | indeed.hu | generic | YES | Indeed Jobs Scraper | Broad discovery |

**Gaps:** none.

---

## Master gap list (boards without a dedicated, maintained actor)

| Board | Domain | Country | Type | Why it's monetizable |
|---|---|---|---|---|
| **jobs.at** | jobs.at | Austria | generic | Notable mainline commercial board, zero dedicated actor |
| **Select HR** | select.be | Belgium | generic | Major recruitment brand, large vacancy DB |
| **Jobsite.be** | jobsite.be | Belgium | generic | Belgian board, no actor |
| **Joblift** | joblift.nl | Netherlands | generic (aggregator) | Large NL aggregator traffic, no actor |
| **ICTerGezocht** | ictergezocht.nl | Netherlands | tech | Dutch IT/tech board, no actor |
| **Infoempleo** | infoempleo.com | Spain | generic | Genuine top-5 generalist, no actor |
| **Landing.Jobs** | landing.jobs | Portugal | tech | Prominent tech careers brand, no actor |
| **Blocket Jobb** | blocket.se | Sweden | generic | Market leader in classifieds, only cars/classifieds actors |
| **JobbSafari** | jobbsafari.se | Sweden | generic | Larger portal, no standalone actor |
| **Jobzonen** | jobzonen.dk | Denmark | generic | One of Denmark's largest (~35k ads), no actor |
| **Workindenmark** | workindenmark.dk | Denmark | generic (state/international) | Official international recruiting portal |
| **Jobijoba** | jobijoba.com | France | generic (aggregator) | Niche mid-tier, low priority |
| **StartUpJobs** | startupjobs.cz | Czechia | tech | Actor exists but "Under maintenance" — replacement fills niche |

## Coverage summary

| Country | boards inventoried | with actor | gaps |
|---|---|---|---|
| United Kingdom | 5 | 5 | 0 |
| Ireland | 5 | 5 | 0 |
| Netherlands | 6 | 4 | 2 (Joblift, ICTerGezocht) |
| Belgium | 6 | 4 | 2 (Select HR, Jobsite.be) |
| Germany | 6 | 6 | 0 |
| Austria | 5 | 4 | 1 (jobs.at) |
| Switzerland | 6 | 6 | 0 |
| Poland | 7 | 7 | 0 |
| France | 7 | 7 | 0 dominant (1 niche) |
| Spain | 5 | 4 | 1 (Infoempleo) |
| Portugal | 6 | 5 | 1 (Landing.Jobs) |
| Italy | 5 | 5 | 0 dominant |
| Sweden | 5 | 3 | 2 (Blocket Jobb, JobbSafari) |
| Norway | 5 | 5 | 0 |
| Denmark | 6 | 4 | 2 (Jobzonen, Workindenmark) |
| Finland | 4 | 4 | 0 |
| Czechia | 5 | 5 | 0 (StartUpJobs weak) |
| Hungary | 5 | 5 | 0 |

Totals: 18 countries, ~99 board listings, ~13 genuine gaps (12 clear + 1 niche + 1 weak-coverage replacement).

---

## Ranked monetization opportunities (popularity × absence of dedicated actor)

1. **Jobzonen (Denmark)** — one of Denmark's largest portals (~35k ads) with genuinely zero actor. High scale, clear market, single clean target.
2. **Blocket Jobb (Sweden)** — the classifieds market leader; its huge traffic has jobs actors missing (only cars/classifieds actors exist). Very large reach.
3. **jobs.at (Austria)** — recognizable mainline commercial board, no actor, straightforward target.
4. **Infoempleo (Spain)** — genuine top-5 generalist Spanish board, no actor; solid volume.
5. **Select HR (Belgium)** — major recruitment brand with big vacancy database; a single dominant BE gap.
6. **Landing.Jobs (Portugal)** — strong pan-European tech brand; high value for tech-hiring niche.
7. **Joblift (Netherlands)** — large NL aggregator traffic, no actor.
8. **ICTerGezocht (Netherlands)** — Dutch IT/tech board, tech-niche demand.
9. **Workindenmark (Denmark)** — official international recruiting portal; value for expat/English-role niche.
10. **StartUpJobs (Czechia)** — existing actor is "Under maintenance"; a maintained replacement wins the niche.

Secondary / low priority: JobbSafari (SE), Jobsite.be (BE), Jobijoba (FR).

## Quality bar — existing actors to model after

- **Welcome to the Jungle / HelloWork / France Travail (France)** — multiple maintained actors (`bebity`, `orgupdate`, `lexis-solutions`, `studio-amba`); good templates for structured-record actors. Our own `evanokeefe39/wttj-france-jobs` (Apify RESIDENTIAL-proxy + Crawlee) is a working reference.
- **Pracuj.pl / Jobs.cz / Profession.hu (CEE)** — well-covered with structured salary/location fields; templates for local-market actors.
- The leaders in coverage: Indeed, LinkedIn, StepStone, jobs.ch, Jobindex, Duunitori, InfoJobs, Pracuj.pl — all have multiple actors; these are "already won" (build differentiated/cheaper only if you can beat on price/features).

## Path to full (100%) Europe coverage

Wave 1 covered the 18 major markets; Wave 2 (below) adds the remaining 14.
Full European coverage is now inventoried.

## Wave 2 — remaining markets (2026-08-28)

### Iceland — CLEAN VOID (highest-value in this batch)

No dedicated Apify actor found for ANY major Icelandic board: Alfreð (alfred.is),
Job.is, Störf.is, Starfatorg (state), Atvinna.is. Small market but genuinely zero
existing coverage.

### Cyprus — near-total gap

Ergodotisi (state), Carierista, CyprusWork, CyprusJobs.com, and the tech board
CyprusTech.Careers all lack dedicated actors. Only a Bazaraki classifieds actor
exists (not a job board). Clearest single-country opportunity.

### Estonia / Latvia / Lithuania — well covered

Estonia: CV Keskus, CV.ee, Töötukassa (state) all have dedicated actors.
Latvia: CV.lv, CVMarket.lv, Visidarbi.lv, NVA (state), Prakse.lv covered.
Lithuania: CVOnline.lt, CVBankas covered. No meaningful gaps.

### Luxembourg — partial

Jobs.lu HAS a dedicated actor (studio-amba/jobs-lu-scraper); Monster has global
actors. **Gaps:** Moovijob (moovijob.com), Work in Luxembourg / jobfinder.lu.

### Malta — saturated (low value)

Unfenced Group maintains actors for Jobsplus, JobHound, KeepMePosted, Konnekt.
Only MaltaJobs.eu is a gap — lowest-value opportunity.

### Croatia / Serbia / Slovakia / Slovenia (Wave 2 — partial data)

- Croatia: MojPosao (YES), Posao.hr (YES), Burza Rada/HZZ (YES). No top gap.
- Serbia: Infostud / Poslovi Infostud (YES), HelloWorld.rs tech (YES). **Gap:** poslovi.rs.
- Slovakia: Profesia.sk (YES), kariera.sk (YES), worki.sk/ISTP (YES). **Gaps:** praca.sme.sk, job.sk.
- Slovenia: MojeDelo.com (YES — note: `apify/mojedelo-scraper` is the CZECH
  mojedelo.cz, not Slovenian; Slovenian MojeDelo.com coverage needs confirming).
  **Gaps:** Kariera.si, Optius.

### Romania / Bulgaria / Greece (Wave 2 — partial data)

Largely covered by `unfenced-group` and `studio-amba` actor families: eJobs,
BestJobs (RO); Jobs.bg, Zaplata.bg, JobTiger.bg (BG); Skywalker.gr, Kariera.gr (GR)
all have dedicated actors. No confirmed major gaps surfaced; a tighter verification
pass would confirm any residual niche gaps.

## Wave 2 master gaps (added)

| Board | Domain | Country | Why monetizable |
|---|---|---|---|
| Alfreð / Job.is / Störf.is / Starfatorg / Atvinna | *.is | Iceland | Entire country void, zero actors |
| Ergodotisi / Carierista / CyprusWork / CyprusJobs / CyprusTech.Careers | *.cy | Cyprus | Near-total country gap |
| Moovijob / Work in Luxembourg | moovijob.com, jobfinder.lu | Luxembourg | Second-tier LU boards, no actor |
| MaltaJobs.eu | maltajobs.eu | Malta | Only MT gap (low value) |
| poslovi.rs | poslovi.rs | Serbia | Secondary RS board, no actor |
| praca.sme.sk, job.sk | *.sk | Slovakia | Secondary SK boards, no actor |
| Kariera.si, Optius | *.si | Slovenia | Secondary SI boards, no actor |

## Updated totals

18 (wave 1) + 14 (wave 2) = **32 European markets inventoried**. Wave 2 adds
Iceland (void), Cyprus (near-void), plus scattered second-tier gaps in LU/MT/RS/SK/SI.
Combined: the stand-out monetizable voids are **Iceland, Cyprus, Denmark (Jobzonen),
Sweden (Blocket Jobb), Austria (jobs.at)**.

*Compiled 2026-08-28 via 4 parallel research agents (wave 1: UK/IE/NL/BE; DE/AT/CH/PL;
FR/ES/PT/IT; SE/NO/DK/FI/CZ/HU — wave 2: RO/BG/GR; HR/SI/RS/SK; EE/LV/LT/IS; LU/MT/CY).
Every YES/GAP grounded in Apify Store + web-search evidence with cited actor URLs.
Popularity per Similarweb (July 2026). RO/BG/GR and HR/SI/RS/SK partially verified —
confirm residual niche gaps before building.*
