#!/usr/bin/env python3
"""Alfred Script Filter: fast search in Apple Mail via its Envelope Index.

Searches subject, sender (name + address), recipients and the message preview.
Multiple words must all match. Prefixes limit the field:
  from:jane   to:jane   subject:invoice
Test: ./mail_search.py invoice   |   ./mail_search.py --schema   (lists tables/columns)
"""
import sys, os, re, json, glob, sqlite3, datetime, html, tempfile, time
from urllib.parse import quote, unquote, urlparse

LIMIET = 30
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MAIL_ICOON = {"type": "fileicon", "path": "/System/Applications/Mail.app"}
SKIP_BOXEN = {"junk", "spam", "trash", "deleted messages", "deleted items",
              "bin", "deleted"}
ALLE_BOXEN = {"all mail", "alle berichten", "archive"}


def uit(items):
    print(json.dumps({"items": items}))
    sys.exit(0)


def melding(titel, sub="", arg=None, ok=False):
    item = {"title": titel, "subtitle": sub, "valid": ok, "icon": MAIL_ICOON}
    if arg:
        item["arg"] = arg
    return item


def vind_db():
    pad = os.environ.get("MAIL_DB")
    if pad:
        return pad
    kand = glob.glob(os.path.expanduser("~/Library/Mail/V*/MailData/Envelope Index"))
    kand.sort(key=lambda p: int(re.search(r"/V(\d+)/", p).group(1)))
    return kand[-1] if kand else None


def verbind(pad):
    for opties in ("mode=ro", "immutable=1"):
        try:
            con = sqlite3.connect("file:%s?%s" % (quote(pad), opties), uri=True, timeout=2)
            con.execute("select 1 from messages limit 1")
            return con
        except sqlite3.Error:
            continue
    return None


def kolommen(con, tabel):
    return {r[1] for r in con.execute("pragma table_info(%s)" % tabel)}


def tabellen(con):
    return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}


def esc(woord):
    return "%" + woord.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def datum_bereik(tekst):
    """Turn a date word into (start, end) unix times. Accepts today/yesterday/week/month,
    2026-11-18, 18-11-2026, 18-11 (this year), 2026-11, 11-2026 and 2026."""
    t = tekst.strip().lower()
    dag = datetime.timedelta(days=1)
    nul = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        if t in ("today", "vandaag"):
            a, b = nul, nul + dag
        elif t in ("yesterday", "gisteren"):
            a, b = nul - dag, nul
        elif t == "week":
            a, b = nul - 7 * dag, nul + dag
        elif t in ("month", "maand"):
            a, b = nul - 30 * dag, nul + dag
        else:
            d = re.split(r"[-/.]", t)
            if not all(x.isdigit() for x in d):
                return None
            n = [int(x) for x in d]

            def maand(j, m):
                return datetime.datetime(j, m, 1), datetime.datetime(j + (m == 12), m % 12 + 1, 1)

            if len(d) == 1 and len(d[0]) == 4:
                a, b = datetime.datetime(n[0], 1, 1), datetime.datetime(n[0] + 1, 1, 1)
            elif len(d) == 2 and len(d[0]) == 4:
                a, b = maand(n[0], n[1])
            elif len(d) == 2 and len(d[1]) == 4:
                a, b = maand(n[1], n[0])
            elif len(d) == 2:
                a = datetime.datetime(nul.year, n[1], n[0])
                b = a + dag
            elif len(d) == 3 and len(d[0]) == 4:
                a = datetime.datetime(n[0], n[1], n[2])
                b = a + dag
            elif len(d) == 3:
                a = datetime.datetime(n[2] + (2000 if n[2] < 100 else 0), n[1], n[0])
                b = a + dag
            else:
                return None
    except ValueError:
        return None
    return int(a.timestamp()), int(b.timestamp())


DATUM_RE = r"^(date|datum|after|since|na|sinds|before|voor):(.+)$"


def datum_where(tok, kolom, delen, params):
    """Handle date:/after:/before: tokens. Returns True when the token was a date filter."""
    md = re.match(DATUM_RE, tok, re.I)
    if not md:
        return False
    bereik = datum_bereik(md.group(2))
    if not bereik:
        uit([melding("Unknown date “%s”" % md.group(2), "Use today, yesterday, week, month, 2026-11-18, 18-11-2026, 2026-11 or 2026")])
    soort = md.group(1).lower()
    if soort in ("date", "datum"):
        delen.append("(%s >= ? AND %s < ?)" % (kolom, kolom))
        params.extend(bereik)
    elif soort in ("before", "voor"):
        delen.append("(%s < ?)" % kolom)
        params.append(bereik[0])
    else:
        delen.append("(%s >= ?)" % kolom)
        params.append(bereik[0])
    return True


def bouw_where(tokens, X):
    """X holds the SQL expressions for this database layout (see schema())."""
    delen, params = [], []

    def like(expr):
        return "%s LIKE ? ESCAPE '\\'" % expr

    for tok in tokens:
        if datum_where(tok, X["date"], delen, params):
            continue
        veld = None
        m = re.match(r"^(van|from|aan|to|onderwerp|subject):(.+)$", tok, re.I)
        if m:
            veld = {"van": "from", "from": "from", "aan": "to", "to": "to",
                    "onderwerp": "subject", "subject": "subject"}[m.group(1).lower()]
            tok = m.group(2)
        p = esc(tok)
        afz = [like(e) for e in (X["addr"], X["name"]) if e]
        ontv, n_ontv = "0", 0
        if X["rcpt"]:
            o_msg, o_adr, heeft_comment = X["rcpt"]
            velden = ["ra.address"] + (["ra.comment"] if heeft_comment else [])
            ontv = ("EXISTS (SELECT 1 FROM recipients r JOIN addresses ra ON ra.ROWID = r.%s "
                    "WHERE r.%s = m.ROWID AND (%s))" % (o_adr, o_msg, " OR ".join(like(v) for v in velden)))
            n_ontv = len(velden)
        if veld == "from":
            delen.append("(" + (" OR ".join(afz) or "0") + ")")
            params += [p] * len(afz)
        elif veld == "to":
            delen.append("(%s)" % ontv)
            params += [p] * n_ontv
        elif veld == "subject":
            delen.append("(%s)" % like(X["subj"]))
            params += [p]
        else:
            stukken = [like(X["subj"])] + afz
            params += [p] * len(stukken)
            if X["summ"]:
                stukken.append(like(X["summ"]))
                params.append(p)
            stukken.append(ontv)
            params += [p] * n_ontv
            delen.append("(" + " OR ".join(stukken) + ")")
    return " AND ".join(delen), params


def schema(con):
    """Work out where things live. Mail's Envelope Index changed over macOS versions:
    newer versions keep subject/sender/summary in lookup tables and store the
    Message-ID header; older ones keep text in `messages` and have no header."""
    tabs = tabellen(con)
    mcols = kolommen(con, "messages")
    acols = kolommen(con, "addresses") if "addresses" in tabs else set()
    try:
        t_subj, t_send = con.execute(
            "SELECT typeof(subject), typeof(sender) FROM messages "
            "WHERE subject IS NOT NULL AND sender IS NOT NULL LIMIT 1").fetchone() or ("integer", "integer")
    except sqlite3.Error:
        t_subj = t_send = "integer"
    X = {"joins": [], "rcpt": None}
    if "subjects" in tabs and t_subj == "integer":
        X["subj"] = "s.subject"
        X["joins"].append("LEFT JOIN subjects s ON s.ROWID = m.subject")
    else:
        X["subj"] = "m.subject"
    if "address" in acols and t_send == "integer":
        X["addr"] = "a.address"
        X["name"] = "a.comment" if "comment" in acols else None
        X["joins"].append("LEFT JOIN addresses a ON a.ROWID = m.sender")
    else:
        X["addr"], X["name"] = "m.sender", None
    if "summaries" in tabs and "summary" in mcols:
        X["summ"] = "sm.summary"
        X["joins"].append("LEFT JOIN summaries sm ON sm.ROWID = m.summary")
    elif "snippet" in mcols:
        X["summ"] = "m.snippet"
    else:
        X["summ"] = None
    if "message_global_data" in tabs and "global_message_id" in mcols \
            and "message_id_header" in kolommen(con, "message_global_data"):
        X["hdr"] = "g.message_id_header"
        X["joins"].append("LEFT JOIN message_global_data g ON g.ROWID = m.global_message_id")
    else:
        X["hdr"] = None
    if "mailboxes" in tabs and "mailbox" in mcols and "url" in kolommen(con, "mailboxes"):
        X["box"] = "mb.url"
        X["joins"].append("LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox")
    else:
        X["box"] = None
    if "recipients" in tabs and "address" in acols:
        rc = kolommen(con, "recipients")
        o_msg = "message" if "message" in rc else ("message_id" if "message_id" in rc else None)
        o_adr = "address" if "address" in rc else ("address_id" if "address_id" in rc else None)
        if o_msg and o_adr:
            X["rcpt"] = (o_msg, o_adr, "comment" in acols)
    X["date"] = "m.date_received" if "date_received" in mcols else ("m.date_sent" if "date_sent" in mcols else "0")
    X["prefix"] = "COALESCE(m.subject_prefix, '')" if "subject_prefix" in mcols else "''"
    X["read"] = "m.read" if "read" in mcols else "1"
    X["deleted"] = "m.deleted = 0 AND " if "deleted" in mcols else ""
    return X


def datum_tekst(ts):
    try:
        d = datetime.datetime.fromtimestamp(ts)
    except Exception:
        return ""
    nu = datetime.datetime.now()
    if d.date() == nu.date():
        return "today %02d:%02d" % (d.hour, d.minute)
    if d.year == nu.year:
        return "%s %d" % (MONTHS[d.month - 1], d.day)
    return "%d %s %d" % (d.day, MONTHS[d.month - 1], d.year)


def boxnaam(url):
    if not url:
        return ""
    return unquote(url.rstrip("/").split("/")[-1])


def datum_keuzes(getypt):
    """Preset list for the date keyword, so nothing has to be typed out."""
    nu = datetime.datetime.now()
    vm = nu.replace(day=1) - datetime.timedelta(days=1)
    keuzes = [("Today", "today"), ("Yesterday", "yesterday"), ("Last 7 days", "week"), ("Last 30 days", "month"),
            ("This month", "%d-%02d" % (nu.year, nu.month)), ("Last month", "%d-%02d" % (vm.year, vm.month)),
            ("This year", str(nu.year)), ("Last year", str(nu.year - 1))]
    t = getypt.lower()
    items = []
    for label, waarde in keuzes:
        if t and not (label.lower().startswith(t) or waarde.startswith(t)):
            continue
        items.append({"title": label, "subtitle": "Enter or Tab to choose, then add search words if you like. Or type a date: 2026-11-18", "autocomplete": waarde + " ",
                      "valid": False, "icon": MAIL_ICOON})
    return items


def emlx_pad(db_pad, url, rowid):
    """Find the message file for Quick Look: <Mail>/V*/<account>/<box>.mbox/*/Data/<digits>/Messages/<id>.emlx"""
    try:
        u = urlparse(url or "")
        delen = [unquote(d) for d in u.path.split("/") if d]
        if not u.netloc or not delen:
            return None
        basis = os.path.dirname(os.path.dirname(db_pad))
        mbox = os.path.join(basis, u.netloc, *[d + ".mbox" for d in delen])
        cijfers = list(reversed(str(rowid // 1000))) if rowid >= 1000 else []
        patroon = os.path.join(glob.escape(mbox), "*", "Data", *cijfers, "Messages", "%d.*emlx" % rowid)
        gevonden = glob.glob(patroon)
        return gevonden[0] if gevonden else None
    except Exception:
        return None


def voorbeeld(db_pad, url, rowid, titel, afz, datum, box, summ):
    """Path for Alfred's Quick Look (Shift or Cmd+Y): the real message, else a small summary page."""
    echt = emlx_pad(db_pad, url, rowid)
    if echt:
        return echt
    try:
        map_ = os.environ.get("alfred_workflow_cache") or os.path.join(tempfile.gettempdir(), "fast-mail-search")
        os.makedirs(map_, exist_ok=True)
        doel = os.path.join(map_, "preview-%d.html" % rowid)
        e = html.escape
        with open(doel, "w", encoding="utf-8") as f:
            f.write("<!doctype html><meta charset='utf-8'><body style='font:15px -apple-system,sans-serif;margin:28px;"
                    "line-height:1.5'><h2 style='margin:0 0 12px'>%s</h2><p style='color:#666;margin:0 0 18px'>"
                    "From: %s<br>Date: %s<br>Mailbox: %s</p><p>%s</p><p style='color:#999;font-size:12px'>Preview only: the full message file was not found on this Mac.</p>"
                    % (e(titel), e(afz), e(datum), e(box), e(summ or ""),))
        return doel
    except Exception:
        return None


def ruim_voorbeelden():
    """Remove summary pages older than a day."""
    try:
        map_ = os.environ.get("alfred_workflow_cache") or os.path.join(tempfile.gettempdir(), "fast-mail-search")
        grens = time.time() - 86400
        for f in glob.glob(os.path.join(glob.escape(map_), "preview-*.html")):
            if os.path.getmtime(f) < grens:
                os.remove(f)
    except Exception:
        pass


def main():
    veld = None
    args = []
    for a in sys.argv[1:]:
        if a.startswith("--field="):
            veld = a.split("=", 1)[1]
        elif a:
            args.append(a)
    pad = vind_db()

    if args and args[0] == "--schema":
        con = verbind(pad)
        for t in sorted(tabellen(con)):
            print(t, sorted(kolommen(con, t)))
        return

    query = " ".join(args).strip()
    if veld in ("date", "datum"):
        woorden = query.split()
        if not woorden or (len(woorden) == 1 and not query.endswith(" ") and not datum_bereik(woorden[0])
                           and ":" not in woorden[0]):
            keuzes = datum_keuzes(woorden[0] if woorden else "")
            if keuzes:
                uit(keuzes)
    if veld:
        query = " ".join(w if re.match(r"^\w+:", w) or (veld in ("date", "datum") and not datum_bereik(w))
                         else veld + ":" + w for w in query.split())
    hint = {"van": "sender (name or address)", "from": "sender (name or address)",
            "aan": "recipient (name or address)", "to": "recipient (name or address)",
            "onderwerp": "word from the subject", "subject": "word from the subject"}
    for k in ("date", "datum", "after", "since", "na", "sinds", "before", "voor"):
        hint[k] = "date (today, yesterday, 2026-11-18, 2026-11)"
    for woord in query.split():
        m = re.match(r"^(van|from|aan|to|onderwerp|subject|date|datum|after|since|na|sinds|before|voor):$", woord, re.I)
        if m:
            uit([melding("Type a %s after %s:" % (hint[m.group(1).lower()], m.group(1).lower()),
                         "For example  %s:jane" % m.group(1).lower())])
    if not pad:
        uit([melding("Mail database not found", "Is Apple Mail set up on this Mac?")])
    if len(" ".join(args).strip()) < 2:
        uit([melding("Search Mail…",
                     "Name, email address, subject or text. Tip: from:jane  to:jane  subject:invoice")])

    try:
        con = verbind(pad)
    except Exception:
        con = None
    if con is None:
        uit([melding("Alfred cannot access Mail",
                     "Give Alfred Full Disk Access (Enter opens System Settings)",
                     "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles", True)])

    X = schema(con)
    where, params = bouw_where(query.split(), X)
    sql = """
        SELECT m.ROWID, %s, %s, %s, %s, %s, %s, %s, %s, %s
        FROM messages m
        %s
        WHERE %s%s
        ORDER BY %s DESC LIMIT 400
    """ % (X["prefix"], X["subj"], X["addr"], X["name"] or "NULL", X["date"], X["box"] or "NULL",
           X["hdr"] or "NULL", X["summ"] or "NULL", X["read"],
           "\n        ".join(X["joins"]), X["deleted"], where, X["date"])
    try:
        rijen = con.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        uit([melding("Search failed", "%s – run ./mail_search.py --schema in the workflow folder" % e)])

    gezien, items = {}, []
    for rowid, pre, onderwerp, adres, naam, ts, url, hdr, summ, gelezen in rijen:
        box = boxnaam(url)
        if box.lower() in SKIP_BOXEN:
            continue
        sleutel = hdr or ("row%d" % rowid)
        if sleutel in gezien:
            oud = gezien[sleutel]
            if oud["alle"] and box.lower() not in ALLE_BOXEN:
                items[oud["i"]] = None
            else:
                continue
        titel = ((pre or "") + (onderwerp or "")).strip() or "(no subject)"
        if not gelezen:
            titel = "● " + titel
        if adres and not naam:
            mm = re.match(r'^\s*"?(.*?)"?\s*<([^>]+)>\s*$', str(adres))
            if mm:
                naam, adres = mm.group(1), mm.group(2)
        afz = ("%s <%s>" % (naam, adres)) if naam and adres and naam != adres else (adres or naam or "")
        item = {
            "uid": sleutel,
            "title": titel,
            "subtitle": "%s  ·  %s  ·  %s" % (afz, datum_tekst(ts), box),
            "icon": MAIL_ICOON,
            "_ql": (url, rowid, titel.lstrip("● "), afz, datum_tekst(ts), box, summ),
            "text": {"largetype": (summ or titel), "copy": titel},
            "variables": {"action": "open"},
            "mods": {"cmd": {"subtitle": "Copy sender email address (%s)" % (adres or ""),
                             "arg": adres or "", "valid": bool(adres),
                             "variables": {"action": "copy"}}},
        }
        if hdr:
            h = hdr.strip()
            if not h.startswith("<"):
                h = "<" + h + ">"
            item["arg"] = "message://" + quote(h, safe="@._+-")
        else:
            # Older Mail databases do not store the Message-ID header: open the message file instead.
            item["arg"] = "emlx:%d" % rowid
        gezien[sleutel] = {"alle": box.lower() in ALLE_BOXEN, "i": len(items)}
        items.append(item)
    items = [i for i in items if i][:LIMIET]
    ruim_voorbeelden()
    for i in items:
        ql = voorbeeld(pad, *i.pop("_ql"))
        if ql:
            i["quicklookurl"] = ql
    if not items:
        uit([melding("No mail found for “%s”" % query, "Try a shorter search term")])
    uit(items)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        uit([melding("Script error", "%s: %s" % (type(e).__name__, e))])
