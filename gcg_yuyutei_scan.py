#!/usr/bin/env python3
"""
GCG VAULT - Yuyu-tei daily price scanner.

What it does
  1. Reads source_registry.csv (the 30 category pages) and downloads each page
     politely (delay between requests, honest User-Agent, robots.txt respected).
  2. Extracts every card: product URL, card number, rarity, name, price, stock.
  3. Writes daily_scan_YYYY-MM-DD.csv in the exact daily_scan_template.csv format
     (live_verified=TRUE because the page was downloaded just now by this program).
  4. Optionally runs update_master.py to merge into the price ledger (--run-master).

It does NOT bypass blocking: on HTTP 403/429 it stops that category, logs it as
ACCESS_BLOCKED and moves on.

Usage
  pip install requests beautifulsoup4
  python gcg_yuyutei_scan.py                      # scan all enabled categories
  python gcg_yuyutei_scan.py --latest data/latest.csv  # publish current prices and save price history
  python gcg_yuyutei_scan.py --only GD06 ST01     # scan only some set keys
  python gcg_yuyutei_scan.py --run-master         # scan, then update the ledger
  python gcg_yuyutei_scan.py --save-html          # keep raw HTML in ./html_dump (for debugging)
"""
import argparse, csv, datetime as dt, re, subprocess, sys, time, urllib.parse, urllib.robotparser
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing packages. Run:  pip install requests beautifulsoup4")

BANGKOK = dt.timezone(dt.timedelta(hours=7))
BASE = "https://yuyu-tei.jp"
UA = "GCG-VAULT-PriceResearch/1.0 (personal research; polite, rate-limited)"
SCAN_FIELDS = ["source_url", "price_jpy", "stock_text", "source_set", "card_number", "rarity",
               "name_jp", "observed_at_bangkok", "live_verified", "source_cache_date",
               "discovery_listing_url", "evidence_url"]
PRICE_HISTORY_FIELDS = ["observed_at_bangkok", "source_url", "card_number", "rarity",
                        "source_set", "name_jp", "price_jpy", "stock_text"]
RARITIES = ["LR++", "LR+", "LR", "R+", "R", "U+", "U", "C++", "C+", "C", "SP", "LK", "P"]
RAR_RE = "|".join(re.escape(r) for r in sorted(RARITIES, key=len, reverse=True))
CARD_HREF = re.compile(r"/sell/gcg/card/([^/]+)/(\d+)")
LABEL_RE = re.compile(r"^\s*([A-Z0-9]+-\d{1,3})\s+(" + RAR_RE + r")\s+(.+?)\s*$")
CARDNO_RE = re.compile(r"\b([A-Z0-9]+-\d{1,3})\b")
PRICE_RE = re.compile(r"([\d,]{1,9})\s*円")
STOCK_RE = re.compile(r"在庫\s*[:：]\s*(×|◯|○|\d+\s*点)")
HEAD_RE = re.compile(r"^\s*(" + RAR_RE + r")\s+Card\s+List", re.I)


def load_registry(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("scan_enabled") or "").upper() == "YES"]


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
    return s


def robots_ok(url, rp):
    try:
        return rp.can_fetch(UA, url)
    except Exception:
        return True


def fetch(session, url, delay):
    time.sleep(delay)
    r = session.get(url, timeout=30)
    return r


def block_for(anchor):
    """Climb from a card link to the smallest container that holds this one card's
    price and stock (and only one distinct card URL)."""
    node = anchor
    for _ in range(8):
        node = node.parent
        if node is None:
            return None
        hrefs = {a["href"].split("?")[0] for a in node.find_all("a", href=CARD_HREF)}
        text = node.get_text(" ", strip=True)
        if len(hrefs) > 1:
            return None
        if "在庫" in text and PRICE_RE.search(text):
            return node
    return None


def parse_page(html, listing_url, source_set):
    """Return list of dict rows (one per distinct product URL) found on this page."""
    soup = BeautifulSoup(html, "html.parser")
    rows, seen = [], set()
    # remember the rarity heading ("LR++ Card List") that precedes each card, in document order
    current_rarity = ""
    for el in soup.find_all(True):
        if el.name in ("h1", "h2", "h3", "h4", "h5", "div", "p", "span"):
            t = el.get_text(" ", strip=True)
            if len(t) < 40:
                m = HEAD_RE.match(t)
                if m:
                    current_rarity = m.group(1).upper()
        if el.name != "a" or not el.get("href") or not CARD_HREF.search(el["href"]):
            continue
        href = el["href"].split("?")[0]
        url = urllib.parse.urljoin(BASE, href)
        if url in seen:
            continue
        blk = block_for(el)
        if blk is None:
            continue
        text = blk.get_text(" ", strip=True)
        pm, sm = PRICE_RE.search(text), STOCK_RE.search(text)
        if not pm:
            continue
        price = int(pm.group(1).replace(",", ""))
        if price <= 0:
            continue
        stock = re.sub(r"\s+", " ", sm.group(1)) if sm else ""
        # label like "GD06-002 LR++ name"  (link text or image alt / title)
        label = ""
        for cand in [el.get_text(" ", strip=True), el.get("title", "")] + [i.get("alt", "") for i in el.find_all("img")]:
            if cand and LABEL_RE.match(cand):
                label = cand
                break
        if not label:
            for a2 in blk.find_all("a", href=CARD_HREF):
                for cand in [a2.get_text(" ", strip=True), a2.get("title", "")] + [i.get("alt", "") for i in a2.find_all("img")]:
                    if cand and LABEL_RE.match(cand):
                        label = cand
                        break
                if label:
                    break
        if label:
            m = LABEL_RE.match(label)
            card_no, rarity, name = m.group(1), m.group(2), m.group(3)
        else:  # fallback: card number from text, rarity from section heading
            m = CARDNO_RE.search(text)
            card_no = m.group(1) if m else ""
            rarity = current_rarity
            h = blk.find(["h4", "h3", "h2"])
            name = h.get_text(" ", strip=True) if h else ""
        seen.add(url)
        rows.append(dict(source_url=url, price_jpy=price, stock_text=stock, source_set=source_set,
                         card_number=card_no, rarity=rarity, name_jp=name,
                         discovery_listing_url=listing_url, evidence_url=url))
    next_pages = sorted({urllib.parse.urljoin(listing_url, a["href"]) for a in soup.find_all("a", href=True)
                         if "page=" in a["href"] and urllib.parse.urlsplit(urllib.parse.urljoin(listing_url, a["href"])).path
                         == urllib.parse.urlsplit(listing_url).path})
    set_slugs = sorted({m.group(1) for a in soup.find_all("a", href=True)
                        for m in [re.search(r"/sell/gcg/s/([^/?#]+)", a["href"])] if m})
    return rows, next_pages, set_slugs


def write_price_history(latest_path, rows, stamp):
    """Archive verified observations from this scan, retaining earlier points."""
    observations = {
        (stamp, row["source_url"]): row for row in rows
        if row.get("observed_at_bangkok") == stamp and row.get("live_verified") == "TRUE"
    }
    if not observations:
        return None

    archive = latest_path.parent / "history" / f"prices_{stamp[:10]}.csv"
    history = {}
    if archive.exists():
        with archive.open(encoding="utf-8-sig", newline="") as f:
            history = {(row["observed_at_bangkok"], row["source_url"]): row for row in csv.DictReader(f)}
        if observations.keys() <= history.keys():
            return archive
    for key, row in observations.items():
        history.setdefault(key, row)

    archive.parent.mkdir(parents=True, exist_ok=True)
    staged = NamedTemporaryFile(mode="w", dir=archive.parent, suffix=".tmp",
                                encoding="utf-8-sig", newline="", delete=False)
    temp_path = Path(staged.name)
    try:
        with staged as f:
            writer = csv.DictWriter(f, fieldnames=PRICE_HISTORY_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(history[key] for key in sorted(history))
        temp_path.replace(archive)
    finally:
        temp_path.unlink(missing_ok=True)
    return archive


def write_latest(latest_path, scanned_rows, scan_status, stamp):
    """Publish complete categories and keep the last prices for the others."""
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    prev = {}
    if latest_path.exists():
        with open(latest_path, encoding="utf-8-sig", newline="") as f:
            prev = {r["source_url"]: r for r in csv.DictReader(f)}

    complete_sets = {s for s, status in scan_status.items() if status == "OK"}
    current = {}
    for row in scanned_rows:
        if row["source_set"] in complete_sets:
            current.setdefault(row["source_url"], row)
    history_path = write_price_history(latest_path, current.values(), stamp)
    carried = {u: p for u, p in prev.items() if p["source_set"] not in complete_sets and u not in current}
    carried_sets = {r["source_set"] for r in carried.values()}
    changes = []
    for url, r in current.items():
        p = prev.get(url)
        if p is None:
            if prev:
                changes.append(("NEW", r, "", f'{r["price_jpy"]} / {r["stock_text"]}', "", r["stock_text"]))
        elif str(p["price_jpy"]) != str(r["price_jpy"]):
            changes.append(("PRICE", r, p["price_jpy"], r["price_jpy"], p["stock_text"], r["stock_text"]))
        elif p["stock_text"] != r["stock_text"]:
            changes.append(("STOCK", r, p["stock_text"], r["stock_text"], p["stock_text"], r["stock_text"]))
    removed = [p for u, p in prev.items() if u not in current and u not in carried]
    failed_sets = scan_status.keys() - complete_sets

    ch_path = latest_path.parent / "changes_latest.csv"
    with open(ch_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["change", "source_set", "card_number", "rarity", "name_jp", "old", "new", "source_url",
                    "old_stock_text", "stock_text", "observed_at_bangkok"])
        for kind, r, old, new, old_stock, stock in changes:
            w.writerow([kind, r["source_set"], r["card_number"], r["rarity"], r["name_jp"],
                        old, new, r["source_url"], old_stock, stock, stamp])
        for k in sorted(failed_sets):
            kind = "CATEGORY_FAILED_KEPT_OLD_ROWS" if k in carried_sets else "CATEGORY_FAILED_NO_OLD_ROWS"
            w.writerow([kind, k, "", "", "", "", "", "", "", "", stamp])
        for p in removed:
            w.writerow(["NOT_SEEN_TODAY", p["source_set"], p["card_number"], p["rarity"], p["name_jp"],
                        f'{p["price_jpy"]} / {p["stock_text"]}', "", p["source_url"], p["stock_text"], "", stamp])
        if current and not prev:
            w.writerow(["INITIAL_SNAPSHOT", "", "", "", "", "", "", "", "", "", stamp])
        elif current and not changes and not removed and not failed_sets:
            w.writerow(["NO_CHANGES", "", "", "", "", "", "", "", "", "", stamp])
    if current or not latest_path.exists():
        with open(latest_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SCAN_FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(current.values())
            w.writerows(carried.values())
    print(f"Latest -> {latest_path}   changes -> {ch_path}  (changed={len(changes)}, not_seen={len(removed)})")
    if history_path:
        print(f"Price history -> {history_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default="source_registry.csv")
    ap.add_argument("--only", nargs="*", help="source_set keys, e.g. GD06 ST01 LIMITED1")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between requests (default 3)")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--save-html", action="store_true")
    ap.add_argument("--run-master", action="store_true")
    ap.add_argument("--latest", help="write latest prices, changes from the previous snapshot, and daily price history")
    ap.add_argument("--master", default="price_master_history.csv")
    args = ap.parse_args()

    Path(args.outdir).mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(BANGKOK)
    day = now.strftime("%Y-%m-%d")
    reg = load_registry(args.registry)
    if args.only:
        wanted = {x.upper() for x in args.only}
        reg = [r for r in reg if r["source_set"].upper() in wanted]
    known_slugs = {r["source_slug"] for r in load_registry(args.registry)}

    session = make_session()
    rp = urllib.robotparser.RobotFileParser()
    try:
        rr = session.get(BASE + "/robots.txt", timeout=20)
        if rr.status_code == 200:
            rp.parse(rr.text.splitlines())
        elif 400 <= rr.status_code < 500:
            rp.parse([])            # no robots.txt => nothing disallowed
        else:
            print(f"Warning: robots.txt returned HTTP {rr.status_code}; continuing carefully")
            rp.parse([])
    except Exception as e:
        print(f"Warning: could not read robots.txt ({type(e).__name__}); continuing carefully")
        rp.parse([])
    out_rows, log, unknown = [], [], set()
    dump = Path(args.outdir) / "html_dump"
    if args.save_html:
        dump.mkdir(exist_ok=True)

    for r in reg:
        key, url = r["source_set"], r["listing_url_candidate"]
        status, pages, got = "OK", 0, 0
        queue, done = [url], set()
        while queue and pages < 20:
            u = queue.pop(0)
            if u in done:
                continue
            done.add(u)
            if not robots_ok(u, rp):
                status = "ROBOTS_DISALLOWED"; break
            try:
                resp = fetch(session, u, args.delay)
            except Exception as e:
                status = f"ERROR:{type(e).__name__}"; break
            if resp.status_code in (403, 429):
                status = f"ACCESS_BLOCKED_{resp.status_code}"; break
            if resp.status_code != 200:
                status = f"HTTP_{resp.status_code}"; break
            pages += 1
            if "charset" not in resp.headers.get("content-type", "").lower():
                resp.encoding = "utf-8"      # site is UTF-8; avoid mojibake when charset is not declared
            if args.save_html:
                (dump / f"{key}_p{pages}.html").write_text(resp.text, encoding="utf-8")
            rows, nxt, slugs = parse_page(resp.text, u, key)
            unknown |= {s for s in slugs if s not in known_slugs and s not in ("new", "sale")}
            got += len(rows)
            out_rows += rows
            queue += [n for n in nxt if n not in done]
        if status == "OK" and got == 0:
            status = "PARSED_ZERO_ROWS(check --save-html)"
        log.append((key, status, pages, got))
        print(f"{key:12s} {status:34s} pages={pages} cards={got}")

    stamp = now.isoformat(timespec="seconds")
    # one row per distinct product URL
    uniq = {}
    for row in out_rows:
        row.update(observed_at_bangkok=stamp, live_verified="TRUE", source_cache_date="")
        uniq.setdefault(row["source_url"], row)
    scan_path = Path(args.outdir) / f"daily_scan_{day}.csv"
    with open(scan_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCAN_FIELDS)
        w.writeheader()
        w.writerows(uniq.values())
    if args.latest:
        scan_status = {source_set: status for source_set, status, _, _ in log}
        write_latest(Path(args.latest), out_rows, scan_status, stamp)
    run_path = Path(args.outdir) / f"scan_report_{day}.csv"
    with open(run_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_set", "status", "pages_scanned", "cards_found", "observed_at_bangkok"])
        w.writerows([*row, stamp] for row in log)
    print(f"\nDistinct products: {len(uniq)}  ->  {scan_path}")
    bad = [l for l in log if l[1] != "OK"]
    if bad:
        print("PARTIAL categories (do not claim full coverage):", ", ".join(l[0] for l in bad))
    if unknown:
        print("Unknown category slugs seen on the site (add to source_registry.csv after review):", ", ".join(sorted(unknown)))
    if args.run_master:
        if bad:
            print("Warning: some categories failed; ledger update uses only what was verified.")
        cmd = [sys.executable, "update_master.py", "--master", args.master, "--scan", str(scan_path),
               "--registry", args.registry, "--out", f"price_master_history_{day}.csv",
               "--queue", "new_cards_queue.csv", "--audit", "daily_run_log.csv", "--chart", "chart_events.csv"]
        print("Running:", " ".join(cmd))
        subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
