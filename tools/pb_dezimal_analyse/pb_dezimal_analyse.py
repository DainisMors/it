#!/usr/bin/env python3
"""
PowerBuilder-Quellcode-Analyse: Ist die Eingabe von Dezimalzahlen mit Komma möglich?

Analysiert exportierte PowerBuilder-Objekte (.srw .sru .srd .srf .srm .srs .sra .srj .srx)
statisch und bewertet, ob Zahlen mit Dezimalkomma (statt Dezimalpunkt) eingegeben
werden können. Ausgabe (Bericht und Schlussfolgerungen) auf Deutsch.

Aufruf:
    python3 pb_dezimal_analyse.py PFAD [PFAD ...] [--format text|md|json] [--output DATEI]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass

ENDUNGEN = {".srw", ".sru", ".srd", ".srf", ".srm", ".srs", ".sra", ".srj", ".srx", ".txt"}

# Kategorien
BLOCKER = "BLOCKER"    # fest verdrahteter Dezimalpunkt -> Komma wird verhindert/falsch gelesen
WARNUNG = "WARNUNG"    # Risiko, dass Komma zu Fehlern führt
LOKALE = "LOKALE"      # verhält sich nach Windows-Regionaleinstellung (Komma möglich, wenn Region es vorgibt)
POSITIV = "POSITIV"    # Komma wird bereits unterstützt / normalisiert
INFO = "INFO"

KAT_TITEL = {
    BLOCKER: "Hartcodierter Dezimalpunkt (verhindert Komma)",
    WARNUNG: "Fehlerrisiko bei Komma",
    LOKALE: "Abhängig von Windows-Regionaleinstellung",
    POSITIV: "Komma bereits unterstützt",
    INFO: "Zur Information",
}


@dataclass
class Regel:
    id: str
    kat: str
    muster: str
    titel: str
    hinweis: str


R = [
    # ---- BLOCKER -------------------------------------------------------------
    Regel("H01", BLOCKER, r"""\b(?:Pos|LastPos)\s*\([^\n]*?["']\.["']""",
          "Suche nach festem Punkt ('.') mit Pos()/LastPos()",
          "Dezimalstelle wird über den Punkt ermittelt; ein Komma wird nicht gefunden. "
          "Dezimaltrennzeichen aus den Regionaleinstellungen verwenden oder zuerst ',' -> '.' normalisieren."),
    Regel("H02", BLOCKER, r"""\bMatch\s*\([^\n]*?(?:\\\.|\[\.\]|~\.)""",
          "Match()-Muster erlaubt nur den Punkt",
          "Regulären Ausdruck um das Komma erweitern, z. B. [.,]."),
    Regel("H03", BLOCKER, r"""\bAsc\s*\(\s*["']\.["']\s*\)""",
          "Zeichencode des Punktes fest abgefragt",
          "Auch Asc(',') zulassen bzw. Dezimaltrennzeichen dynamisch bestimmen."),
    Regel("H04", BLOCKER, r"""\bKey(?:Period|Decimal)!""",
          "Tastaturbehandlung nur für Punkt/Ziffernblock-Dezimaltaste",
          "KeyComma! gleichwertig behandeln (Tastenereignis prüfen)."),
    Regel("H05", BLOCKER, r"""(?i)(?:\bkey|wordparm|vk_|keycode)[^\n]*\b(?:110|190)\b""",
          "Tastencode 110/190 (Dezimal-/Punkt-Taste) fest abgefragt",
          "Tastencode 188 (Komma) ebenfalls berücksichtigen."),
    Regel("H06", BLOCKER, r"""(?:(?:=|<>)\s*["']\.["']|["']\.["']\s*(?:=|<>))""",
          "Zeichenvergleich mit festem Punkt",
          "Vergleich auch mit dem Komma durchführen oder das Zeichen aus den Regionaleinstellungen lesen."),
    # ---- WARNUNG ------------------------------------------------------------
    Regel("W01", WARNUNG, r"""(?i)(?=[^\n]*\b(?:select|insert|update|delete|where|values|set)\b)(?=[^\n]*String\s*\()[^\n]*\+""",
          "Zahl per String() in SQL-Text eingebaut",
          "Bei Dezimalkomma entsteht '1,5' im SQL (Syntaxfehler/falscher Wert). Gebundene Variablen (:var) "
          "verwenden oder Replace(String(x), ',', '.') einsetzen."),
    # ---- POSITIV ------------------------------------------------------------
    Regel("P01", POSITIV, r"""\b\w+\s*\([^()\n]*,\s*["'],["']\s*,\s*["']\.["']\s*\)""",
          "Komma wird in Punkt umgewandelt (Normalisierung)",
          "Gutes Muster: zentral in einer Funktion bündeln und bei jeder Zahleneingabe verwenden."),
    Regel("P02", POSITIV, r"""\bReplace\s*\([^\n]*Pos\s*\([^\n]*["'],["'][^\n]*["']\.["']""",
          "Komma wird per Replace(Pos()) durch Punkt ersetzt",
          "Gutes Muster; auf Vollständigkeit prüfen (alle Eingabestellen)."),
    Regel("P03", POSITIV, r"""(?i)sDecimal|iDigits|GetLocaleInfo|GetProfileString|LOCALE_SDECIMAL|Control\s*Panel.{0,3}International""",
          "Dezimaltrennzeichen wird aus den Systemeinstellungen gelesen",
          "Lokalisierungsbewusste Verarbeitung - Komma ist damit grundsätzlich möglich."),
    Regel("P04", POSITIV, r"""\b\w+\s*\([^()\n]*,\s*["']\.["']\s*,\s*["'],["']\s*\)""",
          "Punkt wird in Komma umgewandelt (Ausgabe)",
          "Ausgabe mit Komma ist bereits vorhanden; Eingabe getrennt prüfen."),
    # ---- LOKALE -------------------------------------------------------------
    Regel("L01", LOKALE, r"""\bIsNumber\s*\(""",
          "IsNumber() - Prüfung richtet sich nach Regionaleinstellung",
          "Mit Dezimalkomma-Region wird '1,5' akzeptiert, '1.5' ggf. nicht. Mit beiden Einstellungen testen."),
    Regel("L02", LOKALE, r"""\b(?:Dec|Double|Real|Number)\s*\(""",
          "Zeichenkette -> Zahl (Dec/Double/Real/Number)",
          "Ergebnis hängt vom Dezimalzeichen des Systems ab (je nach PB-Version); mit Komma testen."),
    Regel("L03", LOKALE, r"""\bString\s*\([^\n]*?,\s*["'][#0,.]*[#0][,.][#0]+[^"']*["']""",
          "String() mit Formatmaske",
          "Der Punkt in der Maske steht für das Dezimalzeichen der Region; im Zielsystem (Komma-Region) prüfen."),
    Regel("L04", LOKALE, r"""\b(?:DecimalMask|NumericMask)!|\bMaskDataType\b|\bfrom\s+editmask\b""",
          "EditMask-Steuerelement für Zahlen",
          "EditMask verwendet das Dezimalzeichen der Windows-Region - Komma ist ohne Codeänderung möglich, "
          "wenn die Region Komma vorgibt."),
    Regel("L05", LOKALE, r"""column=\(type=(?:number|decimal\(\d+\)|real|double)\b""",
          "DataWindow-Spalte mit Dezimal-/Zahlentyp",
          "Eingabe/Anzeige folgt der Regionaleinstellung; Komma funktioniert, wenn die Region es vorgibt."),
    Regel("L06", LOKALE, r"""\bformat\s*=\s*"[^"\n]*[#0][.,][#0]""",
          "DataWindow-Spaltenformat mit Dezimalmaske",
          "Der Punkt in der Maske wird zur Laufzeit durch das Regionalzeichen ersetzt."),
    Regel("L07", LOKALE, r"""(?i)\beditmask\s*\(|edit\.numeric\s*=\s*yes|\bnumeric\s*=\s*yes""",
          "DataWindow-Eingabefilter (EditMask / Numeric=yes)",
          "Filter erlaubt nur die Zeichen der Region; Dezimalkomma nur bei entsprechender Regionaleinstellung."),
    # ---- INFO ---------------------------------------------------------------
    Regel("I01", INFO, r"""\bfrom\s+singlelineedit\b""",
          "Freitextfeld (SingleLineEdit) - keine automatische Zahlenprüfung",
          "Komma wird nur akzeptiert, wenn der Code den Text selbst korrekt in eine Zahl umwandelt."),
    Regel("I02", INFO, r"""\b(?:ImportString|ImportFile|ExportString|ImportClipboard|SaveAs)\s*\(""",
          "Import/Export von Daten",
          "Dezimaltrennzeichen in Dateien/CSV getrennt prüfen."),
]
REGELN = {r.id: r for r in R}
KOMPIL = [(r, re.compile(r.muster)) for r in R]


# ---------------------------------------------------------------------------
def lese_datei(pfad):
    roh = open(pfad, "rb").read()
    if roh.startswith((b"\xff\xfe", b"\xfe\xff")):
        return roh.decode("utf-16", errors="replace")
    if roh.startswith(b"\xef\xbb\xbf"):
        return roh.decode("utf-8-sig", errors="replace")
    if b"\x00" in roh[:200]:
        return roh.decode("utf-16-le", errors="replace")
    try:
        return roh.decode("utf-8")
    except UnicodeDecodeError:
        return roh.decode("cp1252", errors="replace")


def kommentare_entfernen(text):
    """Ersetzt //- und /* */-Kommentare durch Leerzeichen (Zeilen bleiben erhalten).
    Zeichenketten ("..." / '...', Escape ~) bleiben unverändert."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c in "\"'":
            q = c
            out.append(c)
            i += 1
            while i < n and text[i] != "\n":
                out.append(text[i])
                if text[i] == "~" and i + 1 < n:
                    i += 1
                    out.append(text[i])
                elif text[i] == q:
                    i += 1
                    break
                i += 1
        elif c == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
        elif c == "/" and nxt == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def analysiere_datei(pfad):
    text = lese_datei(pfad)
    ist_srd = pfad.lower().endswith(".srd")
    bereinigt = text if ist_srd else kommentare_entfernen(text)
    original = text.splitlines()
    funde = []
    for nr, zeile in enumerate(bereinigt.splitlines(), 1):
        if not zeile.strip():
            continue
        for regel, muster in KOMPIL:
            if muster.search(zeile):
                funde.append({
                    "datei": pfad, "zeile": nr, "regel": regel.id, "kategorie": regel.kat,
                    "code": (original[nr - 1] if nr - 1 < len(original) else zeile).strip()[:200],
                })
    return funde, len(original)


def sammle_dateien(pfade):
    for p in pfade:
        if os.path.isdir(p):
            for wurzel, _, dateien in os.walk(p):
                for d in sorted(dateien):
                    if os.path.splitext(d)[1].lower() in ENDUNGEN:
                        yield os.path.join(wurzel, d)
        elif os.path.isfile(p):
            yield p
        else:
            print(f"Warnung: '{p}' nicht gefunden", file=sys.stderr)


# ---------------------------------------------------------------------------
def bewerte(funde):
    z = Counter(f["kategorie"] for f in funde)
    b, w, l, p = z[BLOCKER], z[WARNUNG], z[LOKALE], z[POSITIV]
    if b and p:
        urteil = "TEILWEISE MÖGLICH"
        text = ("Ein Dezimalkomma wird an einigen Stellen bereits akzeptiert bzw. normalisiert, an anderen "
                "Stellen aber durch fest verdrahtete Punkt-Logik verhindert. Eine Anpassung des Codes ist nötig.")
    elif b:
        urteil = "DERZEIT NICHT ZUVERLÄSSIG MÖGLICH"
        text = ("Der Code enthält fest verdrahtete Prüfungen auf den Dezimalpunkt. Ein Komma wird dadurch "
                "nicht erkannt oder falsch interpretiert. Eine Codeanpassung ist erforderlich.")
    elif l or p:
        urteil = "GRUNDSÄTZLICH MÖGLICH" + (" (MIT PRÜFBEDARF)" if w else "")
        text = ("Es wurde keine fest verdrahtete Punkt-Logik gefunden. Zahleneingaben laufen über "
                "regionsabhängige Mechanismen (EditMask, DataWindow, Dec/IsNumber). Das Komma funktioniert, "
                "wenn in den Windows-Regionaleinstellungen das Komma als Dezimalzeichen eingestellt ist. "
                "Dass heute der Punkt verwendet wird, liegt dann wahrscheinlich an der Systemeinstellung "
                "und nicht am Code.")
    else:
        urteil = "NICHT BEURTEILBAR"
        text = "Es wurden keine relevanten Zahleneingabe-Konstrukte gefunden (falsche Dateien/Pfad?)."
    return urteil, text, z


def empfehlungen(funde, z):
    ids = {f["regel"] for f in funde}
    e = []
    for i in sorted(ids):
        r = REGELN[i]
        if r.kat in (BLOCKER, WARNUNG):
            e.append(f"[{i}] {r.hinweis}")
    if z[BLOCKER] or z[WARNUNG]:
        e.append("Zentrale Funktion (z. B. of_normalize_decimal) einführen: ersetzt ',' durch '.' bzw. nutzt das "
                 "Regionalzeichen und wird an allen Eingabestellen (ItemChanged, Key-Events, SLE-Parsing) verwendet.")
    if z[LOKALE]:
        e.append("Auf Testrechner Windows-Regionaleinstellung 'Dezimalsymbol' auf ',' stellen und EditMasks, "
                 "DataWindow-Spalten sowie IsNumber()/Dec() mit '1,5' und '1.5' prüfen.")
    if z[WARNUNG]:
        e.append("SQL-Statements nicht per String-Verkettung mit Zahlen bauen, sondern gebundene Variablen nutzen.")
    if not e:
        e.append("Keine Anpassung erkennbar notwendig - Praxistest mit Komma-Regionaleinstellung durchführen.")
    return e


EINSCHRAENKUNGEN = [
    "Statische Textanalyse: Laufzeitverhalten und die Windows-Regionaleinstellung der Anwender sind unbekannt.",
    "Das konkrete Verhalten von Dec()/IsNumber()/EditMask kann je nach PowerBuilder-Version abweichen - "
    "Abschlusstest in der eingesetzten Version ist erforderlich.",
    "Nur exportierte Quelltexte (.srw/.sru/.srd ...) werden gelesen, keine kompilierten .pbl/.pbd.",
    "Treffer sind Hinweise, keine Beweise; Kommentare werden ignoriert, dynamisch erzeugter Code nicht erkannt.",
]


# ---------------------------------------------------------------------------
def bericht(funde, anzahl_dateien, anzahl_zeilen, fmt, max_bsp):
    urteil, begruendung, z = bewerte(funde)
    empf = empfehlungen(funde, z)
    if fmt == "json":
        return json.dumps({
            "urteil": urteil, "begruendung": begruendung, "dateien": anzahl_dateien, "zeilen": anzahl_zeilen,
            "anzahl_je_kategorie": dict(z), "empfehlungen": empf, "einschraenkungen": EINSCHRAENKUNGEN,
            "funde": funde,
        }, ensure_ascii=False, indent=2)

    md = fmt == "md"
    h1 = (lambda s: f"# {s}\n") if md else (lambda s: f"{'=' * 70}\n{s}\n{'=' * 70}")
    h2 = (lambda s: f"\n## {s}\n") if md else (lambda s: f"\n--- {s} ---")
    pt = "- " if md else "  * "
    out = [h1("PowerBuilder – Analyse: Dezimaleingabe mit Komma"),
           f"Analysiert: {anzahl_dateien} Datei(en), {anzahl_zeilen} Zeilen",
           h2("Ergebnis"), f"Beurteilung: {'**' + urteil + '**' if md else urteil}", "", begruendung,
           h2("Trefferübersicht")]
    for k in (BLOCKER, WARNUNG, LOKALE, POSITIV, INFO):
        out.append(f"{pt}{KAT_TITEL[k]}: {z[k]}")

    nach_regel = defaultdict(list)
    for f in funde:
        nach_regel[f["regel"]].append(f)
    out.append(h2("Details"))
    for k in (BLOCKER, WARNUNG, LOKALE, POSITIV, INFO):
        regeln = [r for r in R if r.kat == k and r.id in nach_regel]
        if not regeln:
            continue
        out.append(f"\n{'### ' if md else '['}{KAT_TITEL[k]}{'' if md else ']'}")
        for r in regeln:
            liste = nach_regel[r.id]
            out.append(f"{pt}{r.id} {r.titel} – {len(liste)} Treffer")
            for f in liste[:max_bsp]:
                out.append(f"      {f['datei']}:{f['zeile']}: {f['code']}")
            if len(liste) > max_bsp:
                out.append(f"      ... {len(liste) - max_bsp} weitere")
    je_datei = Counter((f["datei"], f["kategorie"]) for f in funde)
    dateien = sorted({f["datei"] for f in funde})
    if dateien:
        out.append(h2("Treffer je Datei (Blocker / Warnung / Region / Positiv)"))
        for d in dateien:
            out.append(f"{pt}{d}: {je_datei[(d, BLOCKER)]} / {je_datei[(d, WARNUNG)]} / "
                       f"{je_datei[(d, LOKALE)]} / {je_datei[(d, POSITIV)]}")
    out.append(h2("Empfehlungen"))
    out += [pt + e for e in empf]
    out.append(h2("Einschränkungen der Analyse"))
    out += [pt + e for e in EINSCHRAENKUNGEN]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Analysiert PowerBuilder-Code auf Unterstützung des Dezimalkommas.")
    ap.add_argument("pfade", nargs="+", help="Dateien oder Verzeichnisse mit exportiertem PowerBuilder-Code")
    ap.add_argument("--format", choices=["text", "md", "json"], default="text")
    ap.add_argument("--output", help="Bericht in Datei schreiben")
    ap.add_argument("--max-beispiele", type=int, default=10, help="max. Beispielzeilen je Regel (Standard 10)")
    a = ap.parse_args()

    funde, zeilen, dateien = [], 0, 0
    for pfad in sammle_dateien(a.pfade):
        f, n = analysiere_datei(pfad)
        funde += f
        zeilen += n
        dateien += 1
    text = bericht(funde, dateien, zeilen, a.format, a.max_beispiele)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"Bericht geschrieben: {a.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
