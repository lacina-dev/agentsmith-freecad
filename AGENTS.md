# Projektová paměť

- 3D modely v tomto projektu vytvářej jako nativní dokumenty FreeCAD (`.FCStd`).
- Modely mají zůstat parametrické a dále upravitelné ve FreeCADu.
- Upřednostni nativní prvky FreeCADu (např. rozměrově svázané Sketches, Pad, Pocket, Fillet) a podle potřeby centrální parametry ve Spreadsheetu.
- OpenSCAD (`.scad`), STL ani importovaný STEP nepoužívej jako hlavní zdrojový model, pokud uživatel výslovně nerozhodne jinak.

## Práce z integrovaného AgentSmith panelu ve FreeCADu

- Je-li úloha spuštěná z AgentSmith panelu, upravuj právě otevřený dokument výhradně přes `freecad_bridge_client.py`; samotná změna generačního skriptu není splněním modelovací úlohy.
- Před změnou ověř `ping`, `capabilities` a `document_info`.
- Pro strukturované změny preferuj atomický příkaz `batch`; složitější zásahy prováděj přes `execute_python`, který bridge obalí FreeCAD transakcí.
- Po změně vždy spusť `validate`, dokument ulož, uprav pohled přes `fit_view` a vytvoř kontrolní screenshot.
- Úspěch hlásit pouze tehdy, když se prokazatelně změnil živý FreeCAD dokument a validace geometrie prošla.

## Časový rozpočet a rozhodné jednání

- Každá živá úloha má tvrdý časový limit (výchozí ~8 min); po jeho překročení watchdog úlohu ukončí a dokument vrátí zpět, takže nedokončené zkoumání = selhání s nulovou hodnotou.
- Cílem je **doručit ověřenou změnu**, ne vyčerpávající analýza. Diagnostikuj rychle a jakmile máš pravděpodobnou příčinu, oprav ji přes bridge dřív, než pokračuješ v dalším zkoumání.
- Rozpočtuj čas: max. první třetina na diagnostiku, prostřední na aplikaci opravy, poslední třetina na `validate`/`save`/screenshot. Při nejistotě si ověř uplynulý čas přes `date`.
- Preferuj konkrétní, vratnou parametrickou změnu s iterací před snahou o dokonalou jistotu předem. Oprav obecnou/řídicí příčinu (např. špatný parametr ve Spreadsheetu či výraz ve Sketchi), ne natvrdo zadanou hodnotu pro jeden model.
- Když diagnostika ukáže, že správná oprava je změna řídicího parametru, výrazu nebo vazby, tuto změnu rovnou proveď přes bridge — nestačí ji jen popsat.
