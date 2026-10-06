#!/usr/bin/env python3
"""
Meklē PowerBuilder (2025 R2) eksportēto avotu failos vietas, kur ir ierakstīts / apstrādāts datuma ievades vai
attēlošanas formāts. Palīdz saplānot pāreju uz ASV formātu (mm/dd/yyyy).

Lietošana:
    python find_date_formats.py [--root .] [--out date_formats.csv] [--ext .srw,.sru,...] [--quiet]

Izvade: <out> (detalizēts saraksts), <out>_summary.csv (atrastās formāta maskas ar skaitu),
        <out>_dwcolumns.csv (DataWindow date/datetime/timestamp kolonnas un retrieval argumenti ar to formātu,
        rediģēšanas stilu un masku), kopsavilkums konsolē.

kind (kas atrasts):
    DW_FORMAT     DataWindow kolonnas/lauka format="..."          (.srd)
    DW_MASK       DataWindow editmask mask="..."                  (.srd)
    DW_EXPR       datuma maska DataWindow izteiksmē, piem. String(col,'dd.mm.yyyy')
    MODIFY_FMT    Modify()/Describe() ar formātu
    MASK_PROP     .Mask / .Format / .EditMask / MaskDataType iestatīšana
    STRING_FMT    String(datums,'dd.mm.yyyy')
    LITERAL_MASK  cita virkne ar datuma masku
    REGIONAL      [shortdate]/[longdate]/[general] - seko Windows reģionālajiem iestatījumiem (parasti nav jāmaina)
    MASK_CTRL     DateMask!/DateTimeMask!/DatePicker/DDCalendar vadīklas (pārbaudīt, kā ievades vadīklas uzvedas)
    DATE_PARSE    Date()/DateTime()/IsDate()/RelativeDate() - teksta pārvēršana datumā (atkarīga no reģ. iestatījumiem)
    POS_PARSE     datuma teksta sadalīšana pēc pozīcijām: Mid(s,4,2), Left(s,2), Right(s,4) ...
    DATE_LITERAL  datums kā teksta konstante: '31.12.2025', '2025-12-31'
    DB_FMT        datubāzes formāts: to_char/convert/date_format/date_order/dateformat/...
    IO_DATE       ImportFile/ImportString/SaveAs/ExportString... (datumi failos)
    ENV           reģionālo iestatījumu nolasīšana: GetEnvironment, RegistryGet, GetLocaleInfo, SetThreadLocale
Kolonna order: DMY / MDY / YMD / YM / MY / DM / MD  (maskas secība);  action: ko ar to darīt.
Kolonna input_event = yes, ja atradums ir ItemChanged/EditChanged/Modified/LosingFocus/ItemError notikumā
(tur visticamāk atrodas ievades parsēšanas loģika).
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
    ("REGIONAL",     re.compile(r"\[\s*(shortdate|longdate|general|time)\s*\]", re.I)),
    ("MASK_CTRL",    re.compile(r"\b(DateMask!|DateTimeMask!|DatePicker|DDCalendar|MaskDataType)\b", re.I)),
    ("DATE_PARSE",   re.compile(r"\b(Date|DateTime|IsDate|RelativeDate)\s*\(", re.I)),
    ("DATE_LITERAL", re.compile(r"[\"']\s*(\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{4})(\s+[\d:]+)?\s*[\"']")),
    ("DB_FMT",       re.compile(r"\b(to_char|to_date|convert|dateformat|date_format|date_order|nearest_century|datepart|"
                                r"set\s+(temporary\s+)?option)\b", re.I)),
    ("IO_DATE",      re.compile(r"\b(ImportFile|ImportString|ImportClipboard|SaveAs|ExportString|FileRead|FileWrite)\s*\(", re.I)),
    ("ENV",          re.compile(r"\b(GetEnvironment|RegistryGet|GetLocaleInfo|SetThreadLocale|sShortDate|iDate)\b", re.I)),
]
POS_RE = re.compile(r"\bMid\s*\(\s*[^,()]+,\s*[3-9]\s*,\s*[24]\s*\)|\b(Left\s*\([^,()]+,\s*[24]\s*\)|Right\s*\([^,()]+,\s*[24]\s*\))", re.I)
DATEWORD_RE = re.compile(r"(dat|date|datum|dt|dd|mm|yy|yyyy|gads|menes|mēnes|diena|period)", re.I)

ACTION = {"DMY": "MAINĪT (dd.mm.yyyy -> mm/dd/yyyy)", "DM": "MAINĪT", "MDY": "OK (jau ASV)", "MD": "OK (jau ASV)",
          "YMD": "PĀRBAUDĪT (ISO/DB/fails?)", "YM": "PĀRBAUDĪT (mēnesis/gads)", "MY": "PĀRBAUDĪT (mēnesis/gads)"}
KIND_ACTION = {"REGIONAL": "OK (seko Windows)", "MASK_CTRL": "PĀRBAUDĪT vadīklu", "DATE_PARSE": "PĀRBAUDĪT (reģ. iestatījumi)",
               "POS_PARSE": "PĀRBAUDĪT (parsē pēc pozīcijām)", "DATE_LITERAL": "PĀRBAUDĪT (secība literālī)",
               "DB_FMT": "PĀRBAUDĪT (DB formāts)", "IO_DATE": "PĀRBAUDĪT (datumi failos)", "ENV": "PĀRBAUDĪT"}


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
    """Atgriež [(maska, secība, pozīcija)] - datuma maskas (dd.mm.yyyy, mm/dd/yyyy, ddmmyyyy, yyyy.mm ...)."""
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
    """DataWindow (.srd): date/datetime/timestamp kolonnas no table(...) + atbilstošais attēlotais lauks column(...)."""
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
                     "radiobuttons" if "radiobuttons." in d.lower() else "edit") if d else "(nav attēlots)"
            ddcal = attr(d, "editmask.ddcalendar")
            tabseq = attr(d, "tabsequence")
            out.append(dict(column=name, type=typ, dbname=dbm.group(1) if dbm else "", format=fmt, style=style,
                            mask=mask, ddcalendar=ddcal, tabseq=tabseq))
        for m in ARG_RE.finditer(ln):
            if re.search(r"\barguments\s*=", ln, re.I) or ln.lstrip().startswith("("):
                out.append(dict(column=m.group(1), type=m.group(2).lower() + " (retrieval arg)", dbname="", format="",
                                style="", mask="", ddcalendar="", tabseq=""))
    return out


def dw_action(c):
    if "arg" in c["type"]:
        return "PĀRBAUDĪT (retrieval arguments - datums ievadā/izsaukumā)"
    for txt in (c["mask"], c["format"]):
        ms = find_masks(txt) if txt else []
        if ms:
            return ACTION.get(ms[0][1], "PĀRBAUDĪT")
    if c["format"].startswith("[") or not c["format"]:
        return "OK (seko Windows)" if c["style"] != "(nav attēlots)" else "PĀRBAUDĪT (nav attēlots)"
    return "PĀRBAUDĪT"


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
                print(f"Nevar nolasīt {rel}: {e}", file=sys.stderr); continue
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
                s = stmt.replace("~\"", "\"").replace("~'", "'")
                scope = scope_at.get(start, "")
                inp = "yes" if INPUT_EVENT_RE.search(scope) else ""
                code = re.sub(r"\s+", " ", stmt).strip()[:400]
                found = []                                    # (kind, mask, order, action)
                masks = find_masks(s)
                if masks:
                    for mask, order, pos in masks:
                        found.append((mask_kind(s, is_srd, pos), mask, order, ACTION.get(order, "PĀRBAUDĪT")))
                for kind, rx in OTHER:
                    if rx.search(s):
                        if kind == "DATE_PARSE" and is_srd and not re.search(r"expression\s*=|initial|validation", s, re.I):
                            continue
                        found.append((kind, "", "", KIND_ACTION[kind]))
                if POS_RE.search(s) and DATEWORD_RE.search(s):
                    found.append(("POS_PARSE", "", "", KIND_ACTION["POS_PARSE"]))
                seen = set()
                for kind, mask, order, action in found:
                    key = (kind, mask)
                    if key in seen: continue
                    seen.add(key)
                    rows.append([kind, action, rel, start + 1, scope, inp, mask, order, code])

    prio = {"MAINĪT": 0, "PĀRBAUDĪT": 1, "OK": 2}
    rows.sort(key=lambda r: (prio.get(r[1].split(" ")[0], 3), r[0], r[2], r[3]))
    with open(a.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["kind", "action", "file", "line", "function_or_event", "input_event", "mask", "order", "code"])
        w.writerows(rows)

    masks = defaultdict(lambda: {"n": 0, "files": set(), "kinds": set(), "order": ""})
    for r in rows:
        if r[6]:
            d = masks[r[6]]; d["n"] += 1; d["files"].add(r[2]); d["kinds"].add(r[0]); d["order"] = r[7]
    base, ext = os.path.splitext(a.out)
    summ = f"{base}_summary{ext or '.csv'}"
    with open(summ, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["mask", "order", "action", "count", "files", "kinds"])
        for m, d in sorted(masks.items(), key=lambda x: -x[1]["n"]):
            w.writerow([m, d["order"], ACTION.get(d["order"], "PĀRBAUDĪT"), d["n"], len(d["files"]), ",".join(sorted(d["kinds"]))])

    dwf = f"{base}_dwcolumns{ext or '.csv'}"
    dwcols.sort(key=lambda r: (r[0].lower(), r[1].lower()))
    with open(dwf, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["datawindow_file", "column", "type", "dbname", "edit_style", "format", "mask", "ddcalendar",
                    "tabsequence", "action"])
        w.writerows(dwcols)

    if not a.quiet:
        print(f"Skenēti faili: {files_scanned}, atradumi: {len(rows)}  ->  {a.out}, {summ}\n")
        by_kind = defaultdict(set); cnt = defaultdict(int)
        for r in rows: cnt[r[0]] += 1; by_kind[r[0]].add(r[2])
        print(f"{'kind':14} {'atradumi':>9} {'faili':>6}")
        for k in sorted(cnt, key=lambda k: -cnt[k]): print(f"{k:14} {cnt[k]:>9} {len(by_kind[k]):>6}")
        print(f"\n{'maska':22} {'secība':7} {'skaits':>7} {'faili':>6}  action")
        for m, d in sorted(masks.items(), key=lambda x: -x[1]["n"]):
            print(f"{m:22} {d['order']:7} {d['n']:>7} {len(d['files']):>6}  {ACTION.get(d['order'], 'PĀRBAUDĪT')}")
        n_inp = sum(1 for r in rows if r[5])
        if n_inp: print(f"\nAtradumi ievades notikumos (ItemChanged/EditChanged/...): {n_inp}  (kolonna input_event)")


if __name__ == "__main__":
    main()
