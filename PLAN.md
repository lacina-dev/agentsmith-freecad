# AgentSmith — implementační plán (od 0.17.0)

Plán vychází z auditu 2026-07-24. Řadí práci tak, aby **každá další fáze byla
měřitelná fází předchozí**: nejdřív testovatelné jádro, pak funkční checky, teprve
pak baseline — jinak by baseline měřil jen geometrii a všechna pozdější čísla by
byla nesrovnatelná.

Legenda stavu: `[ ]` čeká · `[~]` rozpracováno · `[x]` hotovo

---

## Fáze 0 — hygiena (½ dne, bez rizika) — **HOTOVO 2026-07-24**

- [x] **0.1** `eval/README.md`: opravit dvě zmínky `/tmp/freecad-codex-bridge.json`
      → `/tmp/freecad-agentsmith-bridge.json` (kód i `run_eval.DISCOVERY_FILE`
      používají agentsmith variantu; stará cesta mate při ladění „bridge unreachable").
- [x] **0.2** Založit `eval/baselines/` **mimo** `.gitignore` (dnes je ignorované celé
      `eval/results/`, takže není s čím srovnávat) + `baselines/README.md` s konvencí
      pojmenování a postupem.
- [x] **0.3** Test scaffolding: `tests/` + `tests/helpers.py`, běh **bez FreeCADu**.
      Do `README.md` řádek „jak pustit testy".
      **Odchylka od plánu:** místo pytestu stdlib `unittest`
      (`cd tests && python3 -m unittest discover`) — pytest v systému není a zbytek
      repa je záměrně stdlib-only, takže testy běží bez instalace a bez venv.

**Akceptace splněna:** testy běží na stroji bez FreeCADu i bez pytestu.

---

## Fáze 1 — testovatelné jádro (blokuje 2, 5 a 8) — **KÓD HOTOV, čeká smoke test**

Dnes je 6 729 řádků Pythonu a **nula testů** (tři staré leží v ignorovaném `_attic/`).
Cíl: vytáhnout z `BridgeGui.py` čistou logiku bez GUI/FreeCAD importů a otestovat ji.
Bez toho je refaktor (fáze 8) sázka a fáze 5 rozbije supervisora naslepo.

Moduly dostaly prefix `agentsmith_`, protože **všechny FreeCAD Mod adresáře sdílí
`sys.path`** a obecné jméno (`supervision.py`) by mohlo kolidovat s jiným addonem.

- [x] **1.1** `freecad_bridge/agentsmith_supervision.py` — `classify_outcome`
      (ACTION-ONLY kontrakt + fingerprint-jitter výjimka + follow-up akce),
      `restore_reason`, `evaluate_guard` (jeden tik watchdogu: rozpočet, RSS,
      zavřený/přesunutý dokument, tolerance 3 tiků u chybějícího FCStd).
- [x] **1.2** `freecad_bridge/agentsmith_backends.py` — `build_launch_args`
      + `backend_event_text` pro codex / claude / copilot.
- [x] **1.3** `freecad_bridge/agentsmith_harness.py` — `load_registry`,
      `build_index`, `assemble_harness`.
- [x] **1.4** 72 testů v `tests/`; fixtures jsou **reálné zachycené řádky** z
      `.agentsmith/task-history.jsonl` (codex `item.completed`/`item.started`/
      `turn.completed`, claude `assistant`/`system`/`user`/`result`).
      Navíc `test_no_gui_imports.py` hlídá, že se do modulů nevloudí import
      FreeCADu/Qt, a `RealHarness` testy ověřují konzistenci `registry.json`
      s .md soubory na disku (obě strany, žádný osiřelý ani neregistrovaný soubor).
- [x] **1.5** `BridgeGui.py` na moduly jen deleguje; chování beze změny.
- [x] **1.6** *(nad rámec plánu)* `eval/run_e2e.py` přestal držet vlastní kopie
      `assemble_harness_text` a `build_backend_command` a volá tytéž moduly.
      **Kopie už byly rozejité** — e2e index promptu neobsahoval jednu větu
      preambule, takže eval měřil prompt, který se v produkci nikdy neposílá.

**Akceptace splněna 2026-07-24:** 72 testů bez FreeCADu **+ smoke test v živém
FreeCADu 1.1.1** (AppImage, sandbox dokument ve scratchpadu, žádný uživatelův model):

| Ověřeno | Výsledek |
|---|---|
| Addon + tři nové moduly se načtou | OK, bridge 0.17.0, backendy codex/claude/copilot detekovány |
| Harness přes `agentsmith_harness` | 18 playbooků, **74 585 znaků — shodně s `run_e2e.py`**, drift zmizel |
| `build_launch_args` naostro | claude spuštěn s očekávanými flagy + hlavičkou ATTACHED IMAGES |
| Úspěšná úloha (worker → supervisor) | success, changed, 1 bridge event, Box 40 → **55 mm**, checkpoint před/po, vizuál po |
| File guard + rollback | FCStd smazán za běhu → obnoven ze zálohy v paměti, backend SIGTERM (exit 143), dokument vrácen na 55 mm, `restored: True` |

Netestováno naživo: reviewer/autofix kolo (vypnuto kvůli ceně; používá tentýž
`build_launch_args` jako worker) a rozpočtový/RSS limit watchdogu (pokryto
jednotkovými testy).

---

## Fáze 2 — funkční checky (největší díra v kvalitě) — **HOTOVO 2026-07-24**

> **Dva nálezy, které tuhle fázi přerostly.** Při prvním ostrém e2e běhu dostal
> pečlivě vymodelovaný díl 3/7 — a nebyla to vina modelu:
>
> 1. **Grader počítal datumy jako geometrii.** PartDesign Body s sebou nese Origin
>    s třemi rovinami a třemi osami, které FreeCAD hlásí jako `Visibility=True`
>    a s **nekonečným** bounding boxem. Výsledek: `overall_height = 2e+100`,
>    `min_bbox_dim = 0`, a `watertight`/`single_solid` padaly, protože datumová
>    rovina není uzavřené těleso. Jinými slovy: eval systematicky trestal právě ten
>    nativní parametrický postup, který harness vyžaduje. Opraveno sdíleným filtrem
>    `_is_geometry_object` (datumy/skici ven; uvnitř Body mluví Body, ale když má
>    Body prázdný Shape, zaskočí za něj Tip — jinak by díl zmizel z měření úplně).
> 2. **`face.normalAt()` už vrací vnější normálu** — ruční otočení podle
>    `face.Orientation == "Reversed"` ji obracelo podruhé. V `feature_probe` to
>    hlásilo každou díru jako boss; v původním `print_readiness` to obracelo detekci
>    převisů přesně u těch ploch, které vyrábí Pocket a stěny děr. Ověřeno naživo na
>    krychli (všech 6 stěn ven) i na díře (Reversed plocha míří k ose).

Grader dnes měří jen geometrii. Wall hook (18. 7.) dostal **7/7 a byl na zdi
nepoužitelný**; funkci hlídá jen LLM reviewer — nedeterministicky a za tokeny.
Lekce L1 (funkční póza) a L2 (projekční rovina) jdou z velké části zmechanizovat.

- [x] **2.1** Read-only bridge příkaz **`feature_probe`** (bridge 0.18.0): díry/bossy
      seskupené podle osy (směr, kanonický bod, průměr, délka, `kind`), rovinné plochy
      s vnější normálou a obsahem, a `standoff_mm` největší plochy = jak daleko těleso
      vyčnívá od montážní roviny (bounding box na to nestačí, normála je libovolná).
- [x] **2.2** Checky v `run_eval.py` (`check_functional`, blok `functional` v tasku):
      `functional_hole_axes` (L1), `functional_protrusion` (L2), `functional_slab_ratio`.
      Neznámý příkaz na starším bridge = `skipped`, ne `fail`.
      **Návrhová změna proti plánu:** `mounting_axis: "auto"` odvodí montážní rovinu
      z geometrie (největší rovinná plocha) místo světové osy — golden task pózu
      nefixuje (wall hook se záměrně modeluje naležato kvůli tisku), takže natvrdo
      zadaná osa by shazovala i správné díly. Přibyl `max_hole_dia_mm`: ložiskový domek
      má 22mm díru záměrně NEkolmou k základně, musí jít vyloučit z kontroly šroubů.
- [x] **2.3** Funkční bloky v `printed_wall_hook`, `simple_l_bracket`,
      `bearing_block_608`, `pi5_enclosure_base`.
- [x] **2.4** **Regresní důkaz — proveden dvakrát.** Původní vadný hák se nedochoval
      (nikdy nebyl uložen), tak byl reprodukován: (a) jednotkově z jeho geometrie
      (`tests/test_functional_checks.py`), (b) **naživo** ve FreeCADu na dvou reálných
      PartDesign tělesech — správný hák s 38mm ramenem prošel 3/3, plochá deska padla
      na `protrusion` (8 mm < 30) i `slab_ratio` (0.133 < 0.15).
      Zdokumentovaná nuance: u ploché desky **projde** kontrola os děr, protože ty
      k její největší ploše kolmé skutečně jsou — vadu popisují zbylé dva checky.
- [x] **2.5** `core.md` + `verify.md` odkazují na `feature_probe` (worker měří stejně
      jako grader, což odstraňuje celou třídu sporů mezi workerem a reviewerem).

**Akceptace splněna:** 111 jednotkových testů + živý důkaz na FreeCADu 1.1.1
(plochá deska padá, správný díl prochází). Bridge 0.18.0, `feature_probe` v
`read_commands` i v `capabilities`.

---

## Fáze 3 — baseline *(etapa 2 hotová; uchovatelný etalon zbývá)*

### Etapa 2 — 3 úlohy, `claude / claude-opus-5`, 1 běh
`eval/baselines/2026-07-25-claude-opus-5-etapa2-3tasks.json`

| úloha | skóre | čas | co z toho |
|---|---|---|---|
| `simple_l_bracket` | **8/8** | 551 s | čistý průchod |
| `printed_wall_hook` | **10/10** | 902 s | prošlo, ale **narazilo na limit** |
| `bearing_block_608` | **8/9** | 629 s | `bore_diameter`: 22,0 mm místo ≤ 21,95 |

- [x] **3.5** Etapa 2 proběhla a je uložená pod názvem podle etapy.
- [~] **3.6** **Není to uchovatelný etalon.** Jeden běh na úlohu je šum — nástroj to
      sám hlásí a doporučuje 3 opakování. Tahle etapa odpovídá na „projde to a kde to
      drhne", ne na „o kolik se to zlepšilo".
- [~] **3.7** **Limit 900 s je těsný.** `printed_wall_hook` do něj narazil v **obou**
      pokusech (901 a 902 s) a byl přerušen — plný počet bodů má proto, že model
      dokončil ještě před řezem, ale naměřený čas je spodní hranice, ne měření.
      Před uchovávanou baseline limit zvednout, jinak měříme, kdo se stihl vejít.
- [x] **3.8** **Nález, kvůli kterému to mělo smysl pouštět:** `bearing_block_608`
      vyrobil díru pro lisované uložení na **jmenovitých 22,0 mm**. Potřetí to samé
      (dvakrát v etapě 1). Harness to pravidlo **obsahuje** — není to mezera ve
      znalostech, ale v jejich použití. → `lessons.md` **L8** + kontrolní otázka
      přímo v `tolerances.md`.

`eval/results/history.jsonl` má **2 řádky**, `run_e2e.py` nikdy neběžel ostře.
18 playbooků (+ `core.md` a `lessons.md`) a celý reviewer/autofix aparát nemá
důkaz účinnosti.

- [x] **3.1** `run_e2e.py --repeat N` — každé opakování má vlastní sandbox dokument,
      vlastní log a `run_index` v záznamu.
- [x] **3.2** `eval/compare.py` — agreguje opakování na pass-rate, značí
      zlepšení/regrese/nové checky, exit 1 při regresi (lze použít jako bránu ve
      skriptu). Čte oba tvary snapshotů (run_e2e i run_eval). 15 testů.
- [x] **3.0** *(nad rámec plánu, nutné před baseline)* Worker e2e sandboxu už neběží
      v kořeni repa, ale v sandbox adresáři. Repo jako CWD ho svádělo číst zdrojáky
      addonu místo volání bridge (naživo: worker minuty grepoval `BridgeGui.py`) a
      pouštělo mu artefakty do pracovního stromu.
- [~] **3.3** Proběhla **etapa 1: všech 7 úloh × 1 běh** (`gpt-5.6-sol`, 14 min):
      **56/59 checků, 5/7 úloh čistě**, žádný timeout. Účel byl smoke test zadání
      před placením opakování — a vyplatil se: **všechny tři faily byly v evalu,
      ne v modelech** (float na hraně tolerance; „aspoň 8 mm" zapsané jako ±3 okno;
      rozměr měřený přes bbox obou dílů zároveň). Po opravě zbyly dva **skutečné**
      nálezy: bore vychází na nominálních 22,0 místo podměry pro press fit (2× po
      sobě), a snap-fit nechal viditelná konstrukční tělesa. Předtím A/B na wall
      hooku: `gpt-5.6-sol` 10/10 za 128 s, `claude-opus-5` 10/10 za 965 s.
      **Zbývá etapa 2** — opakování na nestabilních úlohách (`snap_fit_box` selhal
      ve dvou bězích na dvou různých checcích).
- [~] **3.4** Spot check uložen v `eval/baselines/` i s vysvětlením, proč záznam
      Opusu ukazuje fail (grader ho hodnotil ještě prahem 300°, teardrop díry mají
      270° — po opravě na 240 přehodnoceno naživo na 3/3). Záznam ponechán needitovaný,
      aby oprava zůstala viditelná.

**Akceptace:** ✅ `compare.py` + `--repeat N` (17 testů) · ✅ etapa 1 proběhla a
opravila 3 chyby v evalu · ⏳ zbývá etapa 2 (opakování) a rozhodnutí o commitu.

---

## Fáze 4 — MCP server místo CLI wrapperu *(kód hotov; 4.4 čeká na baseline)*

Dnes agent volá `python3 freecad_bridge_client.py cmd '{json}'` přes bash a parsuje
stdout. Protokol už JSON-RPC-ish je.

- [x] **4.1** `agentsmith_mcp.py` (protokol, bez I/O — testovatelný) +
      `mcp_server.py` (stdio smyčka + socket). 41 tool definic s uzavřenými schématy
      (`additionalProperties: false`, takže překlep v názvu argumentu odmítne klient,
      ne až model uprostřed dílu) a `readOnlyHint` zrcadlící `read_commands` bridge.
      Test hlídá, že katalog i read-only příznaky **odpovídají dispatch tabulce**
      v `BridgeGui.py` — jinak by se rozešly stejně jako kdysi harness s eval runnerem.
- [x] **4.2** Zapínatelné zaškrtávátkem **MCP** v panelu (výchozí vypnuto).
      Konfigurace se píše per-task do `<projekt>/.agentsmith/mcp.json`, ne globálně:
      běh nesmí uživateli nechat v konfiguraci viset server a dvě instance FreeCADu
      se nesmí přetahovat o jednu registraci. claude dostane `--mcp-config` +
      `--strict-mcp-config` (aby worker viděl jen nástroje FreeCADu), codex dotted
      TOML override přes `-c`. copilot per-invocation MCP flag nemá → poznámka do logu
      a běží dál přes CLI klienta.
- [x] **4.3** Pokyn „preferuj MCP nástroje" jde do **kontextu úlohy**, ne do harness
      `.md`: harness sdílí eval runner, který jede přes CLI klienta, takže fork
      souborů by vyrobil přesně tu tichou divergenci promptů, která už tenhle projekt
      jednou pokousala. CLI klient zůstává jako fallback i pro `eval/`.
- [ ] **4.4** A/B proti baseline z fáze 3 — **čeká na živý test s uživatelem**.

**Akceptace:** ✅ 24 testů (handshake, katalog vs. bridge, chybový bridge = čitelná
chyba nástroje místo mrtvé session, stdio transport) · ⏳ **netestováno naživo** —
e2e sada musí přes MCP projít se stejnou nebo lepší pass-rate a měřitelně nižší
spotřebou tokenů. Když ne, MCP se nenasazuje; proto je výchozí stav vypnuto.

---

## Fáze 5 — smyčka kvality *(kód hotov; 5.4 čeká na baseline)*

- [x] **5.1** Smyčka běží, **dokud se nálezy mění**, a zastaví se na *konvergenci* —
      když reviewer začne opakovat totéž (`agentsmith_review.autofix_decision`).
      Jedno kolo bylo bezpečné číslo, ne správné: je málo, když oprava odhalí další
      problém, a jakékoli pevné N je moc, když to model prostě neumí — pak jen spálí
      celý rozpočet na zopakování téže stížnosti. Limit kol (výběr 1/2/3/5, default 2)
      je jen pojistka. **Čísla se při porovnávání nálezů zahazují**: „stěna má 1,8 mm"
      a „stěna má 1,9 mm" je tentýž neopravený defekt, porovnávání číslic by neúspěšnou
      opravu četlo jako pokrok. Každé zastavení řekne důvod — tichý stop vypadá jako chyba.
- [x] **5.2** Eskalace: volba modelu pro opravné kolo (per backend, ukládá se).
      Model, který na dílu jednou selhal, dostane posilu místo výzvy, ať to zkusí líp.
- [x] **5.3** `_run_prechecks` spustí `validate`, `check_solid`, `model_digest`,
      `feature_probe` a `print_readiness` strojově a reviewer dostane hotový report
      s pokynem **neplýtvat rozpočtem na jejich opakování**; jeho checklist se zúžil
      na porovnání se zadáním a referencí. Příkaz, který selže, se hlásí jako selhaný —
      mlčení by se četlo jako „v pořádku".
- [ ] **5.4** Změřit proti baseline (fáze 3) — **čeká na živý test s uživatelem**.

**Akceptace:** ✅ 26 testů (konvergence, důvody zastavení, precheck report,
strážci zapojení v panelu) · ⏳ **netestováno naživo** — pass-rate nesmí klesnout,
počet kol na úspěch klesnout nebo zůstat, náklady reviewera klesnout.

---

## Fáze 6 — lessons pipeline *(hotovo)*

`lessons.md` je nejcennější soubor harnesu, ale plní se ručně — takové procesy se
zadrhnou.

- [x] **6.1** Tlačítko **„Zapsat lekci"** se aktivuje po `VERDICT: CONCERNS`
      **i po selhané úloze**. To druhé je důležitější: na selhané úloze reviewer
      vůbec neběží, takže nejbohatší selhání (rollback, timeout, zásah guardu) by
      se do `lessons.md` jinak nikdy nedostala.
- [x] **6.2** Krátký běh backendu navrhne záznam; panel ho ukáže v dialogu a **bez
      potvrzení se nezapíše nic** (výchozí tlačítko je Zrušit). Prompt agentovi
      výslovně **dovoluje odmítnout** (`NO LESSON`): pipeline, která musí pokaždé
      vyrobit pravidlo, si ho vymyslí — a vymyšlené pravidlo v souboru, který
      modelovací agent bere jako závazný, je horší než prázdný soubor.
      `extract_entry` vytáhne záznam i z prózy nebo code fence, ať se neplatí
      druhý běh za formátování.
- [x] **6.3** `validate_entry` hlídá tvar hlavičky, ISO datum, přítomnost pravidla
      (bez něj je to anekdota), popis incidentu, délku a **kolizi čísel** — nové
      číslo se bere jako *nejvyšší+1*, ne počet záznamů, protože playbooky citují
      lekce podle čísla a mezera po promované lekci se nesmí recyklovat.
      Výhrady se ukazují uživateli **před** rozhodnutím, nezápis netiší.

**Akceptace:** ✅ 30 testů (parsování reálného `lessons.md`, validace, číslování,
odmítnutí, strážci zapojení) · ⏳ **netestováno naživo** — zbývá provést jedno
reálné selhání celou cestou od tlačítka po zápis. Kandidáti čekají: nominální bore
místo podměry a viditelná konstrukční tělesa z etapy 1.

---

## Fáze 7 — dokončení výrobní cesty *(hotovo; 7.4 čeká na profily od uživatele)*

- [x] **7.0** *(nad rámec plánu)* **Materiál je vlastnost cívky, ne stroje.** Config
      měl u obou tiskáren natvrdo PETG, ale v CORE One je teď PLA. Každá tiskárna má
      nově mapu `filaments` (pla/petg), `filament` = právě zavedený a `loaded_filament`
      ho pojmenovává; `slice_check.py --filament petg` umí slicovat pro jiný materiál
      bez editace souboru. Neznámý materiál **hlasitě spadne** a vypíše dostupné —
      tiché naslicování pro špatný materiál by vyrobilo věrohodný G-code, který zničí tisk.
- [x] **7.0b** PLA profil ověřen ostrým slicem (20mm kalibrační kostka, CORE One):
      `filament_type: PLA`, tryska 220 °C, 3,73 g, 100 vrstev, bez podpor.
      Pozn.: „first layer 30m 32s" hlásí sám slicer; parser je věrný, nepřepisuju ho.
- [x] **7.1** `network.host` vyplněn: CORE One je na **192.168.0.170**. Předchozí
      skeny ji minuly kvůli chybě v metodě — `nmap` nejdřív pinguje a hosty bez
      odpovědi přeskočí, a CORE One na ICMP neodpovídá; s `-Pn` se objevila hned.
      Identita potvrzena `401` na `/api/v1/info` (podpis PrusaLinku bez klíče).
      **Zbývá `api_key`** z displeje tiskárny (Settings → Network → PrusaLink).
      QIDI na portu 7125 neodpovídá = vypnutá.
- [x] **7.1b** Klíč (PrusaLink heslo) uložen v `~/.config/agentsmith/prusalink-core_one.key`
      (chmod 600); config na něj jen odkazuje přes `api_key_file`. Tajemství nepatří
      do souboru, který je v gitu a na privátním remote.
- [x] **7.2** Smoke test `--status` i `--send` proti živé tiskárně. Chybové cesty
      ověřeny (bez hosta = exit 2 s návodnou hláškou, chybějící soubor = exit 2).
      Nahráno `cable_clip.core_one.gcode` i `clip1.gcode` na USB; **tisk nespuštěn**.
- [x] **7.2b** *(nad rámec plánu, na základě pravidla od uživatele)* **Pre-flight
      před tiskem.** `--send` čte trysku, materiál a model tiskárny z hlavičky
      **G-code** (ne z configu — konfigurák popisuje záměr, soubor popisuje to, co
      tiskárna opravdu vykoná) a porovná je s tím, co hlásí sama tiskárna
      (`/api/v1/info` → tryska, `/api/printer` → `telemetry.material`). Neshoda
      **blokuje `--start`** (obejít jen `--force`); nahrání zůstává povolené, protože
      je vratné. Hodnota, kterou tiskárna nehlásí, se hlásí jako *neověřená*, nikdy
      jako shoda — „nezkontrolováno" a „v pořádku" jsou různé odpovědi. Ověřeno živě:
      G-code PLA/0.4 vs. tiskárna PLA/0.4. Pravidlo je i v harnesu
      (`manufacture.md` krok 4, `lessons.md` L6) + 17 testů (`tests/test_preflight.py`).
- [x] **7.2c** *(nad rámec plánu)* PrusaLink neumí spolehlivě přepsat existující
      soubor — `Overwrite: ?1` vrací holé `500`. Iterace na modelu znamená nahrávat
      totéž jméno pořád dokola, takže upload nově zkusí soubor smazat a nahrát znovu;
      retry jen po **úspěšném** smazání, jinak by druhý PUT jen zopakoval tutéž 500 a
      zakryl důvod. `409` (soubor je na tiskárně vybraný) i překročený 8.3 limit
      názvu mají teď vlastní návodnou hlášku.
- [x] **7.3** Cesta „zadej a čekej u tiskárny, co vypadne" **funguje** — uzavřeno
      na rozhodnutí uživatele. Poctivě: ověřená je celá cesta *model → slice →
      pre-flight → upload → fyzický tisk* (dvakrát reálně vytištěno), poslední dva
      kroky ale spouštěl můj shell, ne chatová úloha. Chatový pokus z 17:15 selhal
      na zastíněném configu (fáze 12) a od jeho opravy už chatový běh do historie
      nepřibyl. Kód je stejný v obou případech, netestovaný zůstává jen ten poslední
      článek: že si o tisk řekne agent sám.
- [ ] **7.4** `ston_wolf`: doplnit profily, až budou; do té doby ponechat
      `unconfigured` (chování je správné — nabídne ostatní stroje).

**Akceptace:** ⏳ z chatu vznikne G-code a doletí na tiskárnu bez ručního zásahu.
Slicovací i síťová polovina jsou hotové a ověřené proti živé CORE One (PLA, 0.4);
výrobní cesta je uzavřená (viz 7.3). **Fyzické spuštění tisku si vyžádá potvrzení** — nahrání souboru
je vratné, roztočený tisk ne.

---

## Fáze 8 — refaktor `BridgeGui.py` *(hotovo)*

`BridgeGui.py` měl **4 125 řádků** a mísil dvě nesouvisející věci: socketový most do
živého dokumentu a Qt panel. Rozděleno na čtyři moduly:

| modul | řádků | co dělá |
|---|---|---|
| `BridgeGui.py` | **1 066** | sestavení panelu, nastavení, historie, registrace příkazu |
| `bridge_server.py` | 1 493 | socket, ~45 příkazů, observer, geometrické pomocníky |
| `task_supervisor.py` | 1 249 | běh úlohy: backend, watchdog, rollback, reviewer, autofix, lekce |
| `printer_panel.py` | 429 | objevování tiskáren, zapamatovaný seznam, monitoring |

- [x] **8.1** Rozděleno. `task_supervisor` je **mixin**, ne samostatný objekt: ty metody
      *jsou* panel — čtou jeho widgety a píšou do jeho popisků na každém druhém řádku —
      takže předávat jim odkaz na něj by koupilo jen druhé jméno pro `self`. Co dělení
      koupilo, je čitelnost: sestavování rozhraní a řízení úlohy jsou teď oddělitelné.
- [x] **8.2** `InitGui.py` nebyl dotčen, past z rebrandu 0.15.0 tedy nehrozí.
- [x] **8.3** **Ruční checklist nahrazen skriptem** `tests/gui_smoke.py` (spouští se
      uvnitř FreeCADu). 25 kontrol: konstrukce panelu, MRO, navázání všech 60 metod
      mixinu, volání přes hranice modulů, start/stop bridge, tři úrovně přístupu,
      discovery soubor, checkpoint/validate/undo/redo. **25 ok, 0 failed.**
      Ruční checklist je checklist, který nikdo nezopakuje.
- [x] **8.4** Interní identifikátory (`codex_task`, `_run_codex`) **ponechány** —
      přejmenování je čistě kosmetické a v jednom refaktoru s přesunem 33 metod by
      znemožnilo rozeznat, co co rozbilo.
- [x] **8.5** `HARNESS_DIR` skončil po přesunu ve dvou souborech; přesunut do
      `agentsmith_harness`, kam patří. Dvě cesty, které musí být totožné, takhle
      začínají utíkat.
- [x] **8.6** Testy, které četly zapojení panelu ze zdrojáku, měly natvrdo
      `BridgeGui.py` a refaktor je rozbil, přestože se chování nezměnilo. Nově hledají
      přes `helpers.gui_source()` napříč GUI moduly.

**Akceptace:** 398 unit testů zelených, GUI smoke 25/25, panel se ve FreeCADu otevře.
**Neověřeno:** ostrý běh úlohy přes přepracovaný panel. `run_e2e` k tomu nestačí —
obchází panel a mluví s mostem přímo, takže by mixin vůbec nespustil. Otestuje to
až první skutečná úloha z chatu.

**Vlastní chyby po cestě** (všechny odhalil až smoke test, ne překlad ani unit testy):
`deque` se ztratil při čištění importů — kontroloval jsem *nepoužité importy*, ne
*použité názvy bez importu*. A třikrát jsem v smoke testu tvrdil něco o API, které
jsem si nepřečetl; naposledy jsem `(popisek, text)` z `assemble_harness` četl jako
„harness má dva znaky" a málem hlásil poplach. Odtud i oprava README: playbooků je
**18**, ne 20 — to číslo jsem „ověřil" spočítáním `.md` souborů, mezi nimiž jsou dva
trvale vkládané soubory, které playbooky nejsou.

---

## Fáze 9 — objevování tiskáren v panelu *(hotovo)*

- [x] **9.1** `printer_discovery.py` — mDNS/DNS-SD čistě přes stdlib (FreeCAD má
      vlastní Python, uživateli do něj nelze nic doinstalovat). **Výchozí je pasivní
      mDNS**, aktivní sken jen na vyžádání: proklepnout 254 adres je port scan, který
      je doma neškodný, ale ve firemní síti spustí IDS. Zaškrtávátko to říká narovinu.
- [x] **9.2** Změřeno na živé síti, ne odhadnuto:
      - služba se jmenuje **`_prusalink._tcp`** (ne `_prusa-link`, jak jsem tipoval);
      - responder odpoví na **jednu otázku v paketu** a zbytek ignoruje → dotazy se
        posílají po jednom v kolech, jinak to vypadá jako prázdná síť;
      - RFC 6762 unicast bit (QU) vypadal jako elegantní řešení a vrátil **nic** —
        zůstal obyčejný multicast dotaz;
      - jedno kolo ze čtyř zůstane bez odpovědi → opakování uvnitř jednoho hledání
        (6/6 úspěšných po opravě). Mizející tiskárna v panelu vypadá jako vypnutá.
- [x] **9.3** **Identita se určuje z odpovědi, ne z portu.** QIDI X-Max 3 má otevřený
      port 80 a byla hlášená jako Prusa, dokud jsem nezačal číst, co vlastně
      odpovídá. Sken si drží *všechny* otevřené porty (Klipper má web na 80 a API na
      7125) a každý stroj se dotáže všech dialektů, kterým rozumím. Důkazy mají
      pořadí: rozpoznaná odpověď > 401 (něco tam je a brání se) > 404.
      Nerozpoznané zařízení **nedostane jmenovku** — router v seznamu tiskáren byl
      přesně tenhle omyl.
- [x] **9.4** Čtyři nezávislé stavy místo jedné zelené kontrolky: *nalezena /
      odpovídá (chybí klíč) / připojeno / nedostupná*, a zvlášť **„umím slicovat"** —
      nalezená tiskárna bez profilu není použitelná, i když je online.
- [x] **9.5** Nakonfigurovaná tiskárna s adresou se **rovnou zeptá**, nečeká se, až
      se ohlásí; Moonraker mDNS ve výchozím stavu neinzeruje, takže QIDI i Wolf
      svítili „nedostupná", přestože odpovídali na každý dotaz.
- [x] **9.6** Panel `PrinterListWidget` + `PrinterScanWorker` (hledání na vlákně, aby
      GUI nezamrzlo), tlačítko Hledat/Zrušit, sken s průběhem. Ověřeno v běžícím
      FreeCADu proti **třem** strojům: CORE One (PrusaLink), QIDI X-Max 3 a Ston WOLF
      (oba Moonraker) — všechny „připojeno".
- [x] **9.7** Doplněny adresy do configu: QIDI `192.168.0.71:7125`,
      Ston WOLF `192.168.0.10:7125`. 48 testů (`tests/test_printer_discovery.py`),
      wire formát se testuje proti **skutečně zachyceným paketům**
      (`tests/fixtures/mdns_responses.jsonl`).

**Akceptace:** ✅ panel ukazuje známé i objevené tiskárny a poctivě rozlišuje
„je tam" od „umím ji použít".

- [x] **9.8** **Zapamatované tiskárny** (`printer_book.py`, 30 testů). Vlastní soubor
      `~/.config/agentsmith/printers.json` — odděleně od `slicer-config.json`, který
      je v gitu a popisuje, pro co umí projekt slicovat; tenhle popisuje, co uživatel
      vlastní. Klíčové rozhodnutí: **adresa není identita.** `192.168.0.170` je doma
      tiskárna a jinde může být cizí NAS, takže se u každého stroje ukládá i otisk
      (hostname/model/serial — *ne* verze firmwaru, ta se mění updatem) a
      zapamatovaná tiskárna se prohlásí za přítomnou, až když otisk sedí. Při neshodě
      řádek zčervená a **stav spadne na „nedostupná"** — rozsvítit zeleně cizí
      zařízení s tlačítkem Tisknout by bylo horší než si nepamatovat nic.
- [x] **9.9** Seznam **nezávisí na aktuální síti.** Přenesení notebooku jinam seznam
      nevyprázdní: tiskárny zůstanou s datem posledního spatření. Identita se drží
      hostnamem, ne adresou, takže ani přeskočení DHCP leasu nezaloží druhý záznam.
      `last_network` se ukládá jen jako informace, nikdy jako filtr.
- [x] **9.10** **Ruční přidání podle IP** (i vypnuté tiskárny — zůstane nedostupná,
      dokud se neozve) a **přejmenování** (dvojklik nebo pravé tlačítko → Přejmenovat
      / Zapomenout). Jméno od uživatele přebíjí popisek z configu i z tiskárny a
      **opakované hledání ho nikdy nepřepíše**. Ručně přidaný záznam se sám povýší,
      jakmile se stroj představí. Ověřeno v běžícím FreeCADu: přejmenování přežilo
      další hledání, vypnutá tiskárna zůstala v seznamu.

- [x] **9.11** **Přidat do konfigurace** (pravé tlačítko na objeveném stroji).
      Zapíše se jen síťová část; slicovací profily zůstanou prázdné a `status`
      „unconfigured" — vymyslet názvy profilů by znamenalo vyrobit věrohodný G-code
      pro stroj, který je nikdy neměl. Do doplnění zůstane „Umím slicovat: ne".
      Kolize klíče nepřepíše existující záznam, tajemství se do souboru nepíše.
- [x] **9.12** **Bambu přes SSDP** — pasivní naslouchání na 2021/1990, parsování
      `DevModel.bambu.com` & spol. **Neověřeno proti skutečnému stroji** (žádný tu
      není): testuje se syntetický paket v dokumentovaném tvaru a chybová větev míří
      bezpečným směrem — co parser nepozná, vrátí `None`, takže špatný odhad formátu
      znamená „nic jsem nenašel", ne přízrak v seznamu. Nalezená Bambu se **needotazuje
      přes HTTP** (její LAN rozhraní je MQTT/FTPS) a je poctivě označená jako
      neobsloužitelná — ne s tlačítkem Tisknout.

---

## Fáze 10 — hlasové zadání promptu *(hotovo)*

Rozhodnuto: **lokální whisper.cpp**, ne cloud — do promptu se dostávají detaily o
tom, co člověk staví, a tiše posílat zvuk z mikrofonu třetí straně není výchozí
chování, které by plugin měl mít.

- [x] **10.1** Nahrávání přes externí nástroj: **FreeCAD 1.1.1 nemá QtMultimedia**
      (PySide 6.8.3, modul hlásí ImportError — ověřeno, ne odhadnuto), takže
      `pw-record` / `arecord` / `ffmpeg` / `sox` podle toho, co je k dispozici;
      ffmpeg si bere vstup podle platformy (alsa/avfoundation/dshow). Ověřeno
      skutečným nahrávkou: mono 16 kHz, 1,94 s. Tlačítko se přepíná na „⏹ Zastavit"
      a status **bliká `● nahrávám…`** — co umí poslouchat, musí být vidět, že poslouchá.
- [x] **10.2** `agentsmith_voice.py` (29 testů). Chybějící kus se nehlásí jako „hlas
      nefunguje", ale konkrétně: chybí nahrávací nástroj / chybí whisper.cpp / chybí
      model — tři různé problémy se třemi různými návody. Model se navíc nenabízí,
      dokud není whisper (posílat člověka stahovat model pro program, který nemá, je šum).
- [x] **10.3** Modely s **poctivými velikostmi**; `tiny`/`base` se schválně nenabízejí,
      protože češtinu přepisují tak, že by to vypadalo jako rozbitá funkce.
      Doporučený `large-v3-turbo-q5_0` má **547 MB**, ne 1,5 GB — kvantovaný je třetinový
      při prakticky stejném výsledku. Stahování jen na klik, s průběhem, a soubor se
      přejmenuje **až po dokončení** (napůl stažený model vypadá jako nainstalovaný).
- [x] **10.4** Přepis se **připojí do pole a nic se neodesílá**. Ověřeno v běžícím
      FreeCADu s podstrčeným whisperem: text dorazil, `[BLANK_AUDIO]` se cestou
      odstranilo, proces agenta zůstal `NotRunning` a nahrávka se smazala.
      Zvuk nikam neodchází — celý přepis běží lokálně.

- [x] **10.5** **whisper.cpp nainstalován a čeština ověřena.** Ubuntu 24.04 ho v
      balíčcích nemá → build ze zdrojů bez `sudo` do
      `~/.local/share/agentsmith/whisper.cpp`, symlink `~/.local/bin/whisper-cli`.
      Model `large-v3-turbo-q5_0` (574 MB) v `~/.local/share/agentsmith/whisper`.
      Test češtiny: věta vygenerovaná TTS a zachycená ze zvukového výstupu →
      *„Udělej držák na kabel, průměr dvacet dva milimetrů, tloušťka stěny tři
      milimetry."* přepsáno jako *„Udělej dožák na kabel. Průměr 22 mm. Tloužka
      stěny 3 mm."* — dvě chyby ve dvanácti slovech, **čísla správně převedená na
      číslice** (pro modelovací prompt ideální). Pozn.: syntetický hlas je pro
      rozpoznávání těžší než lidský, takže tohle je spodní odhad.
- [x] **10.6** **Whisper na tichu halucinuje** — měřeno: půl sekundy digitálního ticha
      vyrobilo větu „Titulky vytvořil JohnyX." Vymyšlený příkaz v poli promptu je
      nejhorší možné selhání téhle funkce, takže se před spuštěním modelu měří RMS
      nahrávky a ticho se **odmítne, ne přepíše** (práh 60; naměřeno: tichá místnost 0,
      diktovaná řeč ~4300). Ověřeno v panelu s ostrým modelem: nahrávka bez řeči
      nechala prompt nedotčený a napsala „nic jsem neslyšel".
- [x] **10.7** Změřený rozpad času: načtení modelu jen **318 ms**, všechno ostatní je
      **enkodér — 24,5 s**, a ten jede na pevném 30s okně bez ohledu na délku nahrávky
      (0,5 s ticha stojí stejně jako 8 s řeči). Vláken **max 8**: na 16 jádrech trvalo
      8 vláken 26 s, 16 vláken 43 s — přes určitou mez si šlapou po paměťové propustnosti.
      `medium-q5_0` je rychlejší (17,4 s) při srovnatelné kvalitě.

- [x] **10.8** **Vulkan build — 10× rychleji.** Enkodér **24,5 s → 2,36 s**, celý
      přepis **25,9 s → 3,4 s**, a výstup **znak po znaku stejný**. GPU: AMD Radeon 780M
      (RADV PHOENIX) s maticovými jednotkami (`KHR_coopmat`), odtud ten skok.
      Build v `build-vulkan/` vedle CPU buildu, symlink `~/.local/bin/whisper-cli`
      ukazuje na Vulkan verzi; CPU build zůstal jako záloha.
      Cestou dvě překážky: chyběly **SPIRV-Headers** (nainstalovány lokálně do
      `~/.local/share/agentsmith/deps`, jsou to jen hlavičky, žádný `sudo`) a jejich
      include path se nepropsala do překladu (dořešeno `-DCMAKE_CXX_FLAGS=-I…`).
      Systémově bylo potřeba jen `libvulkan-dev` + `glslc` (instaloval uživatel).
- [x] **10.9** **Mikrofon na tomhle stroji je ztlumený** (výchozí zdroj „Digital
      Microphone", `MUTED`) — proto všechny nahrávky z mikrofonu měly RMS přesně 0.
      Neodtlumoval jsem ho: ztlumený mikrofon bývá vědomé rozhodnutí. Místo toho
      hláška rozlišuje **přesnou nulu** (nic k nahrávači nedorazilo → „vypadá to, že je
      vstup ztlumený") od **tichého pokoje** (nízké RMS, ale šum tam je) — živý
      mikrofon vždycky něco zachytí, takže plochá nula je jiný problém než „nemluvil jsi".

**Akceptace:** ✅ prompt jde nadiktovat, česky, s ostrým modelem, za ~3,4 s.
**Neověřeno:** diktát skutečným hlasem přes mikrofon — ten je na tomhle stroji
ztlumený. Celý řetězec je ověřený až po vstup zvuku; chybí poslední článek.

---

## Fáze 11 — hlídač zabíjel správnou práci *(hotovo)*

Nadiktované zadání „háček na pověšení ručníku" po ~3 minutách skončilo `failed`,
model se vrátil zpět a uživateli to připadalo jako pád. Pádem to nebylo:

```
reason:  Task repeatedly changed ProfileSketch more than 180 times
exit_code: 143 (SIGTERM od supervisora)   restored: true   changed: false
```

- [x] **11.1** **Diagnóza.** Hlídač počítá události na objekt a nad 180 běh zabije.
      Jenže skica se *staví* po jedné události — čára, vazba, čára, vazba — takže
      počet událostí měří **podrobnost objektu, ne zaseknutí agenta**. Ten běh měl
      626 událostí celkem, tedy nikde poblíž skutečné smyčky.
- [x] **11.2** **Tohle byl druhý výskyt téhož.** V kódu byl komentář popisující, jak
      stejný práh zabil korektní běh na spreadsheetu `Parameters` — a řešením tehdy
      byla výjimka jen pro spreadsheety. Skica je tentýž případ pod jiným názvem typu.
      Přidat třetí výjimku by byla další rána do stejného místa.
- [x] **11.3** **Oprava.** Rozhodnutí přesunuto z GUI (kde ho nemohl chytit žádný test)
      do `agentsmith_supervision.mutation_violation()`. Typy, které se staví
      inkrementálně (`Sketcher::`, `Spreadsheet::`) mají strop 1500, ostatní 600
      (bylo 180). Skutečným hlídačem zůstává **celkový strop 3000** — nekonečná
      smyčka ho dosáhne během chvíle, kdežto žádná legitimní stavba se k němu
      nepřiblížila. Per-objektový počet zůstává jen jako předčasné varování hodně
      vysoko nad tím, co reálný díl potřebuje.
- [x] **11.4** 9 testů s **čísly z toho skutečného běhu** (626 událostí, sketch přes
      180). Obě předchozí falešná zastavení se našla tak, že uživatel přišel o
      několik minut správné práce — to je drahá náhrada za test.

**Cena omylu:** ten běh stál 0,86 USD a model byl vrácen zpět, takže je pryč.
Zadání je potřeba pustit znovu.

---

## Fáze 12 — zastaralá kopie configu blokovala tisk *(hotovo)*

Model háčku vznikl správně, ale tisk se nekonal. Agent to odmítl a **měl pravdu**:
hlásil, že CORE One nemá PLA profil a nemá vyplněnou síťovou adresu — obojí přitom
bylo nastavené o hodiny dřív.

- [x] **12.1** **Diagnóza.** Panel kopíruje `slice_check.py`, `print_send.py` a
      `slicer-config.json` do pracovní složky. Config byl z opatrnosti označen jako
      „uživatelův, nikdy nepřepisovat". Jenže kopírované nástroje hledají config
      **vedle sebe**, takže kopie z minulého týdne trvale zastínila ten pravý.
      Nic nespadlo, jen odpověď byla tiše špatná — proto to nikdo neodhalil dřív.
- [x] **12.2** **Oprava.** Config je odvozený artefakt a **obnovuje se vždy**, bez
      porovnání časů. Porovnání času by nestačilo: agent si tu kopii během běhu sám
      přepíše, čímž se zastaralý soubor stane tím novějším — přesně ve chvíli, kdy na
      tom záleží. Rozhodnutí přesunuto z GUI do
      `agentsmith_harness.should_refresh()` + 8 testů.
- [x] **12.3** **Orientace pro tisk změřena, ne odhadnuta.** Model vyexportován v šesti
      polohách a všechny naslicovány. Vítěz **rotY90: 1 h 2 min, 16,3 g, 137 vrstev,
      podpory 17+8** — proti 64 / 129 / 153 / 198 podpůrným úsekům u ostatních.
      Navíc leží profil naplocho, takže vrstvy jsou rovnoběžné se zatížením a rameno
      se ohýbá uvnitř vrstvy, ne přes spoj vrstev. Cena: dvě průchozí díry Ø5,5 se
      tisknou vodorovně (malé přemostění, u díry pro dřík šroubu bez významu).
- [x] **12.4** **Vlastní chyba v měření.** První srovnávací tabulka četla z JSONu
      klíč `supports`, který neexistuje — správně je `supports_generated` — a hlásila
      proto „podpory: ne" u všech orientací. Tabulka se přepočítala; závěr vyšel
      stejný, ale z jiného důvodu (nejméně podpor, ne žádné podpory).
- [x] **12.5** Vytištěno: job #5, `towel_hook.core_one.gcode`, pre-flight PLA/0.4
      proti PLA/0.4 prošel.

---

## Fáze 13 — panel se otevírá s tiskárnami, ne prázdný *(hotovo)*

Tiskárny se sice pamatovaly, ale panel je při otevření nezobrazil, dokud uživatel
nezmáčkl Hledat. Tím se celé pamatování zahodilo: stroje byly známé, neznámý byl
jen jejich aktuální stav.

- [x] **13.1** Panel při otevření **okamžitě vypíše zapamatovaný seznam z disku**
      (`printer_book.cached_rows`), se stavem „zjišťuji…". Ověřeno v běžícím
      FreeCADu: seznam je vidět **0,7 s** po otevření, bez jediného kliknutí.
      Řádky přitom netvrdí nic, co nebylo ověřeno — žádná tiskárna není „připojeno"
      ani „umím slicovat", dokud se opravdu neozve.
- [x] **13.2** Stav se doplní **na pozadí a jen u známých adres** (`known_only`) —
      žádné mDNS kolo, žádný sken. Hledání nových strojů zůstává za tlačítkem.
      Když jsou tiskárny online, doplní se stav za **0,14 s**.
- [x] **13.3** Dotazy paralelně. Cenu platí **vypnuté** tiskárny (HTTP timeout na
      každý zkoušený dialekt), ne ty odpovídající. Změřeno se třemi živými a třemi
      mrtvými: **6,3 s paralelně proti 18,4 s po jedné.** (V komentáři jsem měl
      původně „půl minuty" — nahrazeno naměřenou hodnotou.)
- [x] **13.4** 3 testy na to, že se seznam dá vykreslit bez sítě a že netvrdí
      připravenost, kterou nikdo neověřil.

---

## Fáze 14 — průběžný monitoring tiskáren *(hotovo)*

- [x] **14.1** Panel kontroluje zapamatované tiskárny **každých 30 s** na pozadí.
      Ptá se jen známých adres — žádné mDNS kolo, žádný sken; hledání nových strojů
      zůstává za tlačítkem. Tik se **přeskočí**, když už běží něco, co spustil
      uživatel, a když panel není vidět (dotazovat se za skrytý widget je čirá ztráta).
- [x] **14.2** Aktualizace je **tichá**: nemění text tlačítka ani stav, neblikají
      hlášky a hlavně se **neztrácí výběr ani pozice posuvníku** — na řádek se dá
      klikat pravým tlačítkem, aniž ho tik pod rukama přemaže.
- [x] **14.3** Stav se ukládá do knihy (`last_state`, `last_state_at`, `can_slice`).
      **„Naposledy viděna" znamená viděna, ne hledána** — nedostupnost čas
      posledního spatření neposouvá.
- [x] **14.4** Souhrn stavu jde do **kontextu úlohy pro agenta**, takže „vytiskni to
      na Prusovi" nezačíná objevováním flotily od nuly a vypnutá tiskárna je známá
      jako vypnutá dřív, než s ní úloha začne počítat. Měření starší než **180 s**
      se hlásí jako **„stav neznámý"**, nikdy jako platné — tiskárna prohlášená za
      připravenou na základě hodinu staré kontroly je horší než žádná informace,
      protože vypadá ověřeně.
- [x] **14.5** Stavový řádek panelu říká rovnou to podstatné: *„3 tiskáren,
      2 připraveno k tisku"* — připraveno = připojena **a** máme pro ni profil.
- [x] **14.6** 9 testů (zápis stavu, zastarávání, nikdy nekontrolovaná tiskárna,
      rozbité časové razítko). Ověřeno i živě v běžícím FreeCADu přes dva tiky.

---

## Fáze 15 — README a přenositelnost *(hotovo)*

- [x] **15.1** README přepsáno: co to dělá, **jaké jsou závislosti a proč**
      (povinné / slicování / hlas / MCP), instalace krok za krokem včetně
      whisper.cpp a volitelné Vulkan akcelerace, používání panelu, tiskárny,
      rozvržení repozitáře, kam se co zapisuje mimo repo, a troubleshooting
      tabulka postavená na chybách, které během vývoje reálně nastaly.
- [x] **15.2** **Cesta k OrcaSliceru byla natvrdo `/home/robert/AppImages/…`** —
      addon tím byl pro kohokoli jiného tiše nepoužitelný ke slicování. Nahrazeno
      hledáním v obvyklých adresářích (`~/AppImages`, `~/Applications`,
      `~/.local/bin`, `~/Downloads`, `/opt`, `/usr/local/bin`) s možností přebít
      to `orca_appimage` v configu. Našlo se to **při psaní návodu k instalaci** —
      dokumentace je nečekaně dobrý test toho, jestli jde software vůbec nainstalovat.
- [x] **15.3** Test hlídá, že se do addonu nevrátí absolutní cesta do domovského
      adresáře, plus testy hledání AppImage. Ověřeno: v addonu už `/home/…` není nikde.
- [x] **15.4** Tvrzení v README ověřena proti repozitáři — existence všech
      zmíněných souborů, počet playbooků (20), velikost VAD modelu (885 kB),
      všechny uváděné přepínače CLI a názvy env proměnných.

---

## Fáze 16 — rozměry z reality a spoje, které drží *(hotovo)*

Z praxe: *„Když tomu řeknu aby udělal držák na kuchyňské utěrky, tak 250 mm asi
nebude stačit."* Změřeno — česká role **~230 mm**, německá **~260 mm**, americká
11″ = **279 mm**, průměr 105–150 mm. Do 250 mm se dvě ze tří variant nevejdou.

- [x] **16.1** **Diagnóza je stejná jako u L8:** playbook o rešerši v harnesu **byl**,
      jen se nespustil. Jeho spouštěč zněl „když spec odkazuje na objekt, jehož rozměry
      je třeba dohledat" — a u kuchyňské role si model řekne, že tu zná. Právě u
      všedních předmětů sebejistý odhad selže, protože nic nenutí ke kontrole.
- [x] **16.2** Pravidlo přesunuto z podmíněného playbooku do **`core.md`**, kde ho nejde
      minout: sekce „Before any geometry: what does it hold?" + DoD bod 8. Cokoli díl
      drží nebo do čeho zapadá, dostane rozměry **i se zdrojem dřív, než vznikne první
      geometrie** — a dimenzuje se na **největší běžnou variantu plus vůli**, s uvedením,
      pro kterou variantu je díl navržený.
- [x] **16.3** Nový playbook **`reference-dimensions.md`**: tabulka běžných předmětů
      (papírové role, A4, spojovací materiál, ložiska, Pi/Arduino, 18650, kabely) —
      **rozsahy, ne konstanty**, se zdroji a s poznámkou, kde se prameny neshodly.
      Registr má nově 19 playbooků.
- [x] **16.4** **Spoje.** `assembly.md` §3 dostal tabulku „co to drží / co brání
      uvolnění a pootočení" a dvě konkrétní pasti: *dotýkají se ≠ drží* a *díl na jednom
      šroubu je pant*. Nová §3b o **závitové tyči jako výztuze** — plast je slabý mezi
      vrstvami, ocelová tyč z toho dělá problém oceli. DoD bod 9 to vyžaduje u každé
      vícedílné sestavy.
- [x] **16.5** `fasteners.md` §6: u nosných míst sáhnout po **koupeném železe** dřív
      než po vymyšlené geometrii. K **Fasteners workbenchi** poctivá poznámka — je
      užitečný na kontrolu a vizualizaci, ale díry a kapsy modelovat nativně: dokument
      se šrouby z doplňku se u někoho bez toho doplňku správně neotevře.
- [x] **16.6** Lekce **L9** (rozměry) a **L10** (spoje).
- [x] **16.7** Nová zlatá úloha **`kitchen_roll_holder`** — dobrá rada v souboru se
      nedá měřit. Úloha rozměr role **neuvádí** a check `fits_the_largest_common_roll`
      žádá nejdelší rozměr ≥ 285 mm, takže návrh kolem odhadnutých 250 mm neprojde.

**Neověřeno:** jestli to zabere. Úloha existuje, ale ostrým během neprošla — to je
kandidát na příští etapu evalu.

---

## Fáze 17 — reálný spojovací materiál a ozubení *(hotovo)*

Korekce od uživatele: *„tady nejde jen o 3d tisk, tady jde o návrh komplexních
modelů… pro kompletní představu je někdy dobré tam ty šrouby mít."* Moje předchozí
doporučení („šroub se kupuje, netiskne — modeluj jen díry") bylo psané pro tištěný
díl a pro sestavy neplatí. Uživatel doinstaloval **Fasteners** a **Gears**.

- [x] **17.1** Ověřeno, že obojí jede **bez GUI**, tedy že to agent může řídit přes
      `execute_python`. API jsem si nevymyslel, ale vyzkoušel:
      `FastenersCmd.FSScrewObject(obj, "ISO4762", None)` + `Diameter`/`Length`
      (ISO4762 M5×16 → hlava Ø9,2, 550 mm³); typ **`ThreadedRod`** M5×200 funguje
      (3 918 mm³) — přesně ta výztuha z fáze 16; `InvoluteGear(obj)` s vlastnostmi
      `num_teeth`/`module`/`height` (z=20, m=2 → Ø44 mm). Dva mé první odhady API
      byly špatně, správné volání jsem našel ve zdrojácích doplňku.
- [x] **17.2** Nový playbook **`workbenches.md`**: kdy umístit skutečný šroub a kdy
      stačí díra, tabulka užitečných norem z 570 dostupných, ověřené volání, a
      poctivá poznámka — dokument se šrouby z doplňku se bez toho doplňku otevře, ale
      přestanou být parametrické, takže **díry a kapsy modelovat nativně**.
- [x] **17.3** **Ozubení harness prakticky nepokrýval.** Doplněna aritmetika, kterou
      musí model vyslovit *před* geometrií (rozteč d = m·z, osová vzdálenost
      a = m·(z₁+z₂)/2, převod i = z₂/z₁, **stejný modul u obou kol**) a specifika
      tištěných kol: podřezání pod ~17 zubů, **vůle 0,1–0,3 mm** jako parametr (kola
      na jmenovito se neotočí), modul ≥ 1,5 mm, orientace zubů do roviny, a jak je
      kolo zajištěné na hřídeli — volně se točící kolo nepřenáší nic.
- [x] **17.4** Opravena moje formulace ve `fasteners.md`. Registr má 20 playbooků.

**Neověřeno:** že agent ta volání použije. Playbook existuje, ostrým během neprošel.

---

## Fáze 18 — čím to vůbec změřit *(nástroje hotové, měření čeká na FreeCAD)*

Úkol #10 zněl „změřit MCP a smyčku kvality proti baseline". Při pokusu o spuštění
vyšlo najevo, že **to současnými nástroji změřit nejde**: `run_e2e.py` má rovnou
v hlavičce napsáno *„no reviewer/autofix second pass"* a MCP backendu nikdy
nepředával. Obě funkce jsou vlastnostmi **panelu**, který eval obchází.

Postavené jsou tedy dvě fáze projektu, o kterých nikdo neví, jestli pomáhají.

- [x] **18.1** `run_e2e --mcp` — vystaví most jako typované MCP nástroje, přesně jako
      zaškrtávátko v panelu. Konfigurace se píše **do sandboxu úlohy**, ne do
      uživatelova CLI: měřicí běh nesmí nechat za sebou registrovaný server.
      Do záznamu se ukládá `"mcp": true/false` — dva snímky, které se liší jen tím,
      jak byl most vystavený, by jinak nešly rozeznat.
- [x] **18.2** `run_e2e --review [--reviewer-model M]` — po úloze, která nedostala
      plný počet, spustí nezávislého read-only recenzenta a jedno opravné kolo, pak
      **přehodnotí**. Ukládají se **obě známky** (`grade_before_review` i `grade`);
      jedno finální číslo nedokáže ukázat, jestli smyčka něco spravila, nebo byl
      první pokus rovnou dobrý.
- [x] **18.3** Ochrany, aby smyčka neškodila: **spadlý recenzent opravné kolo
      nespustí** (opravovat model podle útržku z havarovaného recenzenta je horší než
      nerecenzovat), prázdný výstup se nebere ani jako souhlas, ani jako vada, a
      z výstupu se přebírají **jen řádky `FINDING:`**, ne recenzentovy úvahy.
- [x] **18.4** 11 testů (`tests/test_e2e_runner.py`) — první testy, které run_e2e
      vůbec měl. Ověřují, že se `--mcp` opravdu propíše do příkazu backendu, že
      konfigurace končí v sandboxu, a všechny čtyři cesty smyčky.
- [x] **18.5** **Změřeno.** FreeCAD s mostem jsem si spustil sám (`host_bridge.py`) —
      čekat na uživatele nebylo potřeba.

### MCP vs. CLI (3 úlohy, 1 běh)

| úloha | CLI | MCP | čas CLI → MCP |
|---|---|---|---|
| `simple_l_bracket` | **8/8** | **7/8** | 551 → 390 s |
| `printed_wall_hook` | 10/10 | 10/10 | 902 → 1059 s |
| `bearing_block_608` | 9/9 | 9/9 | 629 → 961 s |
| **celkem** | **27/27** | **26/27** | |

MCP prokazatelně běželo (v logu `mcp__agentsmith__validate`, `__undo`,
`__spreadsheet_set`), takže se opravdu měřily dvě různé věci. **Přínos se
neprokázal**: jediný rozdíl je L-profil vyrobený o 20 mm kratší, a to o 30 %
rychleji — vypadá to na spěch, ne na nástroje. Zůstává správně vypnuté.
Jeden běh na úlohu je šum; rozdíl jednoho checku ze 27 je v něm utopený.

### Smyčka kvality (injekce vady)

Smyčka se spouští jen pod plným počtem a tři běhy po sobě prošly napoprvé, takže
byla zapojená, otestovaná — a nikdy nepozorovaná při práci. Vada tedy zavedena
schválně (`BracketWidth` 60 → 45 mm):

```
PŘED 7/8 → recenzent: "FINDING: overall length … is 45 mm, not the required 60 mm,
            spreadsheet BracketWidth (B5) = 45 drives BracketPad.Length" → PO 8/8
```
119 s. **Smyčka funguje** — najde konkrétní buňku a opraví ji.

- [x] **18.6** **Dvě vlastní chyby, které měření odhalilo.**
      *(a)* `append_history` psal pevný výběr polí a `mcp` mezi nimi nebyl, takže obě
      větve vypadaly v historii identicky. Doplněno `mcp`, `review`,
      `score_before_review`. Historie **nepřepsána**; MCP běhy vybrány časem.
      *(b)* Parser nálezů četl řádky začínající na `FINDING:`, jenže log je **JSON
      stream** — text je uvnitř řetězce, kde je konec řádku dva znaky. První měření
      smyčky tak zahodilo zcela správný nález a vypadalo, že smyčka je k ničemu.
      Dekódování nově přes tutéž funkci, kterou používá panel.
- [x] **18.7** **Nejzávažnější nález: lekce L8 byla nesmysl.** Check
      `bore_diameter` četl alias `BearingOD` a žádal ≤ 21,95 — jenže `BearingOD` je
      z definice 22 mm, to *je* ten průměr ložiska. Model měl `PressInterference =
      0,075` a `BoreDia = 21,85`, ověřeno i na geometrii. Po opravě aliasu **9/9**.
      Tři „výskyty" byly tři spuštění jednoho rozbitého checku a lekce z nich učila
      agenta opravovat něco, co dělal správně. L8 v `lessons.md` **zrušena i
      s vysvětlením** (tiše smazaná chyba se zopakuje), snímky opraveny se
      zachovaným `score_as_recorded`.

