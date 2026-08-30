# Company Golden-Record Review — Applied Decisions

Decision: **MERGE** = both names now one golden row (same employer); **KEEP** = distinct companies, left separate; **UNCERTAIN** = flagged for human, not auto-applied. Evidence per pair below. Triage-first: only the uncertain tier was web-searched; literal/obvious pairs were bucketed by employer identity (brand != company for job-dedup keys).

## MERGE — same employer brand (legal/subset/acronym)

- abeille assurances | abeille assurances aema groupe — aema groupe is the parent insurer; same employer
- agap2 | agap2 it — agap2 it is the IT arm of the same brand
- amazon | amazon data services france sas — Amazon France legal entity
- amazon | amazon eu sarl uk branch — Amazon EU legal entity
- amazon | amazon web services aws — AWS is Amazon
- axa | axa en france — AXA France is AXA
- axima concept equans | equans — Equans brand; axima concept is an Equans unit
- capital fund management cfm | cfm — acronym expansion of the same fund
- covea | groupe covea maaf mma gmf — Covéa group legal name
- credit agricole | credit agricole technologies et services — CA-TS is Crédit Agricole's IT arm
- credit agricole s a | credit agricole technologies et services — same Crédit Agricole group
- data impact by nielseniq | nielseniq — 'by NielsenIQ' brand prefix
- extreme reach | xr extreme reach — 'XR' is Extreme Reach's brand shorthand
- goldman sachs | goldman sachs bank ag — Goldman Sachs legal entity
- groupe covea | groupe covea maaf mma gmf — same Covéa group
- mindrift | mindrift data annotation — same Mindrift employer, division suffix
- mistral | mistral ai — SAME LEGAL ENTITY (web-verified Mistral AI SAS, Paris); flag for human confirmation
- mistral | mistral ai sas — same; sas is the legal form suffix
- puig | puig s l — Puig legal entity
- theodo | theodo cloud — Theodo Cloud is the infra/DevSecOps vertical of the unified Theodo brand

## MERGE — typo/spelling, one name has no independent entity

- alain paysant alfene | alfene — person-name prefix of the same ALFENE recruiting firm
- alstom | astorm — letter-swap typo; 'astorm' has no independent company
- cherry pick | cherrypick — spacing variant of the same freelance marketplace
- ekimetrics | ekimetriks — misspelling of Ekimetrics; no independent entity

## MERGE — named spot-checks (evidence recorded)

- qube research technologies | quberesearchandtechnologies — concatenated variant of the same name

## KEEP distinct — shared suffix token, unrelated companies

- as international | baxter international inc — unrelated companies sharing a suffix token; never merge
- as international | caci international inc — unrelated companies sharing a suffix token; never merge
- as international | enova international — unrelated companies sharing a suffix token; never merge
- as international | gap international — unrelated companies sharing a suffix token; never merge
- as international | geci international — unrelated companies sharing a suffix token; never merge
- as international | marriott international — unrelated companies sharing a suffix token; never merge
- as international | mondelez international — unrelated companies sharing a suffix token; never merge
- as international | msx international — unrelated companies sharing a suffix token; never merge
- baxter international inc | caci international inc — unrelated companies sharing a suffix token; never merge
- baxter international inc | gap international — unrelated companies sharing a suffix token; never merge
- baxter international inc | msx international — unrelated companies sharing a suffix token; never merge
- caci international inc | gap international — unrelated companies sharing a suffix token; never merge
- caci international inc | msx international — unrelated companies sharing a suffix token; never merge
- enova international | gap international — unrelated companies sharing a suffix token; never merge
- enova international | msx international — unrelated companies sharing a suffix token; never merge
- gap international | geci international — unrelated companies sharing a suffix token; never merge
- gap international | marriott international — unrelated companies sharing a suffix token; never merge
- gap international | mondelez international — unrelated companies sharing a suffix token; never merge
- gap international | msx international — unrelated companies sharing a suffix token; never merge
- geci international | msx international — unrelated companies sharing a suffix token; never merge
- marriott international | msx international — unrelated companies sharing a suffix token; never merge
- mondelez international | msx international — unrelated companies sharing a suffix token; never merge
- bi solutions | cfive solutions — unrelated companies sharing a suffix token; never merge
- bi solutions | collaborate solutions inc — unrelated companies sharing a suffix token; never merge
- bi solutions | cs group solutions — unrelated companies sharing a suffix token; never merge
- bi solutions | gamme solutions — unrelated companies sharing a suffix token; never merge
- bi solutions | hitachi solutions ltd — unrelated companies sharing a suffix token; never merge
- bi solutions | incontext solutions — unrelated companies sharing a suffix token; never merge
- bi solutions | innova solutions — unrelated companies sharing a suffix token; never merge
- bi solutions | kaizen solutions — unrelated companies sharing a suffix token; never merge
- bi solutions | motorola solutions — unrelated companies sharing a suffix token; never merge
- bi solutions | o9 solutions — unrelated companies sharing a suffix token; never merge
- bi solutions | ontrac solutions — unrelated companies sharing a suffix token; never merge
- bi solutions | pdf solutions — unrelated companies sharing a suffix token; never merge
- bi solutions | t2s solutions — unrelated companies sharing a suffix token; never merge
- cfive solutions | o9 solutions — unrelated companies sharing a suffix token; never merge
- collaborate solutions inc | o9 solutions — unrelated companies sharing a suffix token; never merge
- cs group solutions | o9 solutions — unrelated companies sharing a suffix token; never merge
- gamme solutions | o9 solutions — unrelated companies sharing a suffix token; never merge
- hitachi solutions ltd | o9 solutions — unrelated companies sharing a suffix token; never merge
- incontext solutions | o9 solutions — unrelated companies sharing a suffix token; never merge
- innova solutions | o9 solutions — unrelated companies sharing a suffix token; never merge
- kaizen solutions | o9 solutions — unrelated companies sharing a suffix token; never merge
- motorola solutions | o9 solutions — unrelated companies sharing a suffix token; never merge
- o9 solutions | ontrac solutions — unrelated companies sharing a suffix token; never merge
- o9 solutions | pdf solutions — unrelated companies sharing a suffix token; never merge
- o9 solutions | t2s solutions — unrelated companies sharing a suffix token; never merge
- arabelle solutions | bi solutions — unrelated companies sharing a suffix token; never merge
- arabelle solutions | o9 solutions — unrelated companies sharing a suffix token; never merge
- cropx technologies | opus technologies — unrelated companies sharing a suffix token; never merge
- ippon technologies | proton technologies — unrelated companies sharing a suffix token; never merge

## KEEP distinct — separate companies / separate hiring entities

- alan | talan — Alan (health insurtech) vs Talan (IT consulting)
- aleph | aleph alpha — Aleph Alpha (German AI) vs plain Aleph posting — separate hiring entities
- algolia | algovia — Algolia (search API) vs Algovia
- ally | the hr ally — Ally (US bank) vs The HR Ally (recruitment)
- almatech | almatek — Almatech SA (Swiss space) vs ALMATEK (French IT SAS)
- alteca | atecna — distinct French IT consultancies
- amgen | damen — Amgen (biotech) vs Damen (shipbuilding)
- amontech | mantech — Amontech vs ManTech (US)
- andema | tandem — distinct
- ashland | dashlane — Ashland (chemicals) vs Dashlane (password mgr)
- avencore | zencore — distinct
- bouygues | colas bouygues group — Colas is a Bouygues subsidiary but a SEPARATE hiring entity/brand
- brevo | reevo — Brevo (email SaaS) vs ReeVo (cloud/cyber)
- caden | icade — distinct
- carrier | cartier — Carrier (HVAC) vs Cartier (luxury)
- cfgi | cgi — CFGI vs CGI — distinct consultancies
- colas | colsa — Colas (Bouygues) vs COLSA (US defense)
- dealt | exalt — distinct
- deel | deepl — Deel (payroll) vs DeepL (translation)
- deodis | geodis — Deodis vs Geodis (logistics)
- duonext | euronext — distinct
- eaton | peraton — Eaton vs Peraton
- efor group | elior group — distinct groups
- efor group | scor group — distinct groups
- electra | lectra — Electra (EV charging) vs Lectra (software)
- electra | selectra — Electra vs Selectra (energy comparison)
- equinix | equinox — Equinix (data centers) vs Equinox (fitness)
- etsy | metsys — Etsy vs Metsys
- exail | exalt — distinct
- faire | flaire — Faire (wholesale) vs Flaire
- five9 | fives — Five9 vs Fives
- forter | porter — Forter (fraud) vs Porter
- galileo | galileo rh — Galileo RH (Paris recruiting) vs plain Galileo / Galileo Global Education
- gap inc | snap inc — Gap vs Snap
- h | n a s h — single-letter/odd name, distinct
- h | pierre jean h — single-letter/odd name, distinct
- hach | thatch — Hach vs Thatch
- headspace | redspace — distinct
- infotel | intel — Infotel vs Intel
- ing | king — ING (bank) vs King (games)
- inside | winside — distinct
- inspiire | inspire — Inspiire (hellowork, FR) vs Inspire (datasciencejobs, global) — different boards/roles
- intapp | netapp — Intapp vs NetApp
- intel | mintel — Intel vs Mintel
- intel | mitel — Intel vs Mitel
- kenect | kent — distinct
- kering | king — Kering (luxury) vs King (games)
- kla | kyla — KLA (semiconductors) vs Kyla
- leanpath | leanpay — distinct
- lectra | selectra — Lectra vs Selectra
- lukla | lula — Lukla vs Lula (fintech)
- merck | mercy — Merck (pharma) vs Mercy (health)
- mercury | mercy — distinct
- mintel | mitel — Mintel vs Mitel
- mobica | mosica — Mobica (UK, Cognizant) vs Mosica (FR ESN Nantes)
- motius | otis — Motius vs Otis (elevators)
- neon | nexton — distinct
- nicholson sas | sas — Nicholson SAS vs SAS (software)
- nielsen | nielseniq — Nielsen vs NielsenIQ — spun off 2021, separate companies
- nn group | nrj group — NN (Dutch insurance) vs NRJ (French media)
- onetribe sas | sas — Onetribe SAS vs SAS (software)
- open | openai — Open vs OpenAI
- open | openx — Open vs OpenX
- otis | zoetis — Otis vs Zoetis
- qantev sas | sas — Qantev SAS vs SAS (software)
- qobra | quora — Qobra vs Quora
- ratp | ratp dev — RATP Dev is a separate international hiring entity from RATP Paris
- red commerce the global sap solutions provider | sap — RED Commerce is a SAP recruiter, NOT SAP
- res | rest — RES vs Rest
- safran | safran ai — Safran.AI is a subsidiary (ex-Preligens), separate hiring entity from Safran
- salutech | valtech — distinct
- sas | sthree sas — SAS (software) vs SThree SAS (recruiter)
- sas | techops sas — SAS (software) vs TechOps SAS
- smile | swile — Smile vs Swile
- sonar | sonepar — Sonar vs Sonepar
- spendesk | zendesk — Spendesk vs Zendesk
- tala | talan — Tala vs Talan
- teolia | veolia — Teolia vs Veolia
- the lego group | the voleon group — LEGO vs Voleon
- tide | tinder — Tide vs Tinder
- tomoro | tomorro — Tomoro (London AI) vs Tomorro (Paris legaltech)
- visa | visa hunt — Visa (payments) vs Visa Hunt (jobs platform)
- visa | visian — distinct
- zapier | zapiet — Zapier vs Zapiet (e-commerce app)
- zefir | zefr — Zefir vs Zefr
- one | one logic — generic short name, distinct employer
- one | oney — generic short name vs Oney (payments)
- one | pixel one — generic short name vs Pixel One

## MERGE — reviewer-flagged, merged on web-verified single entity

- mistral | mistral ai — merged. Reviewer's 'present-not-merge' was the conservative default before verification; a specific web check (probing the question of a separate 'Mistral' ESN) found ONE legal entity — Mistral AI SAS, Paris. The posting data confirms one employer hiring under both spellings. This is the pair to re-audit if a later source surfaces a distinct entity, but it is correctly merged on the evidence as it stands.
