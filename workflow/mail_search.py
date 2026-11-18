#!/usr/bin/env python3
"""Alfred Script Filter: fast search in Apple Mail via its Envelope Index.

Searches subject, sender (name + address), recipients and the message preview.
Multiple words must all match. Prefixes limit the field:
  from:jane   to:jane   subject:invoice
Test: ./mail_search.py invoice   |   ./mail_search.py --schema   (lists tables/columns)
"""
import sys, os, re, json, glob, sqlite3, datetime
from urllib.parse import quote, unquote

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


def bouw_where(tokens, X):
    """X holds the SQL expressions for this database layout (see schema())."""
    delen, params = [], []

    def like(expr):
        return "%s LIKE ? ESCAPE '\\'" % expr

    for tok in tokens:
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
    if veld:
        query = " ".join(w if re.match(r"^\w+:", w) else veld + ":" + w for w in query.split())
    hint = {"van": "sender (name or address)", "from": "sender (name or address)",
            "aan": "recipient (name or address)", "to": "recipient (name or address)",
            "onderwerp": "word from the subject", "subject": "word from the subject"}
    for woord in query.split():
        m = re.match(r"^(van|from|aan|to|onderwerp|subject):$", woord, re.I)
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
