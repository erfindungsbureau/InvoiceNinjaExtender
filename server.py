#!/usr/bin/env python3
"""
Invoice Ninja Erfolgsrechnung Export
Standalone HTTP server — generates PDF tax reports from Invoice Ninja data.
"""

import io, os, json, logging
import requests as req
from datetime import date, datetime
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_RIGHT, TA_CENTER

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_PATH = os.environ.get(
    "CONFIG_PATH",
    os.path.join(os.path.dirname(__file__), "config", "settings.json")
)
DEFAULT_CONFIG = {
    "in_url":              "https://your-invoiceninja.example.com/api/v1",
    "in_token":            "",
    "firma":               "Meine Firma",
    "name":                "Vorname Nachname",
    "excluded_categories": ["Material aus Lager"],
    "port":                5757,
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        # Fill missing keys with defaults
        for k, v in DEFAULT_CONFIG.items():
            data.setdefault(k, v)
        return data
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# ── Colors ────────────────────────────────────────────────────────────────────
C_DARK   = colors.HexColor("#1a1a2e")
C_ACCENT = colors.HexColor("#16213e")
C_LIGHT  = colors.HexColor("#f0f0f5")
C_MID    = colors.HexColor("#e0e0eb")
C_GREEN  = colors.HexColor("#2d6a4f")
C_RED    = colors.HexColor("#c1121f")
C_WHITE  = colors.white
C_CAT_BG = colors.HexColor("#dde0ee")

# ── API helpers ───────────────────────────────────────────────────────────────
def make_headers(token):
    return {"X-Api-Token": token, "Content-Type": "application/json"}

def api_get_all(base_url, token, path):
    headers = make_headers(token)
    results, page = [], 1
    while True:
        r = req.get(f"{base_url}{path}", headers=headers,
                    params={"per_page": 100, "page": page}, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            break
        results.extend(data)
        if len(data) < 100:
            break
        page += 1
    return results

def test_connection(base_url, token):
    """Returns (ok: bool, message: str)"""
    try:
        r = req.get(f"{base_url}/expense_categories",
                    headers=make_headers(token),
                    params={"per_page": 1}, timeout=10)
        if r.status_code == 200:
            return True, "Verbindung erfolgreich"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)

def get_categories(base_url, token):
    """Return list of {id, name} for active (non-deleted) expense categories only."""
    try:
        cats = api_get_all(base_url, token, "/expense_categories")
        return [{"id": c["id"], "name": c.get("name","?")}
                for c in cats if not c.get("is_deleted", False)]
    except Exception:
        return []

# ── Data loading ──────────────────────────────────────────────────────────────
def load_data(cfg, year):
    base  = cfg["in_url"]
    token = cfg["in_token"]
    excl  = set(cfg.get("excluded_categories", []))
    year_str = str(year)

    cats_map = {c["id"]: c.get("name","—")
                for c in api_get_all(base, token, "/expense_categories")
                if not c.get("is_deleted", False)}
    vendors_map = {v["id"]: v.get("name","—")
                   for v in api_get_all(base, token, "/vendors")}
    clients_map = {c["id"]: c.get("name","—")
                   for c in api_get_all(base, token, "/clients")}

    # Payments (revenue)
    payments = []
    for p in api_get_all(base, token, "/payments"):
        if p.get("is_deleted"):
            continue
        if not (p.get("date") or "").startswith(year_str):
            continue
        inv_nums, client_name = [], ""
        for pb in (p.get("paymentables") or []):
            inv = pb.get("invoice")
            if inv:
                inv_nums.append(inv.get("number", ""))
                if not client_name:
                    client_name = clients_map.get(inv.get("client_id",""), "")
        if not client_name:
            client_name = clients_map.get(p.get("client_id",""), "Nicht zugewiesen")
        payments.append({
            "date":    p.get("date","")[:10],
            "amount":  float(p.get("amount") or 0),
            "client":  client_name,
            "inv_num": ", ".join(filter(None, inv_nums)) or "—",
        })
    payments.sort(key=lambda x: x["date"])

    # Expenses
    expenses_by_cat = defaultdict(list)
    for e in api_get_all(base, token, "/expenses"):
        if e.get("is_deleted"):
            continue
        if not (e.get("date") or "").startswith(year_str):
            continue
        cid  = e.get("category_id") or ""
        name = cats_map.get(cid, "Unkategorisiert") if cid else "Unkategorisiert"
        if name in excl:
            continue
        vid = e.get("vendor_id") or ""
        expenses_by_cat[name].append({
            "date":   e.get("date","")[:10],
            "amount": float(e.get("amount") or 0),
            "vendor": vendors_map.get(vid,"") if vid else "",
            "notes":  e.get("public_notes") or "",
        })
    for cat in expenses_by_cat:
        expenses_by_cat[cat].sort(key=lambda x: x["date"])

    # Open invoices
    open_inv = []
    for inv in api_get_all(base, token, "/invoices"):
        if inv.get("is_deleted"):
            continue
        if inv.get("status_id") in (2, 3, 6) and float(inv.get("balance") or 0) > 0:
            open_inv.append({
                "number":   inv.get("number","—"),
                "client":   clients_map.get(inv.get("client_id",""),"—"),
                "date":     inv.get("date","")[:10],
                "due_date": inv.get("due_date","")[:10],
                "balance":  float(inv.get("balance") or 0),
            })
    open_inv.sort(key=lambda x: x["date"])
    return payments, dict(expenses_by_cat), open_inv

def get_summary(cfg, year):
    payments, exp_by_cat, _ = load_data(cfg, year)
    rev = sum(p["amount"] for p in payments)
    cat_totals = {cat: sum(e["amount"] for e in items)
                  for cat, items in exp_by_cat.items()}
    exp = sum(cat_totals.values())
    return rev, exp, rev - exp, cat_totals

# ── PDF generation ────────────────────────────────────────────────────────────
def chf(v):
    return f"CHF {v:,.2f}".replace(",", "'")

def fmt_date(d):
    if not d:
        return ""
    try:
        return datetime.strptime(d[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return d[:10]

def build_pdf(cfg, year, payments, expenses_by_cat, open_invoices):
    buf = io.BytesIO()
    W   = A4[0] - 40*mm
    firma = cfg.get("firma","")
    name  = cfg.get("name","")
    excl  = set(cfg.get("excluded_categories",[]))

    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
        title=f"Erfolgsrechnung {year} – {name}", author=name)

    # Styles
    def sty(name_, **kw):
        return ParagraphStyle(name_, **kw)

    s_title  = sty("ti", fontSize=22, leading=28, textColor=C_DARK,
                   fontName="Helvetica-Bold", spaceAfter=2*mm)
    s_sub    = sty("su", fontSize=11, textColor=colors.HexColor("#555"),
                   fontName="Helvetica", spaceAfter=6*mm)
    s_sec    = sty("se", fontSize=13, leading=16, textColor=C_WHITE,
                   fontName="Helvetica-Bold")
    s_sm     = sty("sm", fontSize=8, leading=10,
                   textColor=colors.HexColor("#444"), fontName="Helvetica")
    s_note   = sty("no", fontSize=8, leading=11,
                   textColor=colors.HexColor("#666"),
                   fontName="Helvetica-Oblique", spaceAfter=4*mm)
    s_bold   = sty("bo", fontSize=9, fontName="Helvetica-Bold", textColor=C_DARK)
    s_normal = sty("nr", fontSize=9, leading=12, textColor=C_DARK,
                   fontName="Helvetica")
    s_foot   = sty("ft", fontSize=7, textColor=colors.HexColor("#888"),
                   fontName="Helvetica", alignment=TA_CENTER)

    def section_hdr(title, bg=C_DARK):
        t = Table([[Paragraph(title, s_sec)]], colWidths=[W])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), bg),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 6),
        ]))
        return t

    def data_table(rows, col_widths, header=None, total_row=None):
        data = (([header] if header else []) + rows +
                ([total_row] if total_row else []))
        t = Table(data, colWidths=col_widths,
                  repeatRows=1 if header else 0)
        ds = 1 if header else 0
        style = [
            ("FONTNAME",     (0,0),(-1,-1), "Helvetica"),
            ("FONTSIZE",     (0,0),(-1,-1), 8.5),
            ("LEADING",      (0,0),(-1,-1), 11),
            ("TOPPADDING",   (0,0),(-1,-1), 2.5),
            ("BOTTOMPADDING",(0,0),(-1,-1), 2.5),
            ("LEFTPADDING",  (0,0),(-1,-1), 4),
            ("RIGHTPADDING", (0,0),(-1,-1), 4),
            ("ALIGN",        (-1,0),(-1,-1), "RIGHT"),
            ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
        ]
        if header:
            style += [("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                      ("BACKGROUND",(0,0),(-1,0),C_MID),
                      ("LINEBELOW",(0,0),(-1,0),0.5,C_ACCENT)]
        for i in range(len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND",(0,i+ds),(-1,i+ds),C_LIGHT))
        if total_row:
            last = len(data)-1
            style += [("LINEABOVE",(0,last),(-1,last),0.8,C_ACCENT),
                      ("FONTNAME",(0,last),(-1,last),"Helvetica-Bold"),
                      ("BACKGROUND",(0,last),(-1,last),C_MID)]
        t.setStyle(TableStyle(style))
        return t

    story = []

    # Title
    story.append(Paragraph(f"Erfolgsrechnung {year}", s_title))
    story.append(Paragraph(
        f"{name} · {firma} · Stand: {date.today().strftime('%d.%m.%Y')}", s_sub))
    story.append(HRFlowable(width=W, thickness=1.5, color=C_DARK, spaceAfter=6*mm))

    # 1 — Revenue
    rev_total = sum(p["amount"] for p in payments)
    story.append(section_hdr("1  Einnahmen (nach Zahlungseingang)"))
    story.append(Spacer(1, 2*mm))
    if payments:
        rows = [[fmt_date(p["date"]), Paragraph(p["client"], s_sm),
                 Paragraph(p["inv_num"], s_sm), chf(p["amount"])]
                for p in payments]
        story.append(data_table(rows, [22*mm, W*0.42, W*0.28, 28*mm],
            header=["Datum","Kunde","Rechnungsnr.","CHF"],
            total_row=["","",Paragraph("Total Einnahmen",s_bold),chf(rev_total)]))
    else:
        story.append(Paragraph("Keine Zahlungen in diesem Jahr.", s_note))
    story.append(Spacer(1, 6*mm))

    # 2 — Expenses
    exp_total, cat_totals = 0.0, {}
    story.append(section_hdr("2  Ausgaben (nach Kategorie)"))
    story.append(Spacer(1, 2*mm))
    if excl:
        story.append(Paragraph(f"Ausgeschlossen: {', '.join(sorted(excl))}", s_note))

    sorted_cats = sorted(expenses_by_cat.keys(),
        key=lambda c: ("z"+c if c in ("Unkategorisiert","Nicht zugewiesen") else c))

    for cat in sorted_cats:
        items   = expenses_by_cat[cat]
        cat_sum = sum(e["amount"] for e in items)
        cat_totals[cat] = cat_sum
        exp_total += cat_sum

        cat_hdr = Table(
            [[Paragraph(cat, sty("ch", fontSize=9.5, fontName="Helvetica-Bold",
                                 textColor=C_ACCENT)),
              Paragraph(chf(cat_sum), sty("cv", fontSize=9.5,
                           fontName="Helvetica-Bold",
                           textColor=C_ACCENT, alignment=TA_RIGHT))]],
            colWidths=[W*0.75, W*0.25])
        cat_hdr.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), C_CAT_BG),
            ("TOPPADDING",    (0,0),(-1,-1), 3),
            ("BOTTOMPADDING", (0,0),(-1,-1), 3),
            ("LEFTPADDING",   (0,0),(-1,-1), 6),
            ("RIGHTPADDING",  (0,0),(-1,-1), 6),
            ("LINEBELOW",     (0,0),(-1,-1), 0.3, C_ACCENT),
        ]))
        rows = []
        for e in items:
            desc = e["notes"] or e["vendor"] or "—"
            if e["vendor"] and e["notes"] and e["vendor"] not in e["notes"]:
                desc = f"{e['vendor']} — {e['notes']}"
            rows.append([fmt_date(e["date"]),
                         Paragraph(desc[:90], s_sm), chf(e["amount"])])
        story.append(KeepTogether([
            cat_hdr,
            data_table(rows, [22*mm, W-22*mm-26*mm, 26*mm]),
            Spacer(1, 3*mm)
        ]))

    exp_tot = Table([["", Paragraph("Total Ausgaben", s_bold), chf(exp_total)]],
                    colWidths=[22*mm, W-22*mm-28*mm, 28*mm])
    exp_tot.setStyle(TableStyle([
        ("ALIGN",      (2,0),(2,0),"RIGHT"),
        ("FONTNAME",   (0,0),(-1,-1),"Helvetica-Bold"),
        ("FONTSIZE",   (0,0),(-1,-1),10),
        ("TOPPADDING", (0,0),(-1,-1),4),
        ("BACKGROUND", (0,0),(-1,-1),C_MID),
        ("LINEABOVE",  (0,0),(-1,-1),1.0,C_DARK),
    ]))
    story.append(exp_tot)
    story.append(Spacer(1, 8*mm))

    # 3 — Result
    profit = rev_total - exp_total
    pc = C_GREEN if profit >= 0 else C_RED
    pl = "Reingewinn" if profit >= 0 else "Verlust"
    story.append(section_hdr("3  Ergebnis", bg=colors.HexColor("#16213e")))
    story.append(Spacer(1, 2*mm))
    res = Table([
        ["Einnahmen", chf(rev_total)],
        ["./. Ausgaben", chf(exp_total)],
        [Paragraph(f"<b>{pl} {year}</b>",
                   sty("pl", fontSize=11, fontName="Helvetica-Bold", textColor=pc)),
         Paragraph(f"<b>{chf(profit)}</b>",
                   sty("pv", fontSize=11, fontName="Helvetica-Bold",
                       textColor=pc, alignment=TA_RIGHT))],
    ], colWidths=[W*0.6, W*0.4])
    res.setStyle(TableStyle([
        ("ALIGN",         (1,0),(1,-1),"RIGHT"),
        ("FONTNAME",      (0,0),(-1,1),"Helvetica"),
        ("FONTSIZE",      (0,0),(-1,1),10),
        ("TOPPADDING",    (0,0),(-1,-1),5),
        ("BOTTOMPADDING", (0,0),(-1,-1),5),
        ("LEFTPADDING",   (0,0),(-1,-1),6),
        ("RIGHTPADDING",  (0,0),(-1,-1),6),
        ("LINEBELOW",     (0,1),(-1,1),0.8,C_DARK),
        ("BACKGROUND",    (0,2),(-1,2),C_LIGHT),
        ("LINEABOVE",     (0,2),(-1,2),1.5,pc),
        ("LINEBELOW",     (0,2),(-1,2),1.5,pc),
    ]))
    story.append(res)
    story.append(Spacer(1, 8*mm))

    # 4 — Open receivables
    story.append(section_hdr("4  Offene Forderungen",
                              bg=colors.HexColor("#2d4a6e")))
    story.append(Spacer(1, 2*mm))
    if open_invoices:
        open_total = sum(i["balance"] for i in open_invoices)
        rows = [[i["number"], Paragraph(i["client"], s_sm),
                 fmt_date(i["date"]), fmt_date(i["due_date"]),
                 chf(i["balance"])] for i in open_invoices]
        story.append(data_table(rows, [25*mm, W*0.38, 22*mm, 22*mm, 26*mm],
            header=["Rechnungsnr.","Kunde","Datum","Fällig","Offen CHF"],
            total_row=["","","",Paragraph("Total offen",s_bold),
                        chf(open_total)]))
    else:
        story.append(Paragraph("Keine offenen Forderungen.", s_note))
    story.append(Spacer(1, 8*mm))

    # 5 — Category summary
    story.append(section_hdr("5  Ausgaben nach Kategorie (Übersicht)",
                              bg=colors.HexColor("#374151")))
    story.append(Spacer(1, 2*mm))
    sum_rows = [[Paragraph(c, s_normal), chf(cat_totals[c])]
                for c in sorted_cats]
    sum_rows.append([Paragraph("Total", s_bold), chf(exp_total)])
    sum_tbl = Table(sum_rows, colWidths=[W*0.7, W*0.3])
    ss = [("ALIGN",(1,0),(1,-1),"RIGHT"),("FONTNAME",(0,0),(-1,-1),"Helvetica"),
          ("FONTSIZE",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),3),
          ("BOTTOMPADDING",(0,0),(-1,-1),3),("LEFTPADDING",(0,0),(-1,-1),6),
          ("RIGHTPADDING",(0,0),(-1,-1),6),
          ("LINEABOVE",(0,-1),(-1,-1),0.8,C_DARK),
          ("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
          ("BACKGROUND",(0,-1),(-1,-1),C_MID)]
    for i in range(len(sum_rows)-1):
        if i % 2 == 0:
            ss.append(("BACKGROUND",(0,i),(-1,i),C_LIGHT))
    sum_tbl.setStyle(TableStyle(ss))
    story.append(sum_tbl)

    # Footer
    story.append(Spacer(1, 10*mm))
    story.append(HRFlowable(width=W, thickness=0.5,
                             color=colors.HexColor("#aaa")))
    story.append(Spacer(1, 2*mm))
    base_host = cfg["in_url"].replace("/api/v1","")
    story.append(Paragraph(
        f"Erstellt am {date.today().strftime('%d.%m.%Y')} · {name} · {firma} · "
        f"Datenquelle: Invoice Ninja ({base_host})", s_foot))

    doc.build(story)
    return buf.getvalue()

# ── HTML templates ─────────────────────────────────────────────────────────────
COMMON_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0 }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #f3f4f8; color: #1a1a2e; min-height: 100vh; padding: 32px 16px }
.card { max-width: 720px; margin: 0 auto; background: #fff; border-radius: 12px;
        box-shadow: 0 2px 16px rgba(0,0,0,.10); overflow: hidden }
.header { background: #1a1a2e; color: #fff; padding: 24px 32px;
          display: flex; justify-content: space-between; align-items: center }
.header h1 { font-size: 1.4rem; font-weight: 700 }
.header p  { font-size: .8rem; opacity: .65; margin-top: 3px }
nav a { color: rgba(255,255,255,.75); text-decoration: none; font-size: .85rem;
        padding: 6px 12px; border-radius: 6px; transition: background .15s }
nav a:hover, nav a.active { background: rgba(255,255,255,.15); color: #fff }
nav { display: flex; gap: 4px }
.body { padding: 28px 32px }
label { display: block; font-size: .78rem; font-weight: 600; color: #555;
        text-transform: uppercase; letter-spacing: .5px; margin-bottom: 5px }
input, select { width: 100%; padding: 9px 13px; border: 1.5px solid #dde0ee;
                border-radius: 8px; font-size: .95rem; color: #1a1a2e;
                background: #fff; margin-bottom: 16px }
input:focus, select:focus { outline: none; border-color: #1a1a2e }
.btn { display: inline-flex; align-items: center; gap: 8px; padding: 10px 24px;
       border: none; border-radius: 8px; font-size: .95rem; font-weight: 600;
       cursor: pointer; transition: all .15s; text-decoration: none }
.btn-primary { background: #1a1a2e; color: #fff }
.btn-primary:hover { background: #16213e; box-shadow: 0 4px 12px rgba(26,26,46,.3) }
.btn-secondary { background: #f0f0f5; color: #1a1a2e }
.btn-secondary:hover { background: #e0e0eb }
.btn:disabled { opacity: .5; cursor: not-allowed }
.row { display: flex; justify-content: space-between; align-items: center;
       padding: 10px 16px; border-bottom: 1px solid #f0f0f5 }
.row:last-child { border-bottom: none }
.row.total { background: #f3f4f8; font-weight: 700 }
.row.profit { background: #1a1a2e; color: #fff; font-weight: 700; font-size: 1.05rem }
.row.loss   { background: #c1121f; color: #fff; font-weight: 700; font-size: 1.05rem }
.summary { border: 1.5px solid #dde0ee; border-radius: 10px; overflow: hidden; margin-top: 20px }
.cat-section { font-size: .73rem; font-weight: 700; text-transform: uppercase;
               letter-spacing: .5px; color: #888; padding: 7px 16px 4px;
               border-bottom: 1px solid #dde0ee; background: #fafafa }
table { width: 100%; border-collapse: collapse; font-size: .82rem }
td { padding: 5px 16px; border-bottom: 1px solid #f0f0f5 }
td:last-child { text-align: right }
tr:nth-child(even) { background: #f8f8fc }
tr:last-child td { border: none }
.spinner { display:none; width:16px; height:16px; border:2px solid rgba(255,255,255,.3);
           border-top-color:#fff; border-radius:50%; animation:spin .7s linear infinite }
@keyframes spin { to { transform: rotate(360deg) } }
.alert { padding: 10px 14px; border-radius: 8px; font-size: .875rem;
         margin-top: 12px; display: none }
.alert-ok  { background: #d1fae5; color: #065f46 }
.alert-err { background: #fee2e2; color: #991b1b }
.divider { height: 1px; background: #f0f0f5; margin: 20px 0 }
.hint { font-size: .73rem; color: #888; margin-top: -12px; margin-bottom: 16px }
.field-group { display: grid; grid-template-columns: 1fr 1fr; gap: 0 20px }
.check-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 20px;
              margin-bottom: 16px }
.check-item { display: flex; align-items: center; gap: 8px; padding: 7px 10px;
              border-radius: 6px; cursor: pointer; font-size: .9rem }
.check-item:hover { background: #f0f0f5 }
.check-item input[type=checkbox] { width: auto; margin: 0; cursor: pointer }
.check-item.excluded { text-decoration: line-through; color: #999 }
h3 { font-size: 1rem; margin-bottom: 14px; color: #1a1a2e }
.badge { display:inline-block; padding: 2px 8px; border-radius:99px; font-size:.75rem;
         font-weight:600; background:#fee2e2; color:#991b1b }
.badge.ok { background:#d1fae5; color:#065f46 }
"""

def render_page(title, active, body_html, firma=""):
    subtitle = f"{firma} · Invoice Ninja" if firma else "Invoice Ninja"
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{COMMON_CSS}</style>
</head>
<body>
<div class="card">
  <div class="header">
    <div>
      <h1>📊 Erfolgsrechnung Export</h1>
      <p>{subtitle}</p>
    </div>
    <nav>
      <a href="/" class="{'active' if active=='export' else ''}">Export</a>
      <a href="/settings" class="{'active' if active=='settings' else ''}">Einstellungen</a>
    </nav>
  </div>
  <div class="body">
    {body_html}
  </div>
</div>
</body>
</html>"""

EXPORT_BODY = r"""
<label for="yr">Steuerjahr</label>
<select id="yr" onchange="loadPreview()">YEAR_OPTIONS</select>
<div class="hint">Daten direkt aus Invoice Ninja</div>
<div id="preview"></div>
<button class="btn btn-primary" id="btn" onclick="startDl()" style="width:100%;justify-content:center;margin-top:20px">
  <span class="spinner" id="sp"></span>
  <span id="bl">PDF herunterladen</span>
</button>
<script>
function fmt(v){return"CHF "+v.toLocaleString('de-CH',{minimumFractionDigits:2,maximumFractionDigits:2})}
async function loadPreview(){
  const yr=document.getElementById('yr').value;
  const pv=document.getElementById('preview');
  pv.innerHTML='<div style="text-align:center;padding:20px;color:#888;font-size:.875rem">Lade…</div>';
  try{
    const r=await fetch('/summary?year='+yr);
    const d=await r.json();
    if(d.error){pv.innerHTML=`<div class="alert alert-err" style="display:block">✗ ${d.error}</div>`;return}
    const profit=d.revenue-d.expenses;
    const pc=profit>=0?'profit':'loss', pl=profit>=0?'Reingewinn':'Verlust';
    const sorted=Object.entries(d.categories).sort((a,b)=>b[1]-a[1]);
    let rows='';
    sorted.forEach(([c,v])=>rows+=`<tr><td>${c}</td><td>${fmt(v)}</td></tr>`);
    pv.innerHTML=`
      <div class="divider"></div>
      <div class="summary">
        <div class="row total"><span>Einnahmen</span><span>${fmt(d.revenue)}</span></div>
        <div class="row total"><span>./. Ausgaben</span><span>${fmt(d.expenses)}</span></div>
        <div class="row ${pc}"><span>${pl} ${d.year}</span><span>${fmt(profit)}</span></div>
        <div class="cat-section">Ausgaben nach Kategorie</div>
        <table>${rows}</table>
      </div>`;
  }catch(e){pv.innerHTML='<div class="alert alert-err" style="display:block">✗ Server nicht erreichbar</div>';}
}
function startDl(){
  const yr=document.getElementById('yr').value;
  const btn=document.getElementById('btn');
  const sp=document.getElementById('sp');
  const bl=document.getElementById('bl');
  btn.disabled=true; sp.style.display='block'; bl.textContent='PDF wird erstellt…';
  const a=document.createElement('a');
  a.href='/export?year='+yr;
  a.download='Erfolgsrechnung_'+yr+'.pdf';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  setTimeout(()=>{btn.disabled=false;sp.style.display='none';bl.textContent='PDF herunterladen';},4000);
}
window.addEventListener('DOMContentLoaded',loadPreview);
</script>
"""

SETTINGS_BODY = r"""
<form id="sf" onsubmit="return false">
  <h3>Invoice Ninja Verbindung</h3>
  <label>API Base URL</label>
  <input id="in_url" type="url" placeholder="https://invoices.example.com/api/v1" value="IN_URL_VAL">
  <div class="hint">Vollständige API-URL inkl. /api/v1</div>
  <label>API Token</label>
  <input id="in_token" type="password" placeholder="••••••••" value="IN_TOKEN_VAL"
         onfocus="this.type='text'" onblur="this.type='password'">
  <div class="hint">Unter Einstellungen → API-Token in Invoice Ninja</div>
  <div style="margin-bottom:16px">
    <button class="btn btn-secondary" onclick="testConn()">Verbindung testen</button>
    <span id="conn_status" style="margin-left:12px;font-size:.85rem"></span>
  </div>
  <div class="divider"></div>
  <h3>Allgemein</h3>
  <div class="field-group">
    <div>
      <label>Firmenname</label>
      <input id="firma" type="text" value="FIRMA_VAL">
    </div>
    <div>
      <label>Inhaberin / Inhaber</label>
      <input id="name" type="text" value="NAME_VAL">
    </div>
  </div>
  <div class="divider"></div>
  <h3>Kategorien <span style="font-weight:400;font-size:.85rem;color:#888">— ausgeschlossene werden nicht exportiert</span></h3>
  <div id="cats_loading" style="color:#888;font-size:.875rem;margin-bottom:16px">Lade Kategorien…</div>
  <div class="check-grid" id="cats"></div>
  <div style="display:flex;gap:12px;margin-top:8px">
    <button class="btn btn-primary" onclick="saveSettings()">
      <span class="spinner" id="save_sp"></span>
      <span id="save_bl">Einstellungen speichern</span>
    </button>
  </div>
  <div class="alert alert-ok"  id="ok_msg"></div>
  <div class="alert alert-err" id="err_msg"></div>
</form>
<script>
const excluded = new Set(EXCLUDED_JSON);

async function loadCats(){
  try{
    const r=await fetch('/api/categories');
    const d=await r.json();
    document.getElementById('cats_loading').style.display='none';
    const grid=document.getElementById('cats');
    d.forEach(c=>{
      const isExcl=excluded.has(c.name);
      const el=document.createElement('label');
      el.className='check-item'+(isExcl?' excluded':'');
      el.innerHTML=`<input type="checkbox" value="${c.name}" ${isExcl?'checked':''} onchange="toggleExcl(this)"> ${c.name}`;
      grid.appendChild(el);
    });
    if(!d.length) document.getElementById('cats_loading').textContent='Keine Kategorien gefunden – Verbindung prüfen.';
  }catch(e){
    document.getElementById('cats_loading').textContent='Fehler beim Laden.';
  }
}

function toggleExcl(cb){
  cb.closest('label').className='check-item'+(cb.checked?' excluded':'');
}

async function testConn(){
  document.getElementById('conn_status').textContent='…';
  const r=await fetch('/api/test',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({in_url:document.getElementById('in_url').value,
                         in_token:document.getElementById('in_token').value})});
  const d=await r.json();
  const el=document.getElementById('conn_status');
  el.innerHTML=d.ok
    ?'<span class="badge ok">✓ Verbunden</span>'
    :`<span class="badge">✗ ${d.message}</span>`;
}

async function saveSettings(){
  const btn=document.getElementById('save_bl');
  const sp=document.getElementById('save_sp');
  sp.style.display='block'; btn.textContent='Speichern…';
  const excl=[...document.querySelectorAll('#cats input:checked')].map(e=>e.value);
  const cfg={
    in_url:    document.getElementById('in_url').value,
    in_token:  document.getElementById('in_token').value,
    firma:     document.getElementById('firma').value,
    name:      document.getElementById('name').value,
    excluded_categories: excl,
  };
  const r=await fetch('/api/settings',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
  const d=await r.json();
  sp.style.display='none'; btn.textContent='Einstellungen speichern';
  const ok=document.getElementById('ok_msg');
  const err=document.getElementById('err_msg');
  if(d.ok){ok.textContent='✓ Gespeichert'; ok.style.display='block';
            err.style.display='none'; setTimeout(()=>ok.style.display='none',3000);}
  else{err.textContent='✗ '+d.error; err.style.display='block'; ok.style.display='none';}
}

window.addEventListener('DOMContentLoaded',loadCats);
</script>
"""

# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info(f"{self.address_string()} {fmt % args}")

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html, code=200):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path   = parsed.path
        cfg    = load_config()

        if path in ("/", "/index.html"):
            cur  = date.today().year
            opts = "\n".join(
                f'<option value="{y}"{"selected" if y==cur else ""}>{y}</option>'
                for y in range(cur, 2017, -1))
            body = EXPORT_BODY.replace("YEAR_OPTIONS", opts)
            html = render_page("Erfolgsrechnung Export", "export", body,
                               cfg.get("firma",""))
            self.send_html(html)

        elif path == "/settings":
            excl_json = json.dumps(cfg.get("excluded_categories",[]))
            body = (SETTINGS_BODY
                    .replace("IN_URL_VAL",    cfg.get("in_url",""))
                    .replace("IN_TOKEN_VAL",  cfg.get("in_token",""))
                    .replace("FIRMA_VAL",     cfg.get("firma",""))
                    .replace("NAME_VAL",      cfg.get("name",""))
                    .replace("EXCLUDED_JSON", excl_json))
            html = render_page("Einstellungen", "settings", body,
                               cfg.get("firma",""))
            self.send_html(html)

        elif path == "/summary":
            year = int(params.get("year", [date.today().year])[0])
            try:
                rev, exp, profit, cats = get_summary(cfg, year)
                self.send_json({"year": year, "revenue": rev,
                                "expenses": exp, "profit": profit,
                                "categories": cats})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/export":
            year = int(params.get("year", [date.today().year])[0])
            try:
                payments, exp_by_cat, open_inv = load_data(cfg, year)
                pdf_bytes = build_pdf(cfg, year, payments, exp_by_cat, open_inv)
                fname = f"Erfolgsrechnung_{year}.pdf"
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{fname}"')
                self.send_header("Content-Length", len(pdf_bytes))
                self.end_headers()
                self.wfile.write(pdf_bytes)
                log.info(f"PDF {fname} geliefert ({len(pdf_bytes)//1024} KB)")
            except Exception as e:
                log.error(f"PDF error: {e}")
                self.send_json({"error": str(e)}, 500)

        elif path == "/api/categories":
            cats = get_categories(cfg["in_url"], cfg["in_token"])
            self.send_json(cats)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/test":
            body = self.read_body()
            ok, msg = test_connection(body.get("in_url",""),
                                      body.get("in_token",""))
            self.send_json({"ok": ok, "message": msg})

        elif path == "/api/settings":
            body = self.read_body()
            try:
                cfg = load_config()
                cfg.update({
                    "in_url":              body.get("in_url", cfg["in_url"]),
                    "in_token":            body.get("in_token", cfg["in_token"]),
                    "firma":               body.get("firma", cfg["firma"]),
                    "name":               body.get("name", cfg["name"]),
                    "excluded_categories": body.get("excluded_categories",
                                                    cfg["excluded_categories"]),
                })
                save_config(cfg)
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)

        else:
            self.send_response(404)
            self.end_headers()

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cfg  = load_config()
    port = cfg.get("port", 5757)
    server = HTTPServer(("0.0.0.0", port), Handler)
    log.info(f"Server gestartet auf http://0.0.0.0:{port}")
    log.info(f"Config: {CONFIG_PATH}")
    server.serve_forever()
