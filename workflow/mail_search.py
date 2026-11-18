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


def bouw_where(tokens, heeft_ontv, o_msg, o_adr, heeft_summ):
    delen, params = [], []
    for tok in tokens:
        veld = None
        m = re.match(r"^(van|from|aan|to|onderwerp|subject):(.+)$", tok, re.I)
        if m:
            veld = {"van": "from", "from": "from", "aan": "to", "to": "to",
                    "onderwerp": "subject", "subject": "subject"}[m.group(1).lower()]
            tok = m.group(2)
        p = esc(tok)
        sub_ontv = ("EXISTS (SELECT 1 FROM recipients r JOIN addresses ra ON ra.ROWID = r.%s "
                    "WHERE r.%s = m.ROWID AND (ra.address LIKE ? ESCAPE '\\' OR ra.comment LIKE ? ESCAPE '\\'))"
                    % (o_adr, o_msg)) if heeft_ontv else "0"
        if veld == "from":
            delen.append("(a.address LIKE ? ESCAPE '\\' OR a.comment LIKE ? ESCAPE '\\')")
            params += [p, p]
        elif veld == "to":
            delen.append("(%s)" % sub_ontv)
            params += [p, p] if heeft_ontv else []
        elif veld == "subject":
            delen.append("(s.subject LIKE ? ESCAPE '\\')")
            params += [p]
        else:
            stukken = ["s.subject LIKE ? ESCAPE '\\'",
                       "a.address LIKE ? ESCAPE '\\'",
                       "a.comment LIKE ? ESCAPE '\\'"]
            params += [p, p, p]
            if heeft_summ:
                stukken.append("sm.summary LIKE ? ESCAPE '\\'")
                params.append(p)
            stukken.append(sub_ontv)
            if heeft_ontv:
                params += [p, p]
            delen.append("(" + " OR ".join(stukken) + ")")
    return " AND ".join(delen), params


def datum_tekst(ts):
    try:
        d = datetime.datetime.fromtimestamp(ts)
    except Exception:
        return ""
    nu = datetime.datetime.now()
    if d.date() == nu.date():
        return "today %02d:%02d" % (d.hour, d.minute)
    if d.year == nu.year:
        return "%s %d" % (MONTHS[d.month - 1])
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

    if args and args[0] == "--debug":
        con = verbind(pad)
        r = con.execute("SELECT ROWID, sender, subject FROM messages WHERE deleted=0 ORDER BY date_received DESC LIMIT 1").fetchone()
        print("laatste bericht (ROWID, sender, subject):", r)
        sid = r[1]
        for naam, q in [("senders", "SELECT * FROM senders WHERE ROWID=%d" % sid),
                        ("sender_addresses", "SELECT * FROM sender_addresses WHERE sender=%d" % sid),
                        ("addresses met ROWID=sender", "SELECT * FROM addresses WHERE ROWID=%d" % sid),
                        ("addresses via sender_addresses", "SELECT a.* FROM addresses a JOIN sender_addresses sa ON sa.address=a.ROWID WHERE sa.sender=%d" % sid),
                        ("sender_addresses voorbeeld", "SELECT * FROM sender_addresses LIMIT 3"),
                        ("addresses voorbeeld", "SELECT * FROM addresses LIMIT 3")]:
            try:
                print(naam, "->", con.execute(q).fetchall())
            except Exception as e:
                print(naam, "FOUT", e)
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

    tabs = tabellen(con)
    mcols = kolommen(con, "messages")
    heeft_hdr = "message_global_data" in tabs and "message_id_header" in kolommen(con, "message_global_data") \
        and "global_message_id" in mcols
    heeft_summ = "summaries" in tabs and "summary" in mcols
    heeft_ontv = "recipients" in tabs
    o_msg = o_adr = None
    if heeft_ontv:
        rc = kolommen(con, "recipients")
        o_msg = "message" if "message" in rc else ("message_id" if "message_id" in rc else None)
        o_adr = "address" if "address" in rc else ("address_id" if "address_id" in rc else None)
        heeft_ontv = bool(o_msg and o_adr)

    via_sa = False
    if "sender_addresses" in tabs:
        try:
            via_sa = con.execute("SELECT 1 FROM messages m JOIN sender_addresses sa ON sa.sender = m.sender LIMIT 1").fetchone() is not None
        except sqlite3.Error:
            via_sa = False
    tokens = query.split()
    where, params = bouw_where(tokens, heeft_ontv, o_msg, o_adr, heeft_summ)
    deleted = "m.deleted = 0 AND " if "deleted" in mcols else ""
    prefix = "COALESCE(m.subject_prefix, '')" if "subject_prefix" in mcols else "''"
    sql = """
        SELECT %s, s.subject, a.address, a.comment, m.date_received, mb.url,
               %s, %s, %s
        FROM messages m
        LEFT JOIN subjects s ON s.ROWID = m.subject
        %s
        LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox
        %s %s
        WHERE %s%s
        ORDER BY m.date_received DESC LIMIT 400
    """ % (prefix,
           "g.message_id_header" if heeft_hdr else "NULL",
           "sm.summary" if heeft_summ else "NULL",
           "m.read" if "read" in mcols else "1",
           "LEFT JOIN addresses a ON a.ROWID = m.sender",
           "LEFT JOIN message_global_data g ON g.ROWID = m.global_message_id" if heeft_hdr else "",
           "LEFT JOIN summaries sm ON sm.ROWID = m.summary" if heeft_summ else "",
           deleted, where)
    try:
        rijen = con.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        uit([melding("Search failed", "%s – run ./mail_search.py --schema in the workflow folder" % e)])

    gezien, items = {}, []
    for pre, onderwerp, adres, naam, ts, url, hdr, summ, gelezen in rijen:
        box = boxnaam(url)
        if box.lower() in SKIP_BOXEN:
            continue
        sleutel = hdr or ("rij%d" % len(gezien))
        if sleutel in gezien:
            oud = gezien[sleutel]
            if oud["alle"] and box.lower() not in ALLE_BOXEN:
                items[oud["i"]] = None
            else:
                continue
        titel = ((pre or "") + (onderwerp or "")).strip() or "(no subject)"
        if not gelezen:
            titel = "● " + titel
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
            item["valid"] = False
            item["subtitle"] += "  ·  (no message id, cannot open)"
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
