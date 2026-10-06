#!/usr/bin/env python3
"""
Sucht in exportierten PowerBuilder-Quelldateien (2025 R2) alle Stellen, an denen ein Datumsformat für Eingabe oder
Anzeige festgelegt oder verarbeitet wird. Hilfe für die Umstellung auf das US-Format (mm/dd/yyyy).

Aufruf:
    python find_date_formats.py [--root .] [--out date_formats.csv] [--ext .srw,.sru,...] [--quiet] [--loose]

Ausgabe: <out> (Detailliste), <out>_summary.csv (gefundene Formatmasken mit Anzahl),
         <out>_dwcolumns.csv (DataWindow-Spalten vom Typ date/datetime/timestamp und Retrieval-Argumente mit
         Format, Edit-Stil und Maske), Zusammenfassung in der Konsole.

Art (was gefunden wurde):
    DW_FORMAT     DataWindow: format="..." einer Spalte/eines Feldes                (.srd)
    DW_MASK       DataWindow: editmask mask="..."                                  (.srd)
    DW_EXPR       Datumsmaske in einem DataWindow-Ausdruck, z.B. String(col,'dd.mm.yyyy')
    MODIFY_FMT    Modify()/Describe() mit Format
    MASK_PROP     Setzen von .Mask / .Format / .EditMask / MaskDataType
    STRING_FMT    String(datum,'dd.mm.yyyy')
    LITERAL_MASK  sonstige Zeichenkette mit Datumsmaske
    REGIONAL      [shortdate]/[longdate] - folgt den Windows-Regionaleinstellungen (muss meist nicht geändert werden)
    MASK_CTRL     DateMask!/DateTimeMask!/DatePicker/DDCalendar-Steuerelemente
    DATE_PARSE    Date()/DateTime()/IsDate() - Text wird in ein Datum umgewandelt (abhängig von Regionaleinstellung)
    POS_PARSE     Datumstext wird positionsweise zerlegt: Mid(s,4,2), Left(s,2), Right(s,4) ...
    DATE_LITERAL  Datum als Textkonstante: '31.12.2025'
    DB_FMT        Datenbankformat: to_char/convert/date_format/date_order/dateformat/...
    IO_DATE       ImportFile/ImportString/SaveAs/ExportString... (nur mit --loose, sonst zu viele Treffer)
    ENV           Auslesen der Regionaleinstellungen: Control Panel\\International, GetLocaleInfo, SetThreadLocale
                  (mit --loose auch alle GetEnvironment/RegistryGet)
Spalte Reihenfolge: DMY / MDY / YMD / YM / MY / DM / MD (Reihenfolge der Maske); Massnahme: empfohlene Aktion.
Spalte Eingabe_Event = ja, wenn der Treffer in einem ItemChanged/EditChanged/Modified/LosingFocus/ItemError-Event
liegt (dort ist die Eingabe-Parsing-Logik am wahrscheinlichsten).
In SQL-Code (Embedded SQL, DataWindow-SQL, SQL-Strings) wird das ISO-Format yyyy-mm-dd nicht aufgelistet.
"""
import argparse, csv, os, re, sys
from collections import defaultdict

DEFAULT_EXT = ".srw .sru .srd .srf .srm .srs .sra .srq .srp .srj .srx".split()
SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__"}

HEAD_RE = re.compile(r"^\s*(?:(?:global|public|private|protected)\s+)*"
                     r"(?:(?:subroutine|function\s+\S+)\s+(\w+)\s*\(|event\s+(?:type\s+\S+\s+)?([\w.]+)|on\s+([\w.]+))", re.I)
END_RE = re.compile(r"^\s*end\s+(function|subroutine|event|on)\b", re.I)
SQL_START = re.compile(r"^\s*(select|insert|update|delete|declare|execute|open|fetch|close|commit|rollback)\b", re.I)
INPUT_EVENT_RE = re.compile(r"itemchanged|editchanged|modified|losingfocus|itemerror|ue_.*(date|dat)", re.I)

MASK_CAND = re.compile(r"(?<![A-Za-z0-9_])[dmyDMY]+(?:[./\-][dmyDMY]+){0,2}(?![A-Za-z0-9_])")
RUNS = re.compile(r"d+|m+|y+", re.I)

OTHER = [  # (kind, regex)
    ("REGIONAL",     re.compile(r"\[\s*(shortdate|longdate)\s*\]", re.I)),            # [General]/[Time] werden nicht gezählt - sie betreffen nicht nur Datumswerte
    ("MASK_CTRL",    re.compile(r"\b(DateMask!|DateTimeMask!|DatePicker|DDCalendar\s*=\s*[\"']?yes|DDCalendar\b)", re.I)),
    ("DATE_PARSE",   re.compile(r"\b(IsDate|Date|DateTime)\s*\(\s*(?!Today\s*\(|Now\s*\(|\w+\s*,)", re.I)),
    ("DB_FMT",       re.compile(r"\b(to_char|to_date|dateformat|date_format|date_order|nearest_century|datepart)\b|"
                                r"\bconvert\s*\(\s*n?(var)?char\s*(\(\d+\))?\s*,[^;]*?,\s*\d{1,3}\s*\)", re.I)),
    ("ENV",          re.compile(r"Control\s*Panel.{0,4}International|\b(GetLocaleInfo|SetThreadLocale|sShortDate|iDate)\b", re.I)),
]
OTHER_LOOSE = [
    ("IO_DATE",      re.compile(r"\b(ImportFile|ImportString|ImportClipboard|SaveAs|ExportString)\s*\(", re.I)),
    ("ENV",          re.compile(r"\b(GetEnvironment|RegistryGet)\b", re.I)),
]
LITERAL_RE = re.compile(r"[\"']\s*(\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{4})(\s+[\d:]+)?\s*[\"']")
POS_RE = re.compile(r"\bMid\s*\(\s*[^,()]+,\s*[3-9]\s*,\s*[24]\s*\)|\b(Left\s*\([^,()]+,\s*[24]\s*\)|Right\s*\([^,()]+,\s*[24]\s*\))", re.I)
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DATEPART_RE = re.compile(r"^(dat|date|datum\w*|dtm|[a-z]{0,2}dt|dd|mm|yy|yyyy|gads|menes|diena|period\w*)$", re.I)


def has_dateword(text):
    """Enthält ein Bezeichner (Teile durch _ getrennt) ein Datumswort: ld_dat, ls_datum, dt_von, periode ..."""
    for ident in IDENT_RE.findall(text):
        if any(DATEPART_RE.match(part) for part in ident.split("_") if part):
            return True
    return False


def valid_date_literal(text):
    for m in LITERAL_RE.finditer(text):
        n = re.split(r"[-./]", m.group(1))
        n = [int(x) for x in n]
        a, b, c = n
        y, x1, x2 = (a, b, c) if a > 31 else (c, a, b)
        if 1900 <= y <= 2100 and ((1 <= x1 <= 12 and 1 <= x2 <= 31) or (1 <= x2 <= 12 and 1 <= x1 <= 31)):
            return True
    return False


SQL_CONTEXT_RE = re.compile(r"\b(select\b.+\bfrom|insert\s+into|update\s+[\w.\"\[\]]+\s+set|delete\s+from|\bwhere\b|PBSELECT)", re.I)
ISO_MASK_RE = re.compile(r"^yyyy-mm-dd$", re.I)
ISO_CONVERT_RE = re.compile(r"\bconvert\s*\([^;]*?,\s*(20|21|23|120|121|126|127)\s*\)", re.I)
ISO_LITERAL_RE = re.compile(r"[\"']\s*\d{4}-\d{1,2}-\d{1,2}(\s+[\d:]+)?\s*[\"']")

ACTION = {"DMY": "ÄNDERN (dd.mm.yyyy -> mm/dd/yyyy)", "DM": "ÄNDERN", "MDY": "OK (bereits US-Format)", "MD": "OK (bereits US-Format)",
          "YMD": "PRÜFEN (ISO/DB/Datei?)", "YM": "PRÜFEN (Monat/Jahr)", "MY": "PRÜFEN (Monat/Jahr)"}
KIND_ACTION = {"REGIONAL": "OK (folgt Windows)", "MASK_CTRL": "PRÜFEN (Steuerelement)", "DATE_PARSE": "PRÜFEN (Regionaleinstellung)",
               "POS_PARSE": "PRÜFEN (Parsing nach Position)", "DATE_LITERAL": "PRÜFEN (Reihenfolge im Literal)",
               "DB_FMT": "PRÜFEN (DB-Format)", "IO_DATE": "PRÜFEN (Datumswerte in Dateien)", "ENV": "PRÜFEN"}


def read_text(path):
    raw = open(path, "rb").read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if b"\x00" in raw[:200]:
        return raw.decode("utf-16-le", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1257", "replace")


def statements(lines):
    i, n = 0, len(lines)
    while i < n:
        start, idx, buf = i, [i], lines[i].rstrip()
        sql = bool(SQL_START.match(buf))
        while True:
            if buf.endswith("&") and i + 1 < n:
                i += 1; idx.append(i); buf = buf[:-1] + " " + lines[i].strip()
            elif sql and ";" not in buf and i + 1 < n and len(idx) < 60:
                i += 1; idx.append(i); buf += " " + lines[i].strip()
            else:
                break
        yield start, idx, buf
        i += 1


def find_masks(text):
    """Liefert [(Maske, Reihenfolge, Position)] - Datumsmasken (dd.mm.yyyy, mm/dd/yyyy, ddmmyyyy, yyyy.mm ...)."""
    out = []
    for m in MASK_CAND.finditer(text):
        tok = m.group(0)
        runs = RUNS.findall("".join(ch for ch in tok if ch.isalpha()))
        letters = [r[0].lower() for r in runs]
        if len(letters) < 2 or len(set(letters)) != len(letters) or any(len(r) > 4 for r in runs):
            continue
        out.append((tok, "".join(letters).upper(), m.start()))
    return out


TABLE_COL_RE = re.compile(r"\bcolumn\s*=\s*\(\s*type\s*=\s*(date|datetime|timestamp)\b(.*?)\bname\s*=\s*(\w+)(.*?)\)", re.I)
DBNAME_RE = re.compile(r"dbname\s*=\s*[\"~]*([\w.$#%]+)", re.I)
ARG_RE = re.compile(r"\(\s*[\"~]*(\w+)[\"~]*\s*,\s*(date|datetime|timestamp)\s*\)", re.I)
DISP_RE = re.compile(r"^\s*column\s*\(", re.I)


def attr(line, key):
    m = re.search(r"(?<![\w.])" + re.escape(key) + r"\s*=\s*(?:~?\"([^\"~]*)~?\"|([^\s)]+))", line, re.I)
    return (m.group(1) if m.group(1) is not None else m.group(2)) if m else ""


def dw_date_columns(lines):
    """DataWindow (.srd): date/datetime/timestamp-Spalten aus table(...) + zugehöriges Anzeigefeld column(...)."""
    disp = {}
    for ln in lines:
        if DISP_RE.match(ln):
            n = attr(ln, "name")
            if n and n.lower() not in disp:
                disp[n.lower()] = ln
    out = []
    for ln in lines:
        for m in TABLE_COL_RE.finditer(ln):
            typ, name = m.group(1).lower(), m.group(3)
            dbm = DBNAME_RE.search(ln[m.start():])
            d = disp.get(name.lower(), "")
            fmt, mask = attr(d, "format"), attr(d, "editmask.mask")
            style = ("editmask" if "editmask." in d.lower() else "dddw" if "dddw." in d.lower() else
                     "ddlb" if "ddlb." in d.lower() else "checkbox" if "checkbox." in d.lower() else
                     "radiobuttons" if "radiobuttons." in d.lower() else "edit") if d else "(nicht angezeigt)"
            ddcal = attr(d, "editmask.ddcalendar")
            tabseq = attr(d, "tabsequence")
            out.append(dict(column=name, type=typ, dbname=dbm.group(1) if dbm else "", format=fmt, style=style,
                            mask=mask, ddcalendar=ddcal, tabseq=tabseq))
        for m in ARG_RE.finditer(ln):
            if re.search(r"\barguments\s*=", ln, re.I) or ln.lstrip().startswith("("):
                out.append(dict(column=m.group(1), type=m.group(2).lower() + " (Retrieval-Argument)", dbname="", format="",
                                style="", mask="", ddcalendar="", tabseq=""))
    return out


def dw_action(c):
    if "argument" in c["type"].lower():
        return "PRÜFEN (Retrieval-Argument - Datum bei Aufruf/Eingabe)"
    for txt in (c["mask"], c["format"]):
        ms = find_masks(txt) if txt else []
        if ms:
            return ACTION.get(ms[0][1], "PRÜFEN")
    if c["format"].startswith("[") or not c["format"]:
        return "OK (folgt Windows)" if c["style"] != "(nicht angezeigt)" else "PRÜFEN (nicht angezeigt)"
    return "PRÜFEN"


SRD_OBJ_RE = re.compile(r"^\s*(column|compute|text)\s*\(", re.I)
SRD_KEEP = ["name", "format", "editmask.mask", "editmask.ddcalendar", "editmask.useformat", "expression",
            "validation", "initial", "dbname"]


def snippet(text, pos, width=110):
    t = re.sub(r"\s+", " ", text)
    pos = min(pos, max(0, len(t) - 1))
    a, b = max(0, pos - width), min(len(t), pos + width)
    return ("…" if a else "") + t[a:b].strip() + ("…" if b < len(t) else "")


def short_code(s, pos, is_srd):
    """Bei DataWindow-Objekten (column/compute/text) nur datumsrelevante Attribute behalten (ohne border, color, x, y ...)."""
    m = SRD_OBJ_RE.match(s) if is_srd else None
    if m:
        parts = [m.group(1).lower()]
        for key in SRD_KEEP:
            mm = re.search(r"(?<![\w.])" + re.escape(key) + r"\s*=\s*(\"[^\"]*\"|[^\s)]+)", s, re.I)
            if mm:
                v = mm.group(1)
                parts.append(f"{key}={v}")
        return " ".join(parts)[:300]
    return snippet(s, pos)


def mask_kind(stmt, is_srd, pos):
    if is_srd:
        before = stmt[max(0, pos - 40):pos]
        if re.search(r"\bformat\s*=\s*[\"']?$", before, re.I): return "DW_FORMAT"
        if re.search(r"\bmask\s*=\s*[\"']?$", before, re.I):   return "DW_MASK"
        return "DW_EXPR"
    if re.search(r"\b(Modify|Describe)\s*\(", stmt, re.I): return "MODIFY_FMT"
    if re.search(r"\.\s*(Mask|Format|EditMask)\b|MaskDataType", stmt, re.I): return "MASK_PROP"
    if re.search(r"\bString\s*\(", stmt, re.I): return "STRING_FMT"
    return "LITERAL_MASK"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="date_formats.csv")
    ap.add_argument("--ext", default=",".join(DEFAULT_EXT))
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--loose", action="store_true", help="auch allgemeine Treffer aufnehmen (ImportFile/SaveAs, GetEnvironment/RegistryGet)")
    a = ap.parse_args()
    exts = tuple(e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower() for e in a.ext.split(",") if e.strip())

    rows, dwcols, files_scanned = [], [], 0
    for dp, dn, fn in os.walk(a.root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in sorted(fn):
            if not f.lower().endswith(exts):
                continue
            path = os.path.join(dp, f)
            rel = os.path.relpath(path, a.root)
            try:
                lines = read_text(path).splitlines()
            except OSError as e:
                print(f"Kann {rel} nicht lesen: {e}", file=sys.stderr); continue
            files_scanned += 1
            is_srd = f.lower().endswith(".srd")
            if is_srd:
                for c in dw_date_columns(lines):
                    dwcols.append([rel, c["column"], c["type"], c["dbname"], c["style"], c["format"], c["mask"],
                                   c["ddcalendar"], c["tabseq"], dw_action(c)])
            scope_at, cur = {}, ""
            for i, ln in enumerate(lines):
                m = HEAD_RE.match(ln)
                if m: cur = next(g for g in m.groups() if g)
                scope_at[i] = cur
                if END_RE.match(ln): cur = ""
            for start, idx, stmt in statements(lines):
                s = stmt.replace("~\"", "'").replace("~'", "'")
                scope = scope_at.get(start, "")
                inp = "ja" if INPUT_EVENT_RE.search(scope) else ""
                found = []                                    # (kind, mask, order, action, pos)
                in_sql = bool(SQL_START.match(s) or SQL_CONTEXT_RE.search(s))
                masks = find_masks(s)
                if masks:
                    for mask, order, pos in masks:
                        if in_sql and ISO_MASK_RE.match(mask):
                            continue                          # yyyy-mm-dd in SQL: nicht auflisten
                        found.append((mask_kind(s, is_srd, pos), mask, order, ACTION.get(order, "PRÜFEN"), pos))
                for kind, rx in OTHER + (OTHER_LOOSE if a.loose else []):
                    if rx.search(s):
                        if kind == "DB_FMT" and in_sql and (ISO_CONVERT_RE.search(s) or any(
                                ISO_MASK_RE.match(mk) for mk, _, _ in masks)):
                            continue                          # ISO (yyyy-mm-dd) in SQL: nicht auflisten
                        if kind == "MASK_CTRL" and is_srd:
                            continue                          # DataWindow kolonnas (t.sk. ddcalendar) ir *_dwcolumns.csv
                        if kind == "DATE_PARSE" and is_srd and not re.search(r"expression\s*=|initial|validation", s, re.I):
                            continue
                        found.append((kind, "", "", KIND_ACTION[kind], rx.search(s).start()))
                if valid_date_literal(s) and not (in_sql and ISO_LITERAL_RE.search(s) and not re.search(r"[\"']\s*\d{1,2}[./]\d{1,2}[./]\d{4}", s)):
                    found.append(("DATE_LITERAL", "", "", KIND_ACTION["DATE_LITERAL"], LITERAL_RE.search(s).start()))
                if (len(POS_RE.findall(s)) >= 2 or (POS_RE.search(s) and has_dateword(s))):
                    found.append(("POS_PARSE", "", "", KIND_ACTION["POS_PARSE"], POS_RE.search(s).start()))
                seen = set()
                for kind, mask, order, action, pos in found:
                    key = (kind, mask)
                    if key in seen: continue
                    seen.add(key)
                    rows.append([kind, action, rel, start + 1, scope, inp, mask, order, short_code(s, pos, is_srd)])

    prio = {"ÄNDERN": 0, "PRÜFEN": 1, "OK": 2}
    rows.sort(key=lambda r: (prio.get(r[1].split(" ")[0], 3), r[0], r[2], r[3]))
    with open(a.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Art", "Massnahme", "Datei", "Zeile", "Funktion_oder_Event", "Eingabe_Event", "Maske", "Reihenfolge", "Code"])
        w.writerows(rows)

    masks = defaultdict(lambda: {"n": 0, "files": set(), "kinds": set(), "order": ""})
    for r in rows:
        if r[6]:
            d = masks[r[6]]; d["n"] += 1; d["files"].add(r[2]); d["kinds"].add(r[0]); d["order"] = r[7]
    base, ext = os.path.splitext(a.out)
    summ = f"{base}_summary{ext or '.csv'}"
    with open(summ, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Maske", "Reihenfolge", "Massnahme", "Anzahl", "Dateien", "Arten"])
        for m, d in sorted(masks.items(), key=lambda x: -x[1]["n"]):
            w.writerow([m, d["order"], ACTION.get(d["order"], "PRÜFEN"), d["n"], len(d["files"]), ",".join(sorted(d["kinds"]))])

    dwf = f"{base}_dwcolumns{ext or '.csv'}"
    dwcols.sort(key=lambda r: (r[0].lower(), r[1].lower()))
    with open(dwf, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["DataWindow_Datei", "Spalte", "Typ", "DB_Name", "Edit_Stil", "Format", "Maske", "DDCalendar",
                    "Tabsequenz", "Massnahme"])
        w.writerows(dwcols)

    if not a.quiet:
        print(f"Gescannte Dateien: {files_scanned}, Treffer: {len(rows)}  ->  {a.out}, {summ}, {dwf}")
        print(f"DataWindow-Datumsspalten: {len(dwcols)} (in {len({r[0] for r in dwcols})} .srd-Dateien)  ->  {dwf}\n")
        by_kind = defaultdict(set); cnt = defaultdict(int)
        for r in rows: cnt[r[0]] += 1; by_kind[r[0]].add(r[2])
        print(f"{'Art':14} {'Treffer':>9} {'Dateien':>8}")
        for k in sorted(cnt, key=lambda k: -cnt[k]): print(f"{k:14} {cnt[k]:>9} {len(by_kind[k]):>8}")
        print(f"\n{'Maske':22} {'Reihenf.':8} {'Anzahl':>7} {'Dateien':>8}  Massnahme")
        for m, d in sorted(masks.items(), key=lambda x: -x[1]["n"]):
            print(f"{m:22} {d['order']:8} {d['n']:>7} {len(d['files']):>8}  {ACTION.get(d['order'], 'PRÜFEN')}")
        n_inp = sum(1 for r in rows if r[5])
        if n_inp: print(f"\nTreffer in Eingabe-Events (ItemChanged/EditChanged/...): {n_inp}  (Spalte Eingabe_Event)")


if __name__ == "__main__":
    main()
