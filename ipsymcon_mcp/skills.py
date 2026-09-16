"""Die Skills dieses Repos über die MCP-Schnittstelle ausliefern.

Warum es dieses Modul gibt
--------------------------
Die fünf Runbooks unter ``skills/`` werden seit v0.4.0 als Claude-Code-Plugin
ausgeliefert (``.claude-plugin/plugin.json``). Das deckt genau einen Client ab.
Wer den MCP-Server ohne dieses Plugin einbindet — anderer Client, anderer Rechner,
jemand anderes — bekommt 22 Tools und **keinen einzigen Hinweis darauf, dass es
Betriebsanweisungen dazu gibt**. Die wichtigste davon ist keine Bequemlichkeit,
sondern ein Sicherheitsverhalten: *Plan zeigen, bevor geschrieben wird.*

Dieses Modul schließt die Lücke über zwei MCP-Primitives:

``instructions``
    **Push.** Landet ungefragt im Systemprompt des Clients — auch bei jemandem,
    der die Skills nie installiert. Trägt deshalb nur das, was immer gelten muss:
    Zweck, die harte Schreibgrenze, und einen **Zeiger** auf die Runbooks.

``prompts``
    **Pull.** Ein Prompt je Skill, in Claude Code als
    ``/mcp__ipsymcon__<name>`` aufrufbar.

Die entscheidende Entwurfsregel: **hier steht kein Skill-Text.**
Die Prompts lesen die ``SKILL.md``-Dateien zur Laufzeit aus dem Repo. Eine Kopie
wäre ein zweiter Stand, der irgendwann vom ersten abweicht — und der Server hätte
dann eine Regel behauptet, die im Repo längst anders lautet. Genau diese Drift
(Fähigkeit hier dokumentiert, Grenze dort) war der Befund, aus dem dieses Modul
entstanden ist. Eine Datei, drei Auslieferungswege.
"""

from __future__ import annotations

from pathlib import Path

REPO_URL = "https://github.com/Schimmilab/ipsymcon-mcp-server"
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

#: Reihenfolge ist Absicht: ``ipsymcon`` ist der Einstieg, die übrigen sind
#: Spezialfälle. Bewusst hier gepflegt und nicht aus dem Verzeichnis geraten —
#: ein versehentlich abgelegter Ordner soll nicht zu einem Prompt werden.
SKILLS: dict[str, str] = {
    "ipsymcon": "Einstieg: Objektbaum lesen, Skripte/Variablen/Events anlegen und ändern",
    "ips-automation": "Eine NEUE Automation entwerfen (Schwelle, Zeitschaltung, Nachlauf, Standby-Abschaltung)",
    "ips-cleanup": "Aufräumen: rote Logs, tote Instanzen, verwaiste Objekte entfernen",
    "ips-migration": "Objekt-Teilbaum auf eine andere IPS-Instanz umziehen und die Übernahme prüfen",
    "ips-refactor": "Funktionierendes umstrukturieren, ohne sein Verhalten zu ändern",
}

INSTRUCTIONS = f"""\
Zugriff auf eine IP-Symcon-Hausautomation über deren JSON-RPC-API: Objektbaum,
Variablen samt Archiv-Historie, PHP-Skripte, Events — und, sofern serverseitig
freigeschaltet, das Schalten realer Aktoren.

⛔ SCHREIBEN: ERST PLANEN, DANN AUSFÜHREN.
IP-Symcon steuert ein echtes Haus — Heizung, Rollläden, Steckdosen. Ein falscher
Schreibvorgang hat physische Folgen. Verbindlicher Ablauf für jedes Werkzeug, das
etwas verändert (ips_set_value, ips_request_action, ips_run_script, ips_call sowie
alle ips_create_*/ips_set_*/ips_import_*):

  lesen → Plan mit konkretem vorher→nachher zeigen → Freigabe abwarten →
  ausführen → berichten, was tatsächlich geändert wurde.

Lesen ist davon ausgenommen und braucht keinen Plan -- dafuer gibt es die
Lesewerkzeuge und das Lese-Gateway ips_call_read, das schreibende Funktionen
von sich aus abweist.

Nie schreiben, bevor der Mensch den Plan gesehen hat. Objekt-IDs sind
nichtssagende Ganzzahlen — niemals raten, immer über Namen bzw. Baum auflösen.
Schlägt ein Schritt fehl: anhalten und berichten, nicht weitermachen.

📓 RUNBOOKS: Dieser Server bringt fünf ausführliche Betriebsanweisungen mit.
Sie sind über die Prompts dieses Servers abrufbar (in Claude Code als
/mcp__ipsymcon__<name>) und liegen versioniert unter skills/ im Repo:
{REPO_URL}
Vor einer größeren Aufgabe — Automation bauen, aufräumen, migrieren,
umstrukturieren — das passende Runbook holen, statt es sich herzuleiten.
"""


class SkillNotAvailableError(RuntimeError):
    """Ein Runbook ist zur Laufzeit nicht lesbar."""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - Dateisystemfehler
        raise SkillNotAvailableError(
            f"Runbook nicht lesbar ({path.name}): {exc}. "
            f"Die Runbooks liegen unter skills/ im Repo: {REPO_URL}"
        ) from exc


def strip_frontmatter(text: str) -> str:
    """Entfernt einen führenden YAML-Frontmatter-Block.

    Der Frontmatter ist Routing-Information für den Skill-Lader ("wann greift
    dieser Skill?"). Wer den Prompt aufruft, hat sich bereits entschieden — die
    Frage ist dann beantwortet und der Block nur noch Ballast im Kontext.

    Robust gegen den Fall, dass die Datei mit einer waagerechten Linie beginnt,
    aber gar keinen Frontmatter hat: ohne schließende Zeile bleibt der Text
    unverändert, statt ihn bis zum nächsten ``---`` zu köpfen.
    """
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + 5 :].lstrip("\n")


def render_skill(name: str, *, mit_referenzen: bool = True) -> str:
    """Baut den Prompt-Text eines Runbooks aus den Repo-Dateien.

    ``mit_referenzen`` hängt die Dateien aus ``references/`` an. Das ist der
    Default, weil die SKILL.md-Dateien auf sie verweisen ("template in
    workflow.md") — ohne sie liefert der Prompt eine Anweisung mit einem Loch an
    genau der Stelle, an der es konkret wird.
    """
    if name not in SKILLS:
        bekannt = ", ".join(SKILLS)
        raise SkillNotAvailableError(f"Unbekanntes Runbook {name!r}. Verfügbar: {bekannt}")

    skill_dir = SKILLS_DIR / name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillNotAvailableError(
            f"Runbook {name!r} ist in dieser Installation nicht vorhanden "
            f"({skill_md} fehlt). Das passiert, wenn nur das Python-Paket ohne "
            f"das Repo installiert wurde. Quelle: {REPO_URL}/tree/main/skills/{name}"
        )

    teile = [
        f"# Runbook `{name}` — {SKILLS[name]}",
        "",
        f"> Quelle: `skills/{name}/SKILL.md` aus {REPO_URL} — zur Laufzeit gelesen, "
        f"keine Kopie. Maßgeblich ist immer die Datei im Repo.",
        "",
        strip_frontmatter(_read(skill_md)).rstrip(),
    ]

    if mit_referenzen:
        for ref in sorted((skill_dir / "references").glob("*.md")):
            teile += ["", "---", "", f"## Referenz: `references/{ref.name}`", "", _read(ref).rstrip()]

    return "\n".join(teile)


def register(mcp) -> None:  # noqa: ANN001 - FastMCP-Instanz, kein öffentlicher Typ
    """Registriert je ein Prompt pro Runbook auf der FastMCP-Instanz.

    Bewusst ein Prompt **je** Skill statt eines generischen ``runbook(name=...)``:
    So taucht jedes Runbook einzeln in der Prompt-Liste des Clients auf und ist
    auffindbar, ohne dass jemand erst die gültigen Parameterwerte erraten muss.
    Auffindbarkeit ist der ganze Zweck dieses Moduls.
    """
    for name, beschreibung in SKILLS.items():
        def _make(skill_name: str):
            def _prompt(mit_referenzen: bool = True) -> str:
                return render_skill(skill_name, mit_referenzen=mit_referenzen)

            _prompt.__name__ = skill_name.replace("-", "_")
            return _prompt

        mcp.prompt(
            name=name,
            description=f"{beschreibung}. Betriebsanweisung dieses Servers, gelesen aus skills/{name}/.",
        )(_make(name))
