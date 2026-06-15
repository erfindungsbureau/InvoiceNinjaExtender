#!/usr/bin/env python3
"""
InvoiceNinjaExtender — Financial Report Export Server
Standalone HTTP server — generates PDF income statements from Invoice Ninja data.
UI language is detected automatically from the Invoice Ninja company settings.
"""

import io, os, json, logging, re, time, secrets
import requests as req
from datetime import date, datetime, timezone
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
    "firma":               "My Company",
    "name":                "First Last",
    "excluded_categories": [],
    "language":            "auto",   # "auto" | "en" | "de"
    "port":                5757,
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            data.setdefault(k, v)
        return data
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# ── Translations ───────────────────────────────────────────────────────────────
# Invoice Ninja language_id: 1=English, 3=German (Deutsch)
# Add more languages by extending TRANSLATIONS and IN_LANG_MAP.
IN_LANG_MAP = {
    "1": "en",   # English
    "3": "de",   # Deutsch
    "4": "de",   # Deutsch (some IN versions use 4)
}

TRANSLATIONS = {
    "en": {
        # UI – nav / header
        "nav_export":           "Export",
        "nav_settings":         "Settings",
        "app_title":            "Financial Report Export",
        # UI – nav
        "nav_charts":           "Charts",
        "nav_tasks":            "Open Tasks",
        "nav_timesheet":        "Timesheet",
        # UI – open tasks page
        "tasks_title":          "Open Tasks",
        "tasks_hint":           "All tasks not yet marked as Done, grouped by project.",
        "tasks_col_task":       "Task",
        "tasks_col_status":     "Status",
        "tasks_col_hours":      "Hours",
        "tasks_col_rate":       "Rate",
        "tasks_col_expected":   "Expected CHF",
        "tasks_no_rate":        "no rate",
        "tasks_total":          "Total",
        "tasks_no_open":        "No open tasks found.",
        "tasks_loading":        "Loading tasks…",
        "tasks_no_project":     "No project assigned",
        # UI – charts page
        "chart_title":          "Expected Revenue from Time Tracking",
        "chart_hint":           "Hours logged × hourly rate per day. Rate hierarchy: task → project → client.",
        "chart_label_daily":    "Expected revenue (CHF)",
        "chart_label_cumul":    "Cumulative (CHF)",
        "chart_total":          "Total expected",
        "chart_days":           "days with entries",
        "chart_no_data":        "No time entries with a rate found for this year.",
        "chart_loading":        "Loading time entries…",
        # UI – timesheet page
        "timesheet_title":      "Timesheet",
        "timesheet_hint":       "Select a project and optional date range to generate a timesheet for the client.",
        "label_project":        "Project",
        "label_start_date":     "From",
        "label_end_date":       "To",
        "timesheet_select_project": "Please select a project.",
        "timesheet_col_date":   "Date",
        "timesheet_col_task":   "Description",
        "timesheet_col_hours":  "Hours",
        "timesheet_col_rate":   "Rate",
        "timesheet_col_amount": "Amount CHF",
        "timesheet_total":      "Total",
        "timesheet_no_data":    "No time entries found for this period.",
        "timesheet_loading":    "Loading time entries…",
        "btn_download_timesheet": "Download Timesheet PDF",
        "pdf_timesheet_title":  "Timesheet",
        "pdf_timesheet_project": "Project",
        "pdf_timesheet_client": "Client",
        "pdf_timesheet_period": "Period",
        "pdf_timesheet_all":    "All entries",
        # UI – export page
        "label_tax_year":       "Tax Year",
        "hint_data_source":     "Data loaded directly from Invoice Ninja",
        "loading":              "Loading…",
        "revenue":              "Revenue",
        "expenses_deduct":      "–  Expenses",
        "net_profit":           "Net Profit",
        "net_loss":             "Net Loss",
        "expenses_by_cat":      "Expenses by Category",
        "btn_download_pdf":     "Download PDF",
        "btn_generating_pdf":   "Generating PDF…",
        "err_server":           "Server not reachable",
        # UI – settings page
        "h_connection":         "Invoice Ninja Connection",
        "label_api_url":        "API Base URL",
        "hint_api_url":         "Full API URL including /api/v1",
        "label_api_token":      "API Token",
        "hint_api_token":       "Found under Settings → API Tokens in Invoice Ninja",
        "btn_test_conn":        "Test Connection",
        "conn_ok":              "Connected",
        "h_general":            "General",
        "label_company":        "Company Name",
        "label_owner":          "Owner",
        "h_categories":         "Categories",
        "hint_categories":      "excluded categories will not be exported",
        "loading_cats":         "Loading categories…",
        "no_cats":              "No categories found – check connection.",
        "err_loading_cats":     "Error loading categories.",
        "btn_save":             "Save Settings",
        "btn_saving":           "Saving…",
        "saved_ok":             "✓ Saved",
        # PDF – section headers
        "pdf_title":            "Income Statement",
        "pdf_as_of":            "As of",
        "pdf_sec1":             "1  Revenue (by Payment Date)",
        "pdf_col_date":         "Date",
        "pdf_col_client":       "Client",
        "pdf_col_invoice":      "Invoice No.",
        "pdf_total_revenue":    "Total Revenue",
        "pdf_no_payments":      "No payments recorded for this year.",
        "pdf_sec2":             "2  Expenses (by Category)",
        "pdf_excluded":         "Excluded:",
        "pdf_total_expenses":   "Total Expenses",
        "pdf_uncategorized":    "Uncategorized",
        "pdf_unassigned":       "Unassigned",
        "pdf_sec3":             "3  Result",
        "pdf_revenue":          "Revenue",
        "pdf_expenses_deduct":  "–  Expenses",
        "pdf_sec4":             "4  Open Receivables",
        "pdf_col_invoice_nr":   "Invoice No.",
        "pdf_col_due":          "Due",
        "pdf_col_outstanding":  "Outstanding CHF",
        "pdf_total_open":       "Total outstanding",
        "pdf_no_open":          "No open receivables.",
        "pdf_sec5":             "5  Expenses by Category (Overview)",
        "pdf_total":            "Total",
        "pdf_created_on":       "Created on",
        "pdf_data_source":      "Data source: Invoice Ninja",
    },
    "de": {
        # UI – nav / header
        "nav_export":           "Export",
        "nav_settings":         "Einstellungen",
        "nav_charts":           "Grafiken",
        "nav_tasks":            "Offene Aufgaben",
        "nav_timesheet":        "Stundenliste",
        "app_title":            "Erfolgsrechnung Export",
        # UI – open tasks page
        "tasks_title":          "Offene Aufgaben",
        "tasks_hint":           "Alle Aufgaben ohne Status «Done», gruppiert nach Projekt.",
        "tasks_col_task":       "Aufgabe",
        "tasks_col_status":     "Status",
        "tasks_col_hours":      "Stunden",
        "tasks_col_rate":       "Satz",
        "tasks_col_expected":   "Erwartet CHF",
        "tasks_no_rate":        "kein Satz",
        "tasks_total":          "Total",
        "tasks_no_open":        "Keine offenen Aufgaben gefunden.",
        "tasks_loading":        "Lade Aufgaben…",
        "tasks_no_project":     "Kein Projekt zugewiesen",
        # UI – charts page
        "chart_title":          "Erwartete Einnahmen aus Zeiterfassung",
        "chart_hint":           "Erfasste Stunden × Stundensatz pro Tag. Satzpriorität: Aufgabe → Projekt → Kunde.",
        "chart_label_daily":    "Erwartete Einnahmen (CHF)",
        "chart_label_cumul":    "Kumuliert (CHF)",
        "chart_total":          "Total erwartet",
        "chart_days":           "Tage mit Einträgen",
        "chart_no_data":        "Keine Zeiteinträge mit Stundensatz für dieses Jahr gefunden.",
        "chart_loading":        "Lade Zeiteinträge…",
        # UI – timesheet page
        "timesheet_title":      "Stundenliste",
        "timesheet_hint":       "Projekt und optional Zeitraum wählen, um eine Stundenliste für den Kunden zu erstellen.",
        "label_project":        "Projekt",
        "label_start_date":     "Von",
        "label_end_date":       "Bis",
        "timesheet_select_project": "Bitte ein Projekt auswählen.",
        "timesheet_col_date":   "Datum",
        "timesheet_col_task":   "Beschreibung",
        "timesheet_col_hours":  "Stunden",
        "timesheet_col_rate":   "Satz",
        "timesheet_col_amount": "Betrag CHF",
        "timesheet_total":      "Total",
        "timesheet_no_data":    "Keine Zeiteinträge für diesen Zeitraum gefunden.",
        "timesheet_loading":    "Lade Zeiteinträge…",
        "btn_download_timesheet": "Stundenliste als PDF",
        "pdf_timesheet_title":  "Stundenliste",
        "pdf_timesheet_project": "Projekt",
        "pdf_timesheet_client": "Kunde",
        "pdf_timesheet_period": "Zeitraum",
        "pdf_timesheet_all":    "Alle Einträge",
        # UI – export page
        "label_tax_year":       "Steuerjahr",
        "hint_data_source":     "Daten direkt aus Invoice Ninja",
        "loading":              "Lade…",
        "revenue":              "Einnahmen",
        "expenses_deduct":      "./. Ausgaben",
        "net_profit":           "Reingewinn",
        "net_loss":             "Verlust",
        "expenses_by_cat":      "Ausgaben nach Kategorie",
        "btn_download_pdf":     "PDF herunterladen",
        "btn_generating_pdf":   "PDF wird erstellt…",
        "err_server":           "Server nicht erreichbar",
        # UI – settings page
        "h_connection":         "Invoice Ninja Verbindung",
        "label_api_url":        "API Base URL",
        "hint_api_url":         "Vollständige API-URL inkl. /api/v1",
        "label_api_token":      "API Token",
        "hint_api_token":       "Unter Einstellungen → API-Token in Invoice Ninja",
        "btn_test_conn":        "Verbindung testen",
        "conn_ok":              "Verbunden",
        "h_general":            "Allgemein",
        "label_company":        "Firmenname",
        "label_owner":          "Inhaberin / Inhaber",
        "h_categories":         "Kategorien",
        "hint_categories":      "ausgeschlossene werden nicht exportiert",
        "loading_cats":         "Lade Kategorien…",
        "no_cats":              "Keine Kategorien gefunden – Verbindung prüfen.",
        "err_loading_cats":     "Fehler beim Laden.",
        "btn_save":             "Einstellungen speichern",
        "btn_saving":           "Speichern…",
        "saved_ok":             "✓ Gespeichert",
        # PDF – section headers
        "pdf_title":            "Erfolgsrechnung",
        "pdf_as_of":            "Stand",
        "pdf_sec1":             "1  Einnahmen (nach Zahlungseingang)",
        "pdf_col_date":         "Datum",
        "pdf_col_client":       "Kunde",
        "pdf_col_invoice":      "Rechnungsnr.",
        "pdf_total_revenue":    "Total Einnahmen",
        "pdf_no_payments":      "Keine Zahlungen in diesem Jahr.",
        "pdf_sec2":             "2  Ausgaben (nach Kategorie)",
        "pdf_excluded":         "Ausgeschlossen:",
        "pdf_total_expenses":   "Total Ausgaben",
        "pdf_uncategorized":    "Unkategorisiert",
        "pdf_unassigned":       "Nicht zugewiesen",
        "pdf_sec3":             "3  Ergebnis",
        "pdf_revenue":          "Einnahmen",
        "pdf_expenses_deduct":  "./. Ausgaben",
        "pdf_sec4":             "4  Offene Forderungen",
        "pdf_col_invoice_nr":   "Rechnungsnr.",
        "pdf_col_due":          "Fällig",
        "pdf_col_outstanding":  "Offen CHF",
        "pdf_total_open":       "Total offen",
        "pdf_no_open":          "Keine offenen Forderungen.",
        "pdf_sec5":             "5  Ausgaben nach Kategorie (Übersicht)",
        "pdf_total":            "Total",
        "pdf_created_on":       "Erstellt am",
        "pdf_data_source":      "Datenquelle: Invoice Ninja",
    },
}

def t(key, lang="en"):
    """Return translated string for key in given language, falling back to English."""
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(
        key, TRANSLATIONS["en"].get(key, key))

# ── Session management ────────────────────────────────────────────────────────
SESSION_LIFETIME = 8 * 3600  # 8 hours
_sessions: dict = {}  # session_id -> {"token": str, "expires": float}

def create_session(token: str) -> str:
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = {"token": token, "expires": time.time() + SESSION_LIFETIME}
    # Purge expired sessions
    expired = [k for k, v in _sessions.items() if v["expires"] < time.time()]
    for k in expired:
        del _sessions[k]
    return sid

def get_session(sid: str | None):
    if not sid:
        return None
    s = _sessions.get(sid)
    if not s:
        return None
    if time.time() > s["expires"]:
        del _sessions[sid]
        return None
    return s

def delete_session(sid: str):
    _sessions.pop(sid, None)

def parse_cookies(raw: str) -> dict:
    cookies = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies

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
            return True, "OK"
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

def get_in_language(cfg):
    """
    Return UI language. Priority:
      1. config "language" if set to "en" or "de"
      2. auto-detect from IN company API
      3. fallback "en"
    """
    override = cfg.get("language", "auto")
    if override in TRANSLATIONS:
        return override
    # Auto-detect from IN
    try:
        r = req.get(f"{cfg['in_url']}/company",
                    headers=make_headers(cfg["in_token"]),
                    timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", {})
            lang_id = (data.get("settings", {}).get("language_id")
                       or data.get("language_id")
                       or "1")
            return IN_LANG_MAP.get(str(lang_id), "en")
    except Exception:
        pass
    return "en"

# ── Data loading ──────────────────────────────────────────────────────────────
def load_data(cfg, year, lang="en"):
    base     = cfg["in_url"]
    token    = cfg["in_token"]
    excl     = set(cfg.get("excluded_categories", []))
    year_str = str(year)

    cats_map = {c["id"]: c.get("name","—")
                for c in api_get_all(base, token, "/expense_categories")
                if not c.get("is_deleted", False)}
    vendors_map = {v["id"]: v.get("name","—")
                   for v in api_get_all(base, token, "/vendors")}
    clients_map = {c["id"]: c.get("name","—")
                   for c in api_get_all(base, token, "/clients")}

    uncategorized_label = t("pdf_uncategorized", lang)
    unassigned_label    = t("pdf_unassigned", lang)

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
            client_name = clients_map.get(p.get("client_id",""), unassigned_label)
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
        name = cats_map.get(cid, uncategorized_label) if cid else uncategorized_label
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

def get_summary(cfg, year, lang="en"):
    payments, exp_by_cat, _ = load_data(cfg, year, lang)
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

def build_pdf(cfg, year, payments, expenses_by_cat, open_invoices, lang="en"):
    buf = io.BytesIO()
    W   = A4[0] - 40*mm
    firma = cfg.get("firma","")
    name  = cfg.get("name","")
    excl  = set(cfg.get("excluded_categories",[]))

    pdf_title = t("pdf_title", lang)
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
        title=f"{pdf_title} {year} – {name}", author=name)

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
        tbl = Table([[Paragraph(title, s_sec)]], colWidths=[W])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), bg),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 6),
        ]))
        return tbl

    def data_table(rows, col_widths, header=None, total_row=None):
        data = (([header] if header else []) + rows +
                ([total_row] if total_row else []))
        tbl = Table(data, colWidths=col_widths,
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
        tbl.setStyle(TableStyle(style))
        return tbl

    story = []

    # Title
    story.append(Paragraph(f"{pdf_title} {year}", s_title))
    story.append(Paragraph(
        f"{name} · {firma} · {t('pdf_as_of', lang)}: {date.today().strftime('%d.%m.%Y')}", s_sub))
    story.append(HRFlowable(width=W, thickness=1.5, color=C_DARK, spaceAfter=6*mm))

    # ── helper: keep section header glued to first content element ────────────
    def section_block(hdr_flowable, content_flowables, spacer_after=6*mm):
        """Yield flowables; header is kept with the first content item."""
        if not content_flowables:
            story.append(hdr_flowable)
            return
        # Header + spacer + first content = unbreakable unit
        story.append(KeepTogether(
            [hdr_flowable, Spacer(1, 2*mm)] + content_flowables[:1]
        ))
        # Remaining content flows normally
        for fl in content_flowables[1:]:
            story.append(fl)
        story.append(Spacer(1, spacer_after))

    # 1 — Revenue ──────────────────────────────────────────────────────────────
    rev_total = sum(p["amount"] for p in payments)
    if payments:
        rows = [[fmt_date(p["date"]), Paragraph(p["client"], s_sm),
                 Paragraph(p["inv_num"], s_sm), chf(p["amount"])]
                for p in payments]
        sec1_content = [data_table(rows, [22*mm, W*0.42, W*0.28, 28*mm],
            header=[t("pdf_col_date",lang), t("pdf_col_client",lang),
                    t("pdf_col_invoice",lang), "CHF"],
            total_row=["","",Paragraph(t("pdf_total_revenue",lang),s_bold),
                       chf(rev_total)])]
    else:
        sec1_content = [Paragraph(t("pdf_no_payments", lang), s_note)]
    section_block(section_hdr(t("pdf_sec1", lang)), sec1_content)

    # 2 — Expenses ─────────────────────────────────────────────────────────────
    exp_total, cat_totals = 0.0, {}

    uncategorized_label = t("pdf_uncategorized", lang)
    unassigned_label    = t("pdf_unassigned", lang)
    sorted_cats = sorted(expenses_by_cat.keys(),
        key=lambda c: ("z"+c if c in (uncategorized_label, unassigned_label) else c))

    # Build category blocks first so we can attach the first one to the header
    cat_blocks = []
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
        cat_blocks.append(KeepTogether([
            cat_hdr,
            data_table(rows, [22*mm, W-22*mm-26*mm, 26*mm]),
            Spacer(1, 3*mm)
        ]))

    # Intro elements (excluded note) go before the first category
    sec2_intro = []
    if excl:
        sec2_intro.append(Paragraph(
            f"{t('pdf_excluded', lang)} {', '.join(sorted(excl))}", s_note))
    sec2_intro += cat_blocks  # header glued to first block via section_block

    exp_tot = Table(
        [["", Paragraph(t("pdf_total_expenses", lang), s_bold), chf(exp_total)]],
        colWidths=[22*mm, W-22*mm-28*mm, 28*mm])
    exp_tot.setStyle(TableStyle([
        ("ALIGN",      (2,0),(2,0),"RIGHT"),
        ("FONTNAME",   (0,0),(-1,-1),"Helvetica-Bold"),
        ("FONTSIZE",   (0,0),(-1,-1),10),
        ("TOPPADDING", (0,0),(-1,-1),4),
        ("BACKGROUND", (0,0),(-1,-1),C_MID),
        ("LINEABOVE",  (0,0),(-1,-1),1.0,C_DARK),
    ]))
    sec2_intro.append(exp_tot)
    section_block(section_hdr(t("pdf_sec2", lang)), sec2_intro, spacer_after=8*mm)

    # 3 — Result ───────────────────────────────────────────────────────────────
    profit = rev_total - exp_total
    pc = C_GREEN if profit >= 0 else C_RED
    pl = t("net_profit" if profit >= 0 else "net_loss", lang)
    res = Table([
        [t("pdf_revenue", lang),         chf(rev_total)],
        [t("pdf_expenses_deduct", lang),  chf(exp_total)],
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
    # Section 3 is small — keep entire block together
    section_block(
        section_hdr(t("pdf_sec3", lang), bg=colors.HexColor("#16213e")),
        [res], spacer_after=8*mm)

    # 4 — Open receivables ─────────────────────────────────────────────────────
    if open_invoices:
        open_total = sum(i["balance"] for i in open_invoices)
        rows = [[i["number"], Paragraph(i["client"], s_sm),
                 fmt_date(i["date"]), fmt_date(i["due_date"]),
                 chf(i["balance"])] for i in open_invoices]
        sec4_content = [data_table(rows, [25*mm, W*0.38, 22*mm, 22*mm, 26*mm],
            header=[t("pdf_col_invoice_nr",lang), t("pdf_col_client",lang),
                    t("pdf_col_date",lang), t("pdf_col_due",lang),
                    t("pdf_col_outstanding",lang)],
            total_row=["","","",Paragraph(t("pdf_total_open",lang),s_bold),
                        chf(open_total)])]
    else:
        sec4_content = [Paragraph(t("pdf_no_open", lang), s_note)]
    section_block(
        section_hdr(t("pdf_sec4", lang), bg=colors.HexColor("#2d4a6e")),
        sec4_content, spacer_after=8*mm)

    # 5 — Category summary ─────────────────────────────────────────────────────
    sum_rows = [[Paragraph(c, s_normal), chf(cat_totals[c])]
                for c in sorted_cats]
    sum_rows.append([Paragraph(t("pdf_total", lang), s_bold), chf(exp_total)])
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
    section_block(
        section_hdr(t("pdf_sec5", lang), bg=colors.HexColor("#374151")),
        [sum_tbl], spacer_after=10*mm)

    # Footer
    story.append(HRFlowable(width=W, thickness=0.5,
                             color=colors.HexColor("#aaa")))
    story.append(Spacer(1, 2*mm))
    base_host = cfg["in_url"].replace("/api/v1","")
    story.append(Paragraph(
        f"{t('pdf_created_on', lang)} {date.today().strftime('%d.%m.%Y')} · "
        f"{name} · {firma} · {t('pdf_data_source', lang)} ({base_host})",
        s_foot))

    doc.build(story)
    return buf.getvalue()

# ── Open tasks ────────────────────────────────────────────────────────────────
DONE_STATUS_ID = "l4zbq2dprO"   # IN task status "Done"

def get_open_tasks(cfg):
    """
    Returns list of open tasks (status != Done), each with:
      name, status_name, project_name, hours_logged, rate, expected_chf
    Grouped-ready (sorted by project_name, then task name).
    """
    base  = cfg["in_url"]
    token = cfg["in_token"]

    # Lookups
    statuses = {s["id"]: s.get("name","?")
                for s in api_get_all(base, token, "/task_statuses")}
    projects = {p["id"]: p for p in api_get_all(base, token, "/projects")
                if not p.get("is_deleted")}
    clients  = {c["id"]: c for c in api_get_all(base, token, "/clients")
                if not c.get("is_deleted")}

    result = []
    for task in api_get_all(base, token, "/tasks"):
        if task.get("is_deleted"):
            continue
        if task.get("status_id") == DONE_STATUS_ID:
            continue

        # Hours from time_log
        raw_log = task.get("time_log") or "[]"
        if isinstance(raw_log, str):
            try:
                raw_log = json.loads(raw_log)
            except Exception:
                raw_log = []
        hours = 0.0
        for entry in raw_log:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                s, e = entry[0], entry[1]
                if s and e:
                    hours += (int(e) - int(s)) / 3600.0

        # Rate hierarchy
        rate = float(task.get("rate") or 0)
        if not rate:
            proj = projects.get(task.get("project_id") or "")
            if proj:
                rate = float(proj.get("task_rate") or 0)
        if not rate:
            cl = clients.get(task.get("client_id") or "")
            if cl:
                rate = float(
                    cl.get("settings", {}).get("default_task_rate") or
                    cl.get("rate") or 0)

        proj_name = ""
        proj_id   = task.get("project_id") or ""
        if proj_id in projects:
            proj_name = projects[proj_id].get("name","")

        result.append({
            "name":         task.get("description") or task.get("number","?"),
            "status_name":  statuses.get(task.get("status_id",""), "?"),
            "project_name": proj_name,
            "hours":        round(hours, 2),
            "rate":         rate,
            "expected_chf": round(hours * rate, 2),
        })

    result.sort(key=lambda x: (x["project_name"] or "\xff", x["name"]))
    return result

# ── Time-chart data ───────────────────────────────────────────────────────────
def get_timechart_data(cfg, year):
    """
    Returns dict: { "YYYY-MM-DD": CHF_amount, ... }
    Revenue = logged hours × rate.
    Rate lookup hierarchy: task.rate → project.task_rate → client default_task_rate.
    Entries with rate=0 are skipped (non-billable / no rate configured).
    """
    base     = cfg["in_url"]
    token    = cfg["in_token"]
    year_str = str(year)

    projects = {p["id"]: p for p in api_get_all(base, token, "/projects")
                if not p.get("is_deleted")}
    clients  = {c["id"]: c for c in api_get_all(base, token, "/clients")
                if not c.get("is_deleted")}

    daily = defaultdict(float)

    for task in api_get_all(base, token, "/tasks"):
        if task.get("is_deleted"):
            continue

        # Rate hierarchy
        rate = float(task.get("rate") or 0)
        if not rate:
            proj = projects.get(task.get("project_id") or "")
            if proj:
                rate = float(proj.get("task_rate") or 0)
        if not rate:
            cl = clients.get(task.get("client_id") or "")
            if cl:
                rate = float(
                    cl.get("settings", {}).get("default_task_rate") or
                    cl.get("rate") or 0)
        if not rate:
            continue  # no rate → skip

        # Parse time_log — stored as JSON string or list
        raw_log = task.get("time_log") or "[]"
        if isinstance(raw_log, str):
            try:
                raw_log = json.loads(raw_log)
            except Exception:
                continue

        for entry in raw_log:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            start_ts, end_ts = entry[0], entry[1]
            if not start_ts or not end_ts:
                continue  # running entry
            try:
                start_dt = datetime.fromtimestamp(int(start_ts))
                end_ts   = int(end_ts)
            except Exception:
                continue
            if start_dt.year != year:
                continue
            hours = (end_ts - int(start_ts)) / 3600.0
            if hours <= 0:
                continue
            day_key = start_dt.strftime("%Y-%m-%d")
            daily[day_key] += round(hours * rate, 2)

    return dict(daily)

# ── Timesheet ("Stundenliste") ───────────────────────────────────────────────
def get_projects_list(cfg):
    """Returns a list of active projects: {id, name, client_name}, sorted by name."""
    base, token = cfg["in_url"], cfg["in_token"]
    clients = {c["id"]: c for c in api_get_all(base, token, "/clients")
               if not c.get("is_deleted")}
    result = []
    for p in api_get_all(base, token, "/projects"):
        if p.get("is_deleted"):
            continue
        client = clients.get(p.get("client_id") or "")
        result.append({
            "id":          p["id"],
            "name":        p.get("name","?"),
            "client_name": client.get("name","") if client else "",
        })
    result.sort(key=lambda x: x["name"].lower())
    return result

def get_timesheet_data(cfg, project_id, start_date=None, end_date=None):
    """
    Returns dict with project/client name and a list of timesheet rows
    (date, description, hours, rate, amount), one row per day+task,
    sorted by date. start_date/end_date are "YYYY-MM-DD" strings (inclusive).
    """
    base, token = cfg["in_url"], cfg["in_token"]

    projects = {p["id"]: p for p in api_get_all(base, token, "/projects")
                if not p.get("is_deleted")}
    clients  = {c["id"]: c for c in api_get_all(base, token, "/clients")
                if not c.get("is_deleted")}

    proj = projects.get(project_id, {})
    client = clients.get(proj.get("client_id") or "")

    proj_rate = float(proj.get("task_rate") or 0)
    if not proj_rate and client:
        proj_rate = float(client.get("settings", {}).get("default_task_rate")
                           or client.get("rate") or 0)

    rows_by_key = {}
    for task in api_get_all(base, token, "/tasks"):
        if task.get("is_deleted"):
            continue
        if task.get("project_id") != project_id:
            continue

        rate = float(task.get("rate") or 0) or proj_rate
        desc = (task.get("description") or task.get("number","?")).split("\n")[0].strip()

        raw_log = task.get("time_log") or "[]"
        if isinstance(raw_log, str):
            try:
                raw_log = json.loads(raw_log)
            except Exception:
                raw_log = []

        for entry in raw_log:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            start_ts, end_ts = entry[0], entry[1]
            if not start_ts or not end_ts:
                continue  # running entry
            try:
                start_dt = datetime.fromtimestamp(int(start_ts))
                hours = (int(end_ts) - int(start_ts)) / 3600.0
            except Exception:
                continue
            if hours <= 0:
                continue
            day = start_dt.strftime("%Y-%m-%d")
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            key = (day, desc)
            if key not in rows_by_key:
                rows_by_key[key] = {"date": day, "description": desc,
                                     "hours": 0.0, "rate": rate}
            rows_by_key[key]["hours"] += hours

    rows = []
    for r in rows_by_key.values():
        r["hours"] = round(r["hours"], 2)
        r["amount"] = round(r["hours"] * r["rate"], 2)
        rows.append(r)
    rows.sort(key=lambda x: (x["date"], x["description"]))

    return {
        "project_name": proj.get("name",""),
        "client_name":  client.get("name","") if client else "",
        "rows":         rows,
    }

def build_timesheet_pdf(cfg, data, start_date, end_date, lang="en"):
    buf = io.BytesIO()
    W   = A4[0] - 40*mm
    firma = cfg.get("firma","")
    name  = cfg.get("name","")

    pdf_title = t("pdf_timesheet_title", lang)
    proj_name = data["project_name"]
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
        title=f"{pdf_title} – {proj_name}", author=name)

    s_title = ParagraphStyle("ti", fontSize=22, leading=28, textColor=C_DARK,
                              fontName="Helvetica-Bold", spaceAfter=2*mm)
    s_sub   = ParagraphStyle("su", fontSize=11, textColor=colors.HexColor("#555555"),
                              fontName="Helvetica", spaceAfter=6*mm)
    s_sm    = ParagraphStyle("sm", fontSize=8.5, leading=11,
                              textColor=colors.HexColor("#444444"), fontName="Helvetica")
    s_bold  = ParagraphStyle("bo", fontSize=9, fontName="Helvetica-Bold", textColor=C_DARK)
    s_note  = ParagraphStyle("no", fontSize=9, leading=12,
                              textColor=colors.HexColor("#666666"), fontName="Helvetica")
    s_foot  = ParagraphStyle("ft", fontSize=7, textColor=colors.HexColor("#888888"),
                              fontName="Helvetica", alignment=TA_CENTER)

    if start_date or end_date:
        period = f"{fmt_date(start_date) or '…'} – {fmt_date(end_date) or '…'}"
    else:
        period = t("pdf_timesheet_all", lang)

    story = [
        Paragraph(f"{pdf_title} – {proj_name}", s_title),
        Paragraph(
            f"{t('pdf_timesheet_client', lang)}: {data['client_name']} · "
            f"{t('pdf_timesheet_period', lang)}: {period}", s_sub),
        HRFlowable(width=W, thickness=1.5, color=C_DARK, spaceAfter=6*mm),
    ]

    rows = data["rows"]
    if rows:
        table_rows = [[fmt_date(r["date"]), Paragraph(r["description"], s_sm),
                        f"{r['hours']:.2f}",
                        chf(r["rate"]) if r["rate"] else "—",
                        chf(r["amount"]) if r["amount"] else "—"]
                       for r in rows]
        total_hours  = sum(r["hours"] for r in rows)
        total_amount = sum(r["amount"] for r in rows)
        tbl = Table(
            ([[t("timesheet_col_date",lang), t("timesheet_col_task",lang),
               t("timesheet_col_hours",lang), t("timesheet_col_rate",lang),
               t("timesheet_col_amount",lang)]] + table_rows +
             [["", Paragraph(t("timesheet_total",lang), s_bold),
               f"{total_hours:.2f}", "", chf(total_amount)]]),
            colWidths=[22*mm, W-22*mm-22*mm-26*mm-30*mm, 22*mm, 26*mm, 30*mm],
            repeatRows=1)
        style = [
            ("FONTNAME",     (0,0),(-1,-1), "Helvetica"),
            ("FONTSIZE",     (0,0),(-1,-1), 8.5),
            ("LEADING",      (0,0),(-1,-1), 11),
            ("TOPPADDING",   (0,0),(-1,-1), 2.5),
            ("BOTTOMPADDING",(0,0),(-1,-1), 2.5),
            ("LEFTPADDING",  (0,0),(-1,-1), 4),
            ("RIGHTPADDING", (0,0),(-1,-1), 4),
            ("ALIGN",        (2,0),(-1,-1), "RIGHT"),
            ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
            ("FONTNAME",     (0,0),(-1,0), "Helvetica-Bold"),
            ("BACKGROUND",   (0,0),(-1,0), C_MID),
            ("LINEBELOW",    (0,0),(-1,0), 0.5, C_ACCENT),
            ("LINEABOVE",    (0,-1),(-1,-1), 0.8, C_ACCENT),
            ("FONTNAME",     (0,-1),(-1,-1), "Helvetica-Bold"),
            ("BACKGROUND",   (0,-1),(-1,-1), C_MID),
        ]
        for i in range(len(table_rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND",(0,i+1),(-1,i+1),C_LIGHT))
        tbl.setStyle(TableStyle(style))
        story.append(tbl)
    else:
        story.append(Paragraph(t("timesheet_no_data", lang), s_note))

    story.append(Spacer(1, 8*mm))
    story.append(HRFlowable(width=W, thickness=0.5, color=colors.HexColor("#aaa")))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        f"{t('pdf_created_on', lang)} {date.today().strftime('%d.%m.%Y')} · {name} · {firma}",
        s_foot))

    doc.build(story)
    return buf.getvalue()

# ── Open tasks page ───────────────────────────────────────────────────────────
def build_tasks_body(lang="en"):
    return f"""
<p class="hint" style="margin-bottom:20px">{t('tasks_hint', lang)}</p>
<div id="tasks_status" style="color:#888;font-size:.875rem">{t('tasks_loading', lang)}</div>
<div id="tasks_root"></div>
<script>
const LBL_TASK     = {json.dumps(t('tasks_col_task',     lang))};
const LBL_STATUS   = {json.dumps(t('tasks_col_status',   lang))};
const LBL_HOURS    = {json.dumps(t('tasks_col_hours',    lang))};
const LBL_RATE     = {json.dumps(t('tasks_col_rate',     lang))};
const LBL_EXPECTED = {json.dumps(t('tasks_col_expected', lang))};
const LBL_NO_RATE  = {json.dumps(t('tasks_no_rate',      lang))};
const LBL_TOTAL    = {json.dumps(t('tasks_total',        lang))};
const LBL_EMPTY    = {json.dumps(t('tasks_no_open',      lang))};
const LBL_NOPROJ   = {json.dumps(t('tasks_no_project',   lang))};

function fmtH(h){{
  const hh=Math.floor(h), mm=Math.round((h-hh)*60);
  return hh+'h '+(mm<10?'0':'')+mm+'m';
}}
function fmtC(v){{
  return v?'CHF '+v.toLocaleString('de-CH',{{minimumFractionDigits:2,maximumFractionDigits:2}}):'—';
}}

async function loadTasks(){{
  const r=await fetch('/api/opentasks');
  const tasks=await r.json();
  document.getElementById('tasks_status').textContent='';
  const root=document.getElementById('tasks_root');

  if(!tasks.length){{
    root.innerHTML='<p style="color:#888;font-size:.875rem">'+LBL_EMPTY+'</p>';
    return;
  }}

  // Group by project
  const groups={{}};
  tasks.forEach(tk=>{{
    const g=tk.project_name||LBL_NOPROJ;
    if(!groups[g]) groups[g]=[];
    groups[g].push(tk);
  }});

  let grandTotal=0, grandHours=0;
  Object.entries(groups).forEach(([proj, items])=>{{
    const projTotal=items.reduce((s,tk)=>s+tk.expected_chf,0);
    const projHours=items.reduce((s,tk)=>s+tk.hours,0);
    grandTotal+=projTotal; grandHours+=projHours;

    let rows='';
    items.forEach(tk=>{{
      const rate=tk.rate?'CHF '+tk.rate+'/h':LBL_NO_RATE;
      rows+=`<tr>
        <td>${{tk.name}}</td>
        <td><span style="font-size:.75rem;padding:2px 8px;border-radius:99px;background:#f0f0f5;color:#555">${{tk.status_name}}</span></td>
        <td>${{fmtH(tk.hours)}}</td>
        <td style="color:#888">${{rate}}</td>
        <td>${{tk.expected_chf?fmtC(tk.expected_chf):'—'}}</td>
      </tr>`;
    }});

    root.innerHTML+=`
      <div class="summary" style="margin-bottom:16px">
        <div class="cat-section" style="display:flex;justify-content:space-between;align-items:center">
          <span>${{proj}}</span>
          <span style="font-size:.8rem;font-weight:600;color:#555">
            ${{fmtH(projHours)}}${{projTotal?' · '+fmtC(projTotal):''}}
          </span>
        </div>
        <table>
          <thead style="background:#f0f0f5">
            <tr>
              <th style="text-align:left;padding:5px 16px;font-size:.78rem">${{LBL_TASK}}</th>
              <th style="text-align:left;padding:5px 16px;font-size:.78rem">${{LBL_STATUS}}</th>
              <th style="text-align:right;padding:5px 16px;font-size:.78rem">${{LBL_HOURS}}</th>
              <th style="text-align:right;padding:5px 16px;font-size:.78rem">${{LBL_RATE}}</th>
              <th style="text-align:right;padding:5px 16px;font-size:.78rem">${{LBL_EXPECTED}}</th>
            </tr>
          </thead>
          <tbody>${{rows}}</tbody>
        </table>
      </div>`;
  }});

  // Grand total
  root.innerHTML+=`
    <div class="row total" style="border-radius:8px;border:1.5px solid #dde0ee">
      <span><strong>${{LBL_TOTAL}}</strong></span>
      <span><strong>${{fmtH(grandHours)}}${{grandTotal?' · '+fmtC(grandTotal):''}}
      </strong></span>
    </div>`;
}}
window.addEventListener('DOMContentLoaded', loadTasks);
</script>
"""

# ── Chart page ────────────────────────────────────────────────────────────────
def build_chart_body(lang="en"):
    return f"""
<label for="cyr">{t('label_tax_year', lang)}</label>
<select id="cyr" onchange="loadChart()" style="width:auto;margin-bottom:20px">YEAR_OPTIONS</select>
<div id="chart_status" style="color:#888;font-size:.875rem;margin-bottom:12px">{t('chart_loading', lang)}</div>
<div id="stats" style="display:none;margin-bottom:16px" class="summary">
  <div class="row total">
    <span>{t('chart_total', lang)}</span><span id="stat_total"></span>
  </div>
  <div class="row">
    <span>{t('chart_days', lang)}</span><span id="stat_days"></span>
  </div>
</div>
<canvas id="myChart" style="display:none;width:100%;max-height:360px"></canvas>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const LBL_DAILY  = {json.dumps(t('chart_label_daily', lang))};
const LBL_CUMUL  = {json.dumps(t('chart_label_cumul', lang))};
const LBL_NODATA = {json.dumps(t('chart_no_data', lang))};

let myChart = null;

function fmt(v){{
  return "CHF "+v.toLocaleString('de-CH',{{minimumFractionDigits:2,maximumFractionDigits:2}});
}}

async function loadChart(){{
  const yr = document.getElementById('cyr').value;
  document.getElementById('chart_status').textContent = '{t('chart_loading', lang)}';
  document.getElementById('stats').style.display = 'none';
  document.getElementById('myChart').style.display = 'none';
  if(myChart){{ myChart.destroy(); myChart = null; }}

  const r = await fetch('/api/timechart?year='+yr);
  const d = await r.json();

  if(d.error){{
    document.getElementById('chart_status').textContent = '✗ '+d.error;
    return;
  }}

  const dates = Object.keys(d.daily).sort();
  if(!dates.length){{
    document.getElementById('chart_status').textContent = LBL_NODATA;
    return;
  }}

  // Fill every day of the year (0 for empty days)
  const start = new Date(yr+'-01-01');
  const end   = new Date(yr+'-12-31');
  const allDates=[], dailyVals=[], cumulVals=[];
  let cum=0;
  for(let dt=new Date(start); dt<=end; dt.setDate(dt.getDate()+1)){{
    const k = dt.toISOString().slice(0,10);
    const v = d.daily[k]||0;
    allDates.push(k.slice(5)); // MM-DD
    dailyVals.push(v);
    cum += v;
    cumulVals.push(parseFloat(cum.toFixed(2)));
  }}

  document.getElementById('chart_status').textContent = '';
  document.getElementById('stats').style.display = 'block';
  document.getElementById('stat_total').textContent = fmt(d.total);
  document.getElementById('stat_days').textContent  = dates.length;
  document.getElementById('myChart').style.display  = 'block';

  const ctx = document.getElementById('myChart').getContext('2d');
  myChart = new Chart(ctx, {{
    data: {{
      labels: allDates,
      datasets: [
        {{
          type: 'bar',
          label: LBL_DAILY,
          data: dailyVals,
          backgroundColor: 'rgba(22,33,62,0.75)',
          borderRadius: 2,
          yAxisID: 'y',
        }},
        {{
          type: 'line',
          label: LBL_CUMUL,
          data: cumulVals,
          borderColor: '#2d6a4f',
          backgroundColor: 'rgba(45,106,79,0.08)',
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.3,
          yAxisID: 'y2',
        }}
      ]
    }},
    options: {{
      responsive: true,
      interaction: {{ mode:'index', intersect:false }},
      plugins: {{
        tooltip: {{
          callbacks: {{
            label: ctx => ctx.dataset.label+': '+fmt(ctx.parsed.y)
          }}
        }},
        legend: {{ position:'bottom' }}
      }},
      scales: {{
        x: {{
          ticks: {{ maxTicksLimit:12, maxRotation:0 }},
          grid: {{ display:false }}
        }},
        y: {{
          position:'left',
          title: {{ display:true, text:'CHF / Tag' }},
          ticks: {{ callback: v => 'CHF '+v.toLocaleString('de-CH') }}
        }},
        y2: {{
          position:'right',
          title: {{ display:true, text:'Kumuliert CHF' }},
          grid: {{ drawOnChartArea:false }},
          ticks: {{ callback: v => 'CHF '+v.toLocaleString('de-CH') }}
        }}
      }}
    }}
  }});
}}
window.addEventListener('DOMContentLoaded', loadChart);
</script>
"""

# ── Timesheet page ────────────────────────────────────────────────────────────
def build_timesheet_body(lang="en"):
    return f"""
<p class="hint" style="margin-bottom:20px">{t('timesheet_hint', lang)}</p>
<div class="field-group">
  <div>
    <label for="ts_project">{t('label_project', lang)}</label>
    <select id="ts_project" onchange="loadTimesheet()"></select>
  </div>
</div>
<div class="field-group">
  <div>
    <label for="ts_start">{t('label_start_date', lang)}</label>
    <input id="ts_start" type="date" onchange="loadTimesheet()">
  </div>
  <div>
    <label for="ts_end">{t('label_end_date', lang)}</label>
    <input id="ts_end" type="date" onchange="loadTimesheet()">
  </div>
</div>
<div id="ts_status" style="color:#888;font-size:.875rem">{t('timesheet_loading', lang)}</div>
<div id="ts_root"></div>
<button class="btn btn-primary" id="ts_dl" onclick="downloadTimesheet()"
        style="width:100%;justify-content:center;margin-top:20px" disabled>
  {t('btn_download_timesheet', lang)}
</button>
<script>
const LBL_DATE     = {json.dumps(t('timesheet_col_date',     lang))};
const LBL_TASK     = {json.dumps(t('timesheet_col_task',     lang))};
const LBL_HOURS    = {json.dumps(t('timesheet_col_hours',    lang))};
const LBL_RATE     = {json.dumps(t('timesheet_col_rate',     lang))};
const LBL_AMOUNT   = {json.dumps(t('timesheet_col_amount',   lang))};
const LBL_TOTAL    = {json.dumps(t('timesheet_total',        lang))};
const LBL_EMPTY    = {json.dumps(t('timesheet_no_data',      lang))};
const LBL_SELECT   = {json.dumps(t('timesheet_select_project', lang))};
const LBL_LOADING  = {json.dumps(t('timesheet_loading',      lang))};

function fmtC(v){{
  return v?'CHF '+v.toLocaleString('de-CH',{{minimumFractionDigits:2,maximumFractionDigits:2}}):'—';
}}
function fmtDate(d){{
  const [y,m,day]=d.split('-');
  return day+'.'+m+'.'+y;
}}

async function loadProjects(){{
  const r=await fetch('/api/projects');
  const projects=await r.json();
  const sel=document.getElementById('ts_project');
  sel.innerHTML='<option value="">—</option>'+projects.map(p=>
    `<option value="${{p.id}}">${{p.name}}${{p.client_name?' ('+p.client_name+')':''}}</option>`
  ).join('');
  if(projects.length===1) {{ sel.value=projects[0].id; loadTimesheet(); }}
}}

async function loadTimesheet(){{
  const pid=document.getElementById('ts_project').value;
  const root=document.getElementById('ts_root');
  const status=document.getElementById('ts_status');
  const dlBtn=document.getElementById('ts_dl');
  root.innerHTML='';
  dlBtn.disabled=true;
  if(!pid){{
    status.textContent=LBL_SELECT;
    return;
  }}
  status.textContent=LBL_LOADING;
  const start=document.getElementById('ts_start').value;
  const end=document.getElementById('ts_end').value;
  let url='/api/timesheet?project_id='+encodeURIComponent(pid);
  if(start) url+='&start='+start;
  if(end) url+='&end='+end;
  const r=await fetch(url);
  const d=await r.json();
  status.textContent='';

  if(!d.rows.length){{
    root.innerHTML='<p style="color:#888;font-size:.875rem">'+LBL_EMPTY+'</p>';
    return;
  }}
  dlBtn.disabled=false;

  let rows='', totalHours=0, totalAmount=0;
  d.rows.forEach(r=>{{
    totalHours+=r.hours; totalAmount+=r.amount;
    rows+=`<tr>
      <td>${{fmtDate(r.date)}}</td>
      <td>${{r.description}}</td>
      <td style="text-align:right">${{r.hours.toFixed(2)}}</td>
      <td style="text-align:right;color:#888">${{fmtC(r.rate)}}</td>
      <td style="text-align:right">${{fmtC(r.amount)}}</td>
    </tr>`;
  }});

  root.innerHTML=`
    <div class="summary">
      <table>
        <thead style="background:#f0f0f5">
          <tr>
            <th style="text-align:left;padding:5px 16px;font-size:.78rem">${{LBL_DATE}}</th>
            <th style="text-align:left;padding:5px 16px;font-size:.78rem">${{LBL_TASK}}</th>
            <th style="text-align:right;padding:5px 16px;font-size:.78rem">${{LBL_HOURS}}</th>
            <th style="text-align:right;padding:5px 16px;font-size:.78rem">${{LBL_RATE}}</th>
            <th style="text-align:right;padding:5px 16px;font-size:.78rem">${{LBL_AMOUNT}}</th>
          </tr>
        </thead>
        <tbody>${{rows}}</tbody>
      </table>
      <div class="row total">
        <span>${{LBL_TOTAL}}</span>
        <span>${{totalHours.toFixed(2)}} h · ${{fmtC(totalAmount)}}</span>
      </div>
    </div>`;
}}

function downloadTimesheet(){{
  const pid=document.getElementById('ts_project').value;
  if(!pid) return;
  const start=document.getElementById('ts_start').value;
  const end=document.getElementById('ts_end').value;
  let url='/timesheet/export?project_id='+encodeURIComponent(pid);
  if(start) url+='&start='+start;
  if(end) url+='&end='+end;
  const a=document.createElement('a');
  a.href=url; a.download='Stundenliste.pdf';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}}

window.addEventListener('DOMContentLoaded', loadProjects);
</script>
"""

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

def render_page(title, active, body_html, firma="", lang="en"):
    subtitle = f"{firma} · Invoice Ninja" if firma else "Invoice Ninja"
    html_lang = lang  # e.g. "de", "en"
    nav_export   = t("nav_export", lang)
    nav_settings = t("nav_settings", lang)
    nav_charts   = t("nav_charts", lang)
    nav_tasks    = t("nav_tasks", lang)
    nav_timesheet = t("nav_timesheet", lang)
    app_title    = t("app_title", lang)
    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
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
      <h1>📊 {app_title}</h1>
      <p>{subtitle}</p>
    </div>
    <nav>
      <a href="/" class="{'active' if active=='export' else ''}">{nav_export}</a>
      <a href="/charts" class="{'active' if active=='charts' else ''}">{nav_charts}</a>
      <a href="/tasks" class="{'active' if active=='tasks' else ''}">{nav_tasks}</a>
      <a href="/timesheet" class="{'active' if active=='timesheet' else ''}">{nav_timesheet}</a>
      <a href="/settings" class="{'active' if active=='settings' else ''}">{nav_settings}</a>
      <a href="/logout" style="opacity:.5">⏻</a>
    </nav>
  </div>
  <div class="body">
    {body_html}
  </div>
</div>
</body>
</html>"""

def build_export_body(lang="en"):
    return f"""
<label for="yr">{t('label_tax_year', lang)}</label>
<select id="yr" onchange="loadPreview()">YEAR_OPTIONS</select>
<div class="hint">{t('hint_data_source', lang)}</div>
<div id="preview"></div>
<button class="btn btn-primary" id="btn" onclick="startDl()" style="width:100%;justify-content:center;margin-top:20px">
  <span class="spinner" id="sp"></span>
  <span id="bl">{t('btn_download_pdf', lang)}</span>
</button>
<script>
const LBL_LOADING      = {json.dumps(t('loading', lang))};
const LBL_REVENUE      = {json.dumps(t('revenue', lang))};
const LBL_EXPENSES     = {json.dumps(t('expenses_deduct', lang))};
const LBL_PROFIT       = {json.dumps(t('net_profit', lang))};
const LBL_LOSS         = {json.dumps(t('net_loss', lang))};
const LBL_CATS         = {json.dumps(t('expenses_by_cat', lang))};
const LBL_DL           = {json.dumps(t('btn_download_pdf', lang))};
const LBL_GENERATING   = {json.dumps(t('btn_generating_pdf', lang))};
const LBL_ERR_SERVER   = {json.dumps(t('err_server', lang))};

function fmt(v){{return"CHF "+v.toLocaleString('de-CH',{{minimumFractionDigits:2,maximumFractionDigits:2}})}}
async function loadPreview(){{
  const yr=document.getElementById('yr').value;
  const pv=document.getElementById('preview');
  pv.innerHTML='<div style="text-align:center;padding:20px;color:#888;font-size:.875rem">'+LBL_LOADING+'</div>';
  try{{
    const r=await fetch('/summary?year='+yr);
    const d=await r.json();
    if(d.error){{pv.innerHTML=`<div class="alert alert-err" style="display:block">✗ ${{d.error}}</div>`;return}}
    const profit=d.revenue-d.expenses;
    const pc=profit>=0?'profit':'loss', pl=profit>=0?LBL_PROFIT:LBL_LOSS;
    const sorted=Object.entries(d.categories).sort((a,b)=>b[1]-a[1]);
    let rows='';
    sorted.forEach(([c,v])=>rows+=`<tr><td>${{c}}</td><td>${{fmt(v)}}</td></tr>`);
    pv.innerHTML=`
      <div class="divider"></div>
      <div class="summary">
        <div class="row total"><span>${{LBL_REVENUE}}</span><span>${{fmt(d.revenue)}}</span></div>
        <div class="row total"><span>${{LBL_EXPENSES}}</span><span>${{fmt(d.expenses)}}</span></div>
        <div class="row ${{pc}}"><span>${{pl}} ${{d.year}}</span><span>${{fmt(profit)}}</span></div>
        <div class="cat-section">${{LBL_CATS}}</div>
        <table>${{rows}}</table>
      </div>`;
  }}catch(e){{pv.innerHTML='<div class="alert alert-err" style="display:block">✗ '+LBL_ERR_SERVER+'</div>';}}
}}
function startDl(){{
  const yr=document.getElementById('yr').value;
  const btn=document.getElementById('btn');
  const sp=document.getElementById('sp');
  const bl=document.getElementById('bl');
  btn.disabled=true; sp.style.display='block'; bl.textContent=LBL_GENERATING;
  const a=document.createElement('a');
  a.href='/export?year='+yr;
  a.download='FinancialReport_'+yr+'.pdf';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  setTimeout(()=>{{btn.disabled=false;sp.style.display='none';bl.textContent=LBL_DL;}},4000);
}}
window.addEventListener('DOMContentLoaded',loadPreview);
</script>
"""

def build_settings_body(cfg, lang="en"):
    excl_json   = json.dumps(cfg.get("excluded_categories",[]))
    in_url_val  = cfg.get("in_url","")
    in_token_val = cfg.get("in_token","")
    firma_val   = cfg.get("firma","")
    name_val    = cfg.get("name","")
    return f"""
<form id="sf" onsubmit="return false">
  <h3>{t('h_connection', lang)}</h3>
  <label>{t('label_api_url', lang)}</label>
  <input id="in_url" type="url" placeholder="https://invoices.example.com/api/v1" value="{in_url_val}">
  <div class="hint">{t('hint_api_url', lang)}</div>
  <label>{t('label_api_token', lang)}</label>
  <input id="in_token" type="password" placeholder="••••••••" value="{in_token_val}"
         onfocus="this.type='text'" onblur="this.type='password'">
  <div class="hint">{t('hint_api_token', lang)}</div>
  <div style="margin-bottom:16px">
    <button class="btn btn-secondary" onclick="testConn()">{t('btn_test_conn', lang)}</button>
    <span id="conn_status" style="margin-left:12px;font-size:.85rem"></span>
  </div>
  <div class="divider"></div>
  <h3>{t('h_general', lang)}</h3>
  <div class="field-group">
    <div>
      <label>{t('label_company', lang)}</label>
      <input id="firma" type="text" value="{firma_val}">
    </div>
    <div>
      <label>{t('label_owner', lang)}</label>
      <input id="name" type="text" value="{name_val}">
    </div>
  </div>
  <div class="divider"></div>
  <h3>{"Sprache" if lang=="de" else "Language"}</h3>
  <label>{"Anzeigesprache" if lang=="de" else "Display language"}</label>
  <select id="language" style="width:auto;margin-bottom:16px">
    <option value="auto"  {"selected" if cfg.get("language","auto")=="auto"  else ""}>{"Automatisch (aus Invoice Ninja)" if lang=="de" else "Auto (from Invoice Ninja)"}</option>
    <option value="de"    {"selected" if cfg.get("language","auto")=="de"    else ""}>Deutsch</option>
    <option value="en"    {"selected" if cfg.get("language","auto")=="en"    else ""}>English</option>
  </select>
  <div class="divider"></div>
  <h3>{t('h_categories', lang)} <span style="font-weight:400;font-size:.85rem;color:#888">— {t('hint_categories', lang)}</span></h3>
  <div id="cats_loading" style="color:#888;font-size:.875rem;margin-bottom:16px">{t('loading_cats', lang)}</div>
  <div class="check-grid" id="cats"></div>
  <div style="display:flex;gap:12px;margin-top:8px">
    <button class="btn btn-primary" onclick="saveSettings()">
      <span class="spinner" id="save_sp"></span>
      <span id="save_bl">{t('btn_save', lang)}</span>
    </button>
  </div>
  <div class="alert alert-ok"  id="ok_msg"></div>
  <div class="alert alert-err" id="err_msg"></div>
</form>
<script>
const excluded = new Set({excl_json});
const LBL_NO_CATS     = {json.dumps(t('no_cats', lang))};
const LBL_ERR_CATS    = {json.dumps(t('err_loading_cats', lang))};
const LBL_CONN_OK     = {json.dumps(t('conn_ok', lang))};
const LBL_SAVE        = {json.dumps(t('btn_save', lang))};
const LBL_SAVING      = {json.dumps(t('btn_saving', lang))};
const LBL_SAVED_OK    = {json.dumps(t('saved_ok', lang))};

async function loadCats(){{
  try{{
    const r=await fetch('/api/categories');
    const d=await r.json();
    document.getElementById('cats_loading').style.display='none';
    const grid=document.getElementById('cats');
    d.forEach(c=>{{
      const isExcl=excluded.has(c.name);
      const el=document.createElement('label');
      el.className='check-item'+(isExcl?' excluded':'');
      el.innerHTML=`<input type="checkbox" value="${{c.name}}" ${{isExcl?'checked':''}} onchange="toggleExcl(this)"> ${{c.name}}`;
      grid.appendChild(el);
    }});
    if(!d.length) document.getElementById('cats_loading').textContent=LBL_NO_CATS;
  }}catch(e){{
    document.getElementById('cats_loading').textContent=LBL_ERR_CATS;
  }}
}}

function toggleExcl(cb){{
  cb.closest('label').className='check-item'+(cb.checked?' excluded':'');
}}

async function testConn(){{
  document.getElementById('conn_status').textContent='…';
  const r=await fetch('/api/test',{{
    method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{in_url:document.getElementById('in_url').value,
                         in_token:document.getElementById('in_token').value}})}});
  const d=await r.json();
  const el=document.getElementById('conn_status');
  el.innerHTML=d.ok
    ?`<span class="badge ok">✓ ${{LBL_CONN_OK}}</span>`
    :`<span class="badge">✗ ${{d.message}}</span>`;
}}

async function saveSettings(){{
  const btn=document.getElementById('save_bl');
  const sp=document.getElementById('save_sp');
  sp.style.display='block'; btn.textContent=LBL_SAVING;
  const excl=[...document.querySelectorAll('#cats input:checked')].map(e=>e.value);
  const cfg={{
    in_url:    document.getElementById('in_url').value,
    in_token:  document.getElementById('in_token').value,
    firma:     document.getElementById('firma').value,
    name:      document.getElementById('name').value,
    language:  document.getElementById('language').value,
    excluded_categories: excl,
  }};
  const r=await fetch('/api/settings',{{method:'POST',
    headers:{{'Content-Type':'application/json'}},body:JSON.stringify(cfg)}});
  const d=await r.json();
  sp.style.display='none'; btn.textContent=LBL_SAVE;
  const ok=document.getElementById('ok_msg');
  const err=document.getElementById('err_msg');
  if(d.ok){{ok.textContent=LBL_SAVED_OK; ok.style.display='block';
            err.style.display='none'; setTimeout(()=>ok.style.display='none',3000);}}
  else{{err.textContent='✗ '+d.error; err.style.display='block'; ok.style.display='none';}}
}}

window.addEventListener('DOMContentLoaded',loadCats);
</script>
"""

# ── Login page ────────────────────────────────────────────────────────────────
def build_login_page(error: str = "", prefill_url: str = "") -> str:
    cfg      = load_config()
    lang     = get_in_language(cfg)
    url_val  = prefill_url or cfg.get("in_url", "")
    # Don't pre-fill the placeholder default
    if "example.com" in url_val:
        url_val = ""
    err_html = (f'<div class="alert alert-err" style="display:block">✗ {error}</div>'
                if error else "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login – InvoiceNinjaExtender</title>
<meta http-equiv="content-language" content="{lang}">
<style>
{COMMON_CSS}
.login-wrap {{ max-width: 420px; margin: 60px auto; background: #fff;
               border-radius: 12px; box-shadow: 0 2px 16px rgba(0,0,0,.12);
               overflow: hidden }}
.login-head {{ background: #1a1a2e; color: #fff; padding: 28px 32px; text-align: center }}
.login-head h1 {{ font-size: 1.3rem; font-weight: 700; margin-bottom: 4px }}
.login-head p  {{ font-size: .8rem; opacity: .6 }}
.login-body {{ padding: 28px 32px }}
.login-body p  {{ font-size: .85rem; color: #555; margin-bottom: 18px; line-height: 1.5 }}
</style>
</head>
<body>
<div class="login-wrap">
  <div class="login-head">
    <h1>📊 InvoiceNinjaExtender</h1>
    <p>Financial Report Export</p>
  </div>
  <div class="login-body">
    <p>{"Melde dich mit deinem <strong>Invoice Ninja API-Token</strong> an.<br>Zu finden unter <em>Einstellungen → API-Token</em> in Invoice Ninja." if lang=="de" else "Sign in with your <strong>Invoice Ninja API token</strong>.<br>Find it under <em>Settings → API Tokens</em> in Invoice Ninja."}</p>
    <form method="POST" action="/login">
      <label>{"Invoice Ninja URL" if lang=="de" else "Invoice Ninja URL"}</label>
      <input type="url" name="in_url" placeholder="https://invoices.example.com/api/v1"
             value="{url_val}" autocomplete="url" required>
      <label>API Token</label>
      <input type="password" name="token" placeholder="••••••••••••"
             autocomplete="current-password"
             onfocus="this.type='text'" onblur="this.type='password'"
             required style="margin-bottom:8px">
      {err_html}
      <button class="btn btn-primary" type="submit"
              style="width:100%;justify-content:center;margin-top:12px">
        {"Anmelden" if lang=="de" else "Sign in"}
      </button>
    </form>
  </div>
</div>
</body>
</html>"""

# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info(f"{self.address_string()} {fmt % args}")

    # ── Auth helpers ──────────────────────────────────────────────────────────
    def get_session_id(self) -> str | None:
        cookies = parse_cookies(self.headers.get("Cookie", ""))
        return cookies.get("session")

    def check_auth(self) -> bool:
        """Return True if request has a valid session, else redirect to /login."""
        sid = self.get_session_id()
        if get_session(sid):
            return True
        self.send_response(302)
        self.send_header("Location", "/login")
        self.end_headers()
        return False

    def set_session_cookie(self, sid: str):
        self.send_header(
            "Set-Cookie",
            f"session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_LIFETIME}"
        )

    def clear_session_cookie(self):
        self.send_header(
            "Set-Cookie",
            "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
        )

    # ── Response helpers ──────────────────────────────────────────────────────
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

        # Public routes (no auth required)
        if path == "/login":
            self.send_html(build_login_page())
            return

        if path == "/logout":
            delete_session(self.get_session_id())
            self.send_response(302)
            self.clear_session_cookie()
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # All other routes require a valid session
        if not self.check_auth():
            return

        cfg  = load_config()
        lang = get_in_language(cfg)

        if path in ("/", "/index.html"):
            cur  = date.today().year
            opts = "\n".join(
                f'<option value="{y}"{"selected" if y==cur else ""}>{y}</option>'
                for y in range(cur, 2017, -1))
            body = build_export_body(lang).replace("YEAR_OPTIONS", opts)
            html = render_page(t("app_title", lang), "export", body,
                               cfg.get("firma",""), lang)
            self.send_html(html)

        elif path == "/tasks":
            html = render_page(t("nav_tasks", lang), "tasks",
                               build_tasks_body(lang), cfg.get("firma",""), lang)
            self.send_html(html)

        elif path == "/api/opentasks":
            try:
                self.send_json(get_open_tasks(cfg))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/charts":
            cur  = date.today().year
            opts = "\n".join(
                f'<option value="{y}"{"selected" if y==cur else ""}>{y}</option>'
                for y in range(cur, 2017, -1))
            body = build_chart_body(lang).replace("YEAR_OPTIONS", opts)
            html = render_page(t("nav_charts", lang), "charts", body,
                               cfg.get("firma",""), lang)
            self.send_html(html)

        elif path == "/api/timechart":
            year = int(params.get("year", [date.today().year])[0])
            try:
                daily = get_timechart_data(cfg, year)
                total = round(sum(daily.values()), 2)
                self.send_json({"year": year, "daily": daily, "total": total})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/settings":
            body = build_settings_body(cfg, lang)
            html = render_page(t("nav_settings", lang), "settings", body,
                               cfg.get("firma",""), lang)
            self.send_html(html)

        elif path == "/summary":
            year = int(params.get("year", [date.today().year])[0])
            try:
                rev, exp, profit, cats = get_summary(cfg, year, lang)
                self.send_json({"year": year, "revenue": rev,
                                "expenses": exp, "profit": profit,
                                "categories": cats})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/export":
            year = int(params.get("year", [date.today().year])[0])
            try:
                payments, exp_by_cat, open_inv = load_data(cfg, year, lang)
                pdf_bytes = build_pdf(cfg, year, payments, exp_by_cat,
                                      open_inv, lang)
                fname = f"FinancialReport_{year}.pdf"
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{fname}"')
                self.send_header("Content-Length", len(pdf_bytes))
                self.end_headers()
                self.wfile.write(pdf_bytes)
                log.info(f"PDF {fname} delivered ({len(pdf_bytes)//1024} KB)")
            except Exception as e:
                log.error(f"PDF error: {e}")
                self.send_json({"error": str(e)}, 500)

        elif path == "/timesheet":
            html = render_page(t("nav_timesheet", lang), "timesheet",
                               build_timesheet_body(lang), cfg.get("firma",""), lang)
            self.send_html(html)

        elif path == "/api/projects":
            try:
                self.send_json(get_projects_list(cfg))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/api/timesheet":
            project_id = params.get("project_id", [""])[0]
            start = params.get("start", [None])[0]
            end   = params.get("end", [None])[0]
            try:
                self.send_json(get_timesheet_data(cfg, project_id, start, end))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/timesheet/export":
            project_id = params.get("project_id", [""])[0]
            start = params.get("start", [None])[0]
            end   = params.get("end", [None])[0]
            try:
                data = get_timesheet_data(cfg, project_id, start, end)
                pdf_bytes = build_timesheet_pdf(cfg, data, start, end, lang)
                safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_",
                                    data["project_name"] or project_id).strip("_")
                fname = f"Stundenliste_{safe_name or 'Projekt'}.pdf"
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{fname}"')
                self.send_header("Content-Length", len(pdf_bytes))
                self.end_headers()
                self.wfile.write(pdf_bytes)
                log.info(f"PDF {fname} delivered ({len(pdf_bytes)//1024} KB)")
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

        if path == "/login":
            length = int(self.headers.get("Content-Length", 0))
            raw    = self.rfile.read(length).decode()
            fields = parse_qs(raw)
            token  = fields.get("token",  [""])[0].strip()
            in_url = fields.get("in_url", [""])[0].strip().rstrip("/")
            # Normalise: ensure path ends with /api/v1
            if in_url and not in_url.endswith("/api/v1"):
                in_url = in_url.rstrip("/") + "/api/v1"
            # Validate token against IN
            ok, msg = test_connection(in_url, token)
            if ok:
                # Persist URL (and token) into config so the app is ready to use
                cfg = load_config()
                changed = False
                if in_url and cfg.get("in_url") != in_url:
                    cfg["in_url"] = in_url
                    changed = True
                if token and cfg.get("in_token") != token:
                    cfg["in_token"] = token
                    changed = True
                if changed:
                    save_config(cfg)
                    log.info("Config updated from login form")
                sid = create_session(token)
                self.send_response(302)
                self.set_session_cookie(sid)
                self.send_header("Location", "/")
                self.end_headers()
                log.info(f"Login successful from {self.address_string()}")
            else:
                log.warning(f"Failed login from {self.address_string()}: {msg}")
                self.send_html(build_login_page(
                    error=f"Invalid token or cannot reach Invoice Ninja ({msg})",
                    prefill_url=in_url))
            return

        # All other POST routes require auth
        if not self.check_auth():
            return

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
                    "name":                body.get("name", cfg["name"]),
                    "language":            body.get("language", cfg.get("language","auto")),
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
    log.info(f"Server started on http://0.0.0.0:{port}")
    log.info(f"Config: {CONFIG_PATH}")
    server.serve_forever()
