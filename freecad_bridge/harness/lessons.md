# Lessons from real failures (always in effect)

Distilled, binding rules born from tasks that actually failed or were rejected by the
user. Each entry is one incident → one rule. When a rule matures into a full playbook
section, the entry shrinks to a pointer. Maintainers append entries after every
failed/rejected task; the agent must treat every rule here as non-negotiable.

## L1 — Functional pose before geometry (2026-07-18, wall hook)
A hook was modeled as its side profile extruded flat: every dimension passed, the part
was unusable on the wall. **Rule:** state mounting surface, fastener axes, load vector
and working-feature direction BEFORE modeling, and verify the finished part against
them. → promoted: `model` step 2, `verify` "Functional pose", core DoD item 6.

## L2 — Map a reference image to its projection (2026-07-18, coat hook vesak3)
A reference photo (side profile) was faithfully rebuilt as the FRONT view — silhouette
matched, part was a flat lookalike. Matching one view proves nothing. **Rule:** for
every reference image decide which view it shows, write the three-view description in
mounted pose, and check the working feature actually protrudes along the
away-from-mounting axis. → promoted: `reference` §4b, `verify` projection check.

## L3 — Bridge units are explicit (2026-07-18, wall hook v2)
`mass_properties` was fed density `0.00127` (g/mm³) instead of **kg/m³** → mass came
out ~10⁶× too small and nobody noticed until review. **Rule:** check the unit every
bridge argument expects; sanity-check every computed mass/volume against common sense
(a hand-sized PETG part weighs grams to tens of grams). → promoted: `materials` notes.

## L4 — A finished model must be visible and framed (2026-07-18)
A correct model was delivered invisible; the user saw an empty viewport and read it as
failure. **Rule:** end every task with result bodies Visible (construction inputs
hidden), `fit_view`, screenshot. → promoted: core DoD item 7.

## L5 — Don't forage for infrastructure (2026-07-18)
A reviewer burned budget hunting for `freecad_bridge_client.py` in sibling folders.
**Rule:** the client is copied into the task's working directory by the panel — use
`python3 freecad_bridge_client.py …` relative to the project directory and do not go
looking elsewhere; if it is genuinely missing, say so instead of searching the disk.

## L6 — Verify the machine, not the config, before a print (2026-07-24, cable clip)
A test clip was sliced from `slicer-config.json` and uploaded without ever asking the
printer what nozzle and filament it actually had. The config happened to be right, so
nothing broke — but the check that would have caught a wrong spool was simply missing.
A config records intent; only the machine knows reality, and a 40-minute print is not
a cheap place to discover the difference. **Rule:** before starting any print, compare
the nozzle diameter and filament type from the G-code header against the printer's own
report AND against the material the user named in the prompt; treat "could not verify"
as unresolved rather than as a match. → promoted: `manufacture` print step 4,
`print_send.py --send` pre-flight (blocks `--start` on mismatch).

## L7 — Vzdálený start tisku předpokládá prázdnou podložku (2026-07-25, háček na ručník)
Tisk byl spuštěn ze sítě dvě hodiny po předchozím tisku, aniž kdokoli potvrdil, že je
podložka volná. Tiskárna po startu spadla do `ATTENTION`. Pre-flight umí porovnat trysku
a filament, ale **stav podložky žádné API nehlásí** — a hotový díl z minulého tisku pod
sondou zastaví start stejně spolehlivě jako špatná tryska. **Rule:** před `--start` na
dálku si nech potvrdit, že je podložka prázdná a předchozí výtisk sundaný; nahrání
G-code je vratné a může proběhnout bez ptaní, roztočení tisku ne. → promoted:
`manufacture` krok 4.

## L8 — ZRUŠENA: chyba byla v evalu, ne v modelu (2026-07-26)
Tady stálo, že modely třikrát vyrobily lisované uložení na jmenovitých 22,0 mm.
**Nebyla to pravda.** Kontrola v `bearing_block_608.json` četla alias `BearingOD`
a žádala, aby byl pod 21,95 — jenže `BearingOD` je z definice **22 mm, to je ten
průměr ložiska**. Model měl celou dobu `PressInterference = 0,075` a `BoreDia =
21,85`, což je přesně správně; ověřeno i na geometrii (válcová plocha Ø21,85).
Po opravě aliasu na `BoreDia` má tentýž model **9/9**.

Ponaučení tedy zůstává, ale míří jinam. **Rule:** než z opakovaného selhání uděláš
pravidlo pro agenta, **ověř na geometrii, že check měří to, co si myslíš** — u
kontroly aliasu si přečti, co ten alias podle zadání znamená. Tři „výskyty" byly tři
spuštění jednoho rozbitého checku, a lekce z nich napsaná učila agenta opravovat
něco, co dělal správně. Je to stejná třída chyby jako kdysi datumy počítané jako
geometrie nebo obrácené normály — eval měřil něco jiného než realitu a vina padla
na model. → promoted: `bearing_block_608.json` (alias opraven na `BoreDia`).

## L9 — Rozměry běžných věcí se dohledávají, ne odhadují (2026-07-25, držák na utěrky)
Držák na kuchyňské utěrky dostal 250 mm a role se do něj nevejde. Naměřeno: česká
role ~230 mm, německá ~260 mm, americká 11″ = 279 mm, průměr 105–150 mm. Playbook
o rešerši v harnesu **byl**, jen se nespustil — jeho spouštěč zněl „když spec odkazuje
na objekt, jehož rozměry je třeba dohledat", a u kuchyňské role si člověk (i model)
řekne, že tu přece zná. Právě u všedních předmětů sebejistý odhad selže, protože nic
nenutí ke kontrole. **Rule:** cokoli díl drží, nese nebo do čeho zapadá, dostane
rozměry **napsané i se zdrojem dřív, než vznikne první geometrie** — i když ten
předmět „znáš". Dimenzuj na **největší běžnou variantu plus vůli** a napiš, pro
kterou variantu je díl navržený; díl padnoucí jen na nejmenší verzi je pro většinu
lidí rozbitý. → promoted: `core` (sekce „Before any geometry: what does it hold?",
DoD bod 8), nový playbook `reference-dimensions`.

## L10 — „Dotýkají se, takže drží" není spoj (2026-07-25, zpětná vazba z praxe)
Vícedílné sestavy vznikaly bez toho, aby bylo řečeno, co je drží pohromadě —
plocha na ploše a doufat. Dvě konkrétní pasti: díl na **jednom šroubu je pant**
(drží, ale otočí se), a **tištěný závit nebo klip** na místě, kde působí síla,
vydrží zlomek toho co koupené železo. **Rule:** ke každému rozhraní napsat, **co ho
drží** a **co brání uvolnění a pootočení**, dřív než vznikne geometrie. U nosných
míst sáhnout po **standardním spojovacím materiálu** (šroub do zálisku, matice
v kapse, závitová tyč skrz díl) a u dlouhých tištěných dílů zvážit **závitovou tyč
jako výztuhu** — plast je slabý mezi vrstvami, ocelová tyč z toho dělá problém oceli.
V reportu uvést, co si musí uživatel koupit. → promoted: `core` DoD bod 9,
`assembly` §3 a §3b, `fasteners` §6.

