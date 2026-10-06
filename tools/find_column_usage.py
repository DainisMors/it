#!/usr/bin/env python3
"""
Meklē PowerBuilder (2025 R2) eksportēto avotu failos (.srw .sru .srd .srf .srm .srs .sra .srq .srp .srj .srx ...)
darbības ar laukiem, kas uzskaitīti columns.txt (nosaukumi atdalīti ar komatu).

Lietošana:
    python find_column_usage.py [--root .] [--columns columns.txt] [--out column_usage.csv]
                                [--ext .srw,.sru,...] [--case-sensitive] [--quiet]

Katram atradumam nosaka veidu (kind):
    DW_DEF  - lauka definīcija DataWindow (.srd): name=..., dbname="tab.col"
    SQL     - SELECT/INSERT/UPDATE/DELETE/WHERE/ORDER BY/GROUP BY teikums (PowerScript vai DW retrieve)
    SET     - vērtības piešķiršana: SetItem*, .object.col[r] = ..., Modify, UPDATE/INSERT
    GET     - vērtības nolasīšana: GetItem*, .object.col[r], Describe, Find/Filter/Sort izteiksmes
    DATE_OP - datuma/virknes manipulācija ar to pašu teikumu: Date(), String(.., 'yyyy.mm'), Left/Mid/Right,
              RelativeDate, Month/Year/Day, Replace, Integer, ...
    REF     - cits pieminējums
Izvade: CSV (UTF-8 ar BOM, atveras Excel) + kopsavilkums konsolē.
"""
import argparse, csv, os, re, sys
from collections import defaultdict

DEFAULT_EXT = ".srw .sru .srd .srf .srm .srs .sra .srq .srp .srj .srx".split()
SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__"}

SQL_RE  = re.compile(r"\b(select|insert\s+into|update|delete\s+from|where|order\s+by|group\s+by|having|union|"
                     r"declare\s+\w+\s+cursor|execute\s+immediate)\b", re.I)
DATE_RE = re.compile(r"\b(date|datetime|time|string|relativedate|relativetime|year|month|day|today|now|"
                     r"daysafter|monthsafter|secondsafter|isdate|left|mid|right|pos|replace|integer|long|dec|"
                     r"to_char|to_date|datepart|dateformat|dateadd|datediff|convert|cast|substr|substring|"
                     r"trim|lefttrim|righttrim|fill|space)\s*\(", re.I)
HEAD_RE = re.compile(r"^\s*(?:(?:global|public|private|protected)\s+)*"
                     r"(?:(?:subroutine|function\s+\S+)\s+(\w+)\s*\(|event\s+(?:type\s+\S+\s+)?([\w.]+)|on\s+([\w.]+))", re.I)
END_RE  = re.compile(r"^\s*end\s+(function|subroutine|event|on)\b", re.I)
SQL_START = re.compile(r"^\s*(select|insert|update|delete|declare|execute|open|fetch|close|commit|rollback)\b", re.I)


def read_text(path):
    raw = open(path, "rb").read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if b"\x00" in raw[:200]:                       # UTF-16 bez BOM
        return raw.decode("utf-16-le", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1257", "replace")      # latviešu ANSI


def statements(lines):
    """Apvieno rindas teikumos: '&' turpinājums un daudzrindu SQL līdz ';'. Atgriež (sākuma_rinda, [rindu_indeksi], teksts)."""
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


def classify(stmt, col_re, col, is_srd):
    kinds = []
    esc = re.escape(col)
    q = r"[\"'~]*"
    if is_srd and (re.search(r"\bname\s*=\s*" + esc + r"\b", stmt, re.I) or
                   re.search(r"dbname\s*=\s*[\"~]*[\w.$#%]*\b" + esc + r"\b", stmt, re.I)):
        kinds.append("DW_DEF")
    if SQL_RE.search(stmt):
        kinds.append("SQL")
    if (re.search(r"SetItem\w*\s*\([^)]*[\"']" + esc + r"[\"']", stmt, re.I) or
        re.search(r"\.object\.\s*" + esc + r"(\.\w+)?\s*(\[[^\]]*\])?\s*=(?!=)", stmt, re.I) or
        re.search(r"\.\s*" + esc + r"\s*\[[^\]]*\]\s*=(?!=)", stmt, re.I) or
        re.search(r"\bModify\s*\(", stmt, re.I) or
        re.search(r"^\s*(update|insert\s+into)\b", stmt, re.I)):
        kinds.append("SET")
    if "SET" in kinds and re.search(r"\.\s*" + esc + r"\s*\[[^\]]*\]\s*=(?!=)", stmt, re.I) \
            and not re.search(r"=.*(\.object\.|GetItem)", stmt, re.I):
        pass                                       # tīra piešķiršana - nav nolasīšana
    elif (re.search(r"GetItem\w*\s*\([^)]*[\"']" + esc + r"[\"']", stmt, re.I) or
        re.search(r"\.object\.\s*" + esc + r"\b", stmt, re.I) or
        re.search(r"\bDescribe\s*\(", stmt, re.I) or
        re.search(r"\b(Find|SetFilter|SetSort|GetChild|SetSQLSelect)\s*\(", stmt, re.I) or
        re.search(r"\.\s*" + esc + r"\s*\[", stmt, re.I)):
        kinds.append("GET")
    if DATE_RE.search(stmt) and not is_srd_noise(stmt):
        kinds.append("DATE_OP")
    return sorted(set(kinds)) or ["REF"]


def is_srd_noise(stmt):
    # DW definīcijas rindas (format=, font.face=, ...) satur 'date(' tikai kā stila daļu - neskaitām
    return bool(re.match(r"^\s*(text|column|compute|line|rectangle|group|header|summary|footer|detail)\s*\(", stmt, re.I)
                and "expression=" not in stmt.lower())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--columns", default="columns.txt")
    ap.add_argument("--out", default="column_usage.csv")
    ap.add_argument("--ext", default=",".join(DEFAULT_EXT), help="paplašinājumi, atdalīti ar komatu")
    ap.add_argument("--case-sensitive", action="store_true", help="PowerBuilder nav reģistrjutīgs, pēc noklusējuma ignorē reģistru")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    cols_text = read_text(a.columns)
    cols = sorted({c.strip() for c in re.split(r"[,\r\n]+", cols_text) if c.strip()})
    if not cols:
        sys.exit("columns.txt ir tukšs")
    flags = 0 if a.case_sensitive else re.I
    ident = r"[A-Za-z0-9_$#%]"                    # PB identifikatora simboli (bez '-')
    col_res = {c: re.compile(r"(?<!" + ident + r")" + re.escape(c) + r"(?!" + ident + r")", flags) for c in cols}
    exts = tuple(e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower() for e in a.ext.split(",") if e.strip())

    rows, per_col = [], defaultdict(lambda: defaultdict(int))
    files_scanned = 0
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
            scope_at = {}                          # rindas nr -> ietverošā funkcija/event
            cur = ""
            for i, ln in enumerate(lines):
                m = HEAD_RE.match(ln)
                if m: cur = next(g for g in m.groups() if g)
                scope_at[i] = cur
                if END_RE.match(ln): cur = ""
            for start, idx, stmt in statements(lines):
                for c, rx in col_res.items():
                    hit = next((j for j in idx if rx.search(lines[j])), None)
                    if hit is None:
                        continue
                    kinds = classify(stmt, rx, c, is_srd)
                    for k in kinds: per_col[c][k] += 1
                    rows.append([c, rel, hit + 1, scope_at.get(hit, ""), "|".join(kinds), re.sub(r"\s+", " ", stmt).strip()[:400]])
    # faili pa laukiem
    files_by_col = defaultdict(set)
    for r in rows: files_by_col[r[0]].add(r[1])

    rows.sort(key=lambda r: (r[0].lower(), r[1], r[2]))
    with open(a.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["column", "file", "line", "function_or_event", "kind", "code"])
        w.writerows(rows)

    if not a.quiet:
        print(f"Skenēti faili: {files_scanned}, lauki: {len(cols)}, atradumi: {len(rows)}  ->  {a.out}\n")
        print(f"{'column':30} {'files':>5} {'DW_DEF':>6} {'SQL':>5} {'SET':>5} {'GET':>5} {'DATE_OP':>7} {'REF':>5}")
        for c in cols:
            d = per_col.get(c, {})
            print(f"{c:30} {len(files_by_col.get(c, ())):>5} " + " ".join(
                f"{d.get(k, 0):>{w}}" for k, w in (("DW_DEF", 6), ("SQL", 5), ("SET", 5), ("GET", 5), ("DATE_OP", 7), ("REF", 5))))
        unused = [c for c in cols if c not in files_by_col]
        if unused:
            print("\nNeatrasti nevienā failā: " + ",".join(unused))


if __name__ == "__main__":
    main()
