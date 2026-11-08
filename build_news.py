#!/usr/bin/env python3
"""Erzeugt docs/index.html: Tages-Highlights, YouTube-Playlist, Ressorts. Nur Standardbibliothek.
Optional: ANTHROPIC_API_KEY setzen -> Highlights werden von Claude redaktionell zusammengefasst."""
import html, json, os, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import lru_cache
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TOP_FEEDS = [("Tagesschau", "https://www.tagesschau.de/index~rss2.xml"),
             ("Spiegel", "https://www.spiegel.de/schlagzeilen/index.rss"),
             ("DW", "https://rss.dw.com/rdf/rss-de-all")]
TS = "https://www.tagesschau.de"
GEN = [("Tagesschau", TS + "/index~rss2.xml"), ("Tagesschau", TS + "/inland/index~rss2.xml"),
       ("Tagesschau", TS + "/ausland/index~rss2.xml"), ("Spiegel", "https://www.spiegel.de/schlagzeilen/index.rss"),
       ("DW", "https://rss.dw.com/rdf/rss-de-all")]
# Reihenfolge = Priorität: eine Meldung erscheint nur im ersten passenden Ressort.
# kw = Stichwort-Filter (Regex) auf Titel+Teaser. URLs bitte einmal im Browser prüfen.
RESSORTS = [
    dict(name="Bundesregierung", hue=215, feeds=GEN,
         kw=r"bundesregierung|\bmerz\b|kanzler|koalition|bundestag|kabinett|bundesminister|klingbeil|wadephul|bundesrat"),
    dict(name="China", hue=0, feeds=GEN, kw=r"china|chines|peking|xi jinping|taiwan|hongkong|shanghai"),
    dict(name="Iran", hue=150, feeds=GEN, kw=r"\biran|teheran|hormus"),
    dict(name="Infos und Ereignisse in Deutschland", nav="Deutschland", hue=45,
         feeds=[("Tagesschau", TS + "/inland/index~rss2.xml"), ("Spiegel", "https://www.spiegel.de/panorama/index.rss"),
                ("Spiegel", "https://www.spiegel.de/schlagzeilen/index.rss")]),
    dict(name="Wissenschaft, Entdeckungen und Raumfahrt", nav="Wissenschaft", hue=270,
         feeds=[("Spiegel", "https://www.spiegel.de/wissenschaft/index.rss"), ("DW", "https://rss.dw.com/rdf/rss-de-wissen")]),
    dict(name="Kunst", hue=320,
         feeds=[("Spiegel", "https://www.spiegel.de/kultur/index.rss"), ("DW", "https://rss.dw.com/rdf/rss-de-kul")]),
]
WORLD_FEEDS = [("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"), ("Guardian", "https://www.theguardian.com/world/rss"),
               ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"), ("NYT", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
               ("France 24", "https://www.france24.com/en/rss"), ("DW English", "https://rss.dw.com/rdf/rss-en-world"),
               ("Sky News", "https://feeds.skynews.com/feeds/rss/world.xml")]
EN_STOP = set("says said after over with from will what have more news live world latest about their into amid could would year first "
              "this that they been were than when where while which also there other against between during under".split())
YT_CHANNELS = {"tagesschau": "tagesschau", "ZDFheute": "ZDFheute", "DW Deutsch": "dwdeutsch",
               "WELT": "WELTNachrichtensender"}  # Anzeigename -> @Handle (ggf. anpassen)
PER_SECTION, N_TOP, N_VIDEOS = 6, 7, 12
TZ = ZoneInfo("Europe/Berlin")
UA = {"User-Agent": "Mozilla/5.0 news-overview/2.0"}

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read()

def strip(t, n=190):
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"

def parse_date(s):
    for f in (parsedate_to_datetime, lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            d = f(s)
            if d.tzinfo: return d
        except Exception: pass
    return None

def local(tag): return tag.rsplit("}", 1)[-1]

def entries(source, url):
    try: root = ET.fromstring(get(url))
    except Exception as e:
        print(f"WARNUNG {source}: {e}", file=sys.stderr); return []
    out = []
    for el in root.iter():
        if local(el.tag) in ("item", "entry"):
            out.append({local(c.tag): c for c in el})
    return out

@lru_cache(maxsize=None)
def fetch(source, url):
    items = []
    for f in entries(source, url):
        title = strip(f["title"].text if "title" in f else "", 200)
        link = (f["link"].get("href") or f["link"].text or "").strip() if "link" in f else ""
        d = next((parse_date(f[k].text) for k in ("pubDate", "date", "published", "updated") if k in f and f[k].text), None)
        summ = next((f[k].text for k in ("description", "summary") if k in f), "")
        if title and link.startswith("http"):
            items.append(dict(title=title, link=link, source=source, date=d, summary=strip(summ)))
    return items

# ---------- Top-Ereignisse ----------
STOP = set("der die das und im ist zu von mit für auf den dem ein eine nach bei als um an aus über wie was wird sind hat nicht auch".split())
def words(t): return {w for w in re.findall(r"[a-zäöüß]{4,}", t.lower()) if w not in STOP}

def top_pool():
    pool = []
    for s, u in TOP_FEEDS:
        for pos, i in enumerate(fetch(s, u)[:12]): i["pos"] = pos; pool.append(i)
    return pool

def heuristic_top(pool):
    ws = [words(i["title"] + " " + i["summary"]) for i in pool]
    for n, i in enumerate(pool):
        i["cov"] = 1 + len({j["source"] for m, j in enumerate(pool) if j["source"] != i["source"] and len(ws[n] & ws[m]) >= 2})
    chosen = []
    for n in sorted(range(len(pool)), key=lambda n: (-pool[n]["cov"], pool[n]["pos"])):
        if all(len(ws[n] & ws[m]) < 3 for m in chosen): chosen.append(n)
        if len(chosen) == N_TOP: break
    return [dict(title=pool[n]["title"], detail=pool[n]["summary"], link=pool[n]["link"],
                 source=pool[n]["source"], cov=pool[n]["cov"]) for n in chosen]

def ai_top(pool):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not pool: return None
    lst = "\n".join(f'{n}. [{i["source"]}] {i["title"]} – {i["summary"]}' for n, i in enumerate(pool))
    prompt = (f"Hier sind aktuelle Schlagzeilen deutscher Medien:\n{lst}\n\nWähle die {N_TOP} größten, wichtigsten Ereignisse des Tages "
              "(Themen, die mehrere Quellen bringen, zählen mehr; thematisch breit mischen). Antworte NUR mit einem JSON-Array aus Objekten: "
              '{"titel": max. 8 Wörter, "detail": ein sachlicher Satz auf Deutsch, "quelle": Index der besten Meldung, "cov": Anzahl berichtender Quellen}. '
              "Nutze nur Fakten aus den Meldungen.")
    body = json.dumps({"model": "claude-sonnet-5-5", "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", body, {
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        txt = json.load(urllib.request.urlopen(req, timeout=90))["content"][0]["text"]
        arr = json.loads(re.search(r"\[.*\]", txt, re.S).group(0))
        return [dict(title=a["titel"], detail=a["detail"], link=pool[a["quelle"]]["link"],
                     source=pool[a["quelle"]]["source"], cov=int(a.get("cov", 1))) for a in arr][:N_TOP]
    except Exception as e:
        print("WARNUNG KI-Zusammenfassung:", e, file=sys.stderr); return None

# ---------- YouTube ----------
def videos():
    out = []
    for name, handle in YT_CHANNELS.items():
        try:
            m = re.search(r"channel_id=(UC[\w-]{22})", get(f"https://www.youtube.com/@{handle}").decode("utf-8", "ignore"))
            if not m: raise ValueError("Channel-ID nicht gefunden")
            for f in entries(name, f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}")[:6]:
                vid = f["videoId"].text
                out.append(dict(id=vid, title=strip(f["title"].text, 110), channel=name,
                                date=(parse_date(f["published"].text) if "published" in f else None)))
        except Exception as e:
            print(f"WARNUNG YouTube {name}: {e}", file=sys.stderr)
    out.sort(key=lambda v: v["date"].timestamp() if v["date"] else 0, reverse=True)
    return out[:N_VIDEOS]

# ---------- HTML ----------
NOW = datetime.now(TZ)
def chat(title, link):
    q = (f"Ich möchte über diesen Nachrichtenartikel sprechen: {title}\n{link}\n\n"
         "Lies den Artikel bitte, fasse ihn kurz zusammen und beantworte danach meine Fragen dazu.")
    return (f'<a class="chat" href="https://claude.ai/new?q={urllib.parse.quote(q)}" target="_blank" rel="noopener">'
            '💬 Claude befragen</a>')

def meta(i):
    d = i.get("date")
    when = ""
    if d:
        d = d.astimezone(TZ)
        when = d.strftime(" · %H:%M Uhr") if d.date() == NOW.date() else d.strftime(" · %d.%m.")
    return html.escape(i["source"]) + when

def block(bid, title, entries, hue, cls=""):
    li = "".join(
        f'<li><span class="n">{n}</span><div><a href="{html.escape(e["link"])}" rel="noopener">{html.escape(e["title"])}</a>'
        + (f'<p>{html.escape(e["detail"])}</p>' if e.get("detail") else "")
        + f'<small>{e["meta"]}</small>{chat(e["title"], e["link"])}</div></li>' for n, e in enumerate(entries, 1))
    li = li or "<li><div><p>Aktuell keine passenden Meldungen gefunden.</p></div></li>"
    return f'<section id="{bid}" class="blk {cls}" style="--h:{hue}"><h2>{html.escape(title)}</h2><ol>{li}</ol></section>'

def slug(t): return re.sub(r"[^a-z0-9]+", "-", t.lower().replace("ä","ae").replace("ö","oe").replace("ü","ue").replace("ß","ss")).strip("-")

def ressort_items(r, used):
    items = [i for s, u in r["feeds"] for i in fetch(s, u)]
    if r.get("kw"): items = [i for i in items if re.search(r["kw"], i["title"] + " " + i["summary"], re.I)]
    items.sort(key=lambda i: i["date"].timestamp() if i["date"] else 0, reverse=True)
    out = []
    for i in items:
        if i["link"] in used or i["title"] in used: continue
        used.update((i["link"], i["title"]))
        out.append(dict(title=i["title"], detail=i["summary"], link=i["link"], meta=meta(i)))
        if len(out) == PER_SECTION: break
    return out

def video_block(vs):
    if not vs: return ""
    items = "".join(
        f'<button class="v" data-i="{n}"><img loading="lazy" src="https://i.ytimg.com/vi/{v["id"]}/mqdefault.jpg" alt="">'
        f'<span><b>{html.escape(v["title"])}</b><small>{html.escape(v["channel"])}</small></span></button>' for n, v in enumerate(vs))
    return ('<section id="videos"><h2>Videos</h2><div class="vwrap"><div><div class="stage"><div id="player"></div></div>'
            '<div class="ctl"><button id="prev">⏮ Zurück</button><button id="next">Weiter ⏭</button>'
            '<label><input type="checkbox" id="auto" checked> Automatisch nächstes Video</label></div></div>'
            f'<div class="list">{items}</div></div></section>')

JS = """const V=__V__;let cur=0,P=null;const $=s=>document.querySelector(s);const btns=[...document.querySelectorAll('.v')];
function mark(){btns.forEach((b,i)=>b.classList.toggle('on',i===cur))}
function play(i){cur=(i+V.length)%V.length;mark();if(P&&P.loadVideoById){P.loadVideoById(V[cur].id)}else{
$('.stage').innerHTML='<iframe src="https://www.youtube-nocookie.com/embed/'+V[cur].id+'?autoplay=1&rel=0" allow="autoplay;fullscreen" allowfullscreen></iframe>'}}
btns.forEach((b,i)=>b.onclick=()=>play(i));$('#next').onclick=()=>play(cur+1);$('#prev').onclick=()=>play(cur-1);
window.onYouTubeIframeAPIReady=()=>{P=new YT.Player('player',{host:'https://www.youtube-nocookie.com',videoId:V[0].id,
playerVars:{rel:0,playsinline:1},events:{onStateChange:e=>{if(e.data===0&&$('#auto').checked)play(cur+1)}}})};
mark();const s=document.createElement('script');s.src='https://www.youtube.com/iframe_api';document.head.appendChild(s);"""

PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Nachrichten-Überblick</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--tx:#1b1b1d;--mu:#6b6b72;--ac:#c2410c;--bd:#e4e2dc;color-scheme:light dark}
@media(prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e21;--tx:#ececee;--mu:#9a9aa3;--ac:#fb923c;--bd:#2d2d32}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{max-width:1100px;margin:0 auto;padding:32px 20px 8px}h1{font-family:Georgia,serif;font-size:2rem;margin:0}header p{color:var(--mu);margin:4px 0 0}
nav{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--bd);padding:10px 20px;display:flex;gap:16px;overflow-x:auto}
nav a{color:var(--mu);text-decoration:none;font-weight:600;white-space:nowrap}nav a:hover{color:var(--ac)}
main{max-width:1100px;margin:0 auto;padding:8px 20px 40px}
h2{font-family:Georgia,serif;border-left:4px solid var(--ac);padding-left:10px;margin:28px 0 12px}
.blk{--h:20;--a:hsl(var(--h) 90% 62%);background:linear-gradient(135deg,hsl(var(--h) 18% 11%),hsl(var(--h) 55% 17%));color:#fafaf9;border-radius:16px;padding:8px 24px 24px;margin-top:20px}
.blk h2{border-color:var(--a);color:#fff}.blk ol{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px 28px}
.blk li{display:flex;gap:14px}.n{font:700 2.2rem/1 Georgia,serif;color:var(--a);min-width:2rem}
.blk a{color:#fff;font-weight:700;font-size:1.08rem;text-decoration:none}.blk a:hover{color:hsl(var(--h) 90% 78%)}
.blk p{margin:2px 0;color:#d6d3d1;font-size:.93rem}.blk small{color:hsl(var(--h) 80% 76%);display:block}
.blk a.chat{color:var(--a);font-size:.82rem;font-weight:600;display:inline-block;margin-top:4px}
.blk small a{font-size:.82rem;font-weight:500;text-decoration:underline;color:inherit}
.blk.world{margin-top:24px;border:2px solid var(--a);padding-bottom:30px}.world ol{grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:22px 32px}
.world .n{font-size:3.4rem}.world li>div>a:first-child{font-size:1.3rem}.world p{font-size:1rem}
.vwrap{display:grid;grid-template-columns:2fr 1fr;gap:16px}.stage{aspect-ratio:16/9;background:#000;border-radius:12px;overflow:hidden}
.stage iframe,.stage #player{width:100%;height:100%;border:0}
.ctl{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px;color:var(--mu);font-size:.9rem}
.ctl button{background:var(--card);color:var(--tx);border:1px solid var(--bd);border-radius:8px;padding:6px 12px;cursor:pointer}
.list{display:flex;flex-direction:column;gap:8px;max-height:430px;overflow-y:auto}
.v{display:flex;gap:10px;text-align:left;background:var(--card);color:var(--tx);border:1px solid var(--bd);border-radius:10px;padding:6px;cursor:pointer;font:inherit;flex:none}
.v img{width:112px;aspect-ratio:16/9;object-fit:cover;border-radius:6px}.v b{font-size:.88rem;line-height:1.25;display:block}.v.on{border-color:var(--ac);box-shadow:0 0 0 2px var(--ac)}
.v span{display:flex;flex-direction:column;gap:2px}.v small{color:var(--mu)}
@media(max-width:800px){.vwrap{grid-template-columns:1fr}.list{flex-direction:row;max-height:none;overflow-x:auto}.v{width:260px}}
footer{text-align:center;color:var(--mu);padding:20px;font-size:.85rem}
</style></head><body>
<header><h1>Nachrichten-Überblick</h1><p>__DATE__ · aktualisiert um __TIME__ Uhr</p></header>
<nav>__NAV__</nav><main>__BODY__</main>
<footer>Schlagzeilen aus den öffentlichen RSS-Feeds der genannten Quellen, Videos von YouTube – Klick führt zum Original.</footer>
__SCRIPT__</body></html>"""

# ---------- Die 3 wichtigsten Ereignisse weltweit ----------
def en_words(t): return {w for w in re.findall(r"[a-z]{4,}", t.lower()) if w not in EN_STOP}
def related(a, b):
    x = a["w"] & b["w"]
    return len(x) >= 2 and len(x) / len(a["w"] | b["w"]) >= 0.2

def claude_text(prompt):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key: return None
    body = json.dumps({"model": "claude-sonnet-5-5", "max_tokens": 1000, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", body, {
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try: return json.load(urllib.request.urlopen(req, timeout=90))["content"][0]["text"]
    except Exception as e:
        print("WARNUNG Claude:", e, file=sys.stderr); return None

def world_top():
    """Die 3 Themen, über die die meisten internationalen Quellen berichten."""
    cutoff = NOW.timestamp() - 36 * 3600
    items = []
    for src, url in WORLD_FEEDS:
        for pos, i in enumerate(fetch(src, url)[:20]):
            if i["date"] and i["date"].timestamp() < cutoff: continue
            items.append(dict(i, pos=pos, w=en_words(i["title"] + " " + i["summary"])))
    clusters = []
    for _ in range(3):
        best = None
        for a in items:
            grp = {}
            for b in sorted(items, key=lambda b: b["pos"]):
                if (b is a or (b["source"] != a["source"] and related(a, b))) and b["source"] not in grp: grp[b["source"]] = b
            key = (len(grp), -a["pos"])
            if best is None or key > best[0]: best = (key, a, list(grp.values()))
        if not best or best[0][0] < 2: break
        _, seed, grp = best
        clusters.append((seed, grp))
        items = [i for i in items if not related(seed, i) and i is not seed]
    de = None
    txt = claude_text("Fasse diese 3 Weltthemen auf Deutsch zusammen. Antworte NUR mit JSON-Array in gleicher Reihenfolge aus Objekten "
                      '{"titel": max. 8 Wörter, "detail": ein sachlicher Satz}. Nutze nur Fakten aus den Meldungen.\n\n'
                      + "\n\n".join(f"Thema {n}:\n" + "\n".join(f"- {g['title']}: {g['summary']}" for g in grp)
                                    for n, (_, grp) in enumerate(clusters, 1))) if clusters else None
    try: de = json.loads(re.search(r"\[.*\]", txt, re.S).group(0)) if txt else None
    except Exception: de = None
    out = []
    for n, (seed, grp) in enumerate(clusters):
        links = " · ".join(f'<a href="{html.escape(g["link"])}" rel="noopener">{html.escape(g["source"])}</a>' for g in grp)
        ok = de and n < len(de)
        out.append(dict(title=de[n]["titel"] if ok else seed["title"], detail=de[n]["detail"] if ok else seed["summary"],
                        link=seed["link"], meta=f"Weltweit in {len(grp)} Quellen: {links}" + ("" if ok else " (Originaltitel englisch)")))
    return out

def main():
    pool = top_pool()
    top = ai_top(pool) or heuristic_top(pool)
    world = world_top()
    vids = videos()
    used, secs = set(), []
    for r in RESSORTS:
        secs.append((r, ressort_items(r, used)))
    nav_items = ([("welt", "Top 3 weltweit")] if world else []) + ([("top", "Top")] if top else []) + ([("videos", "Videos")] if vids else []) \
        + [(slug(r["name"]), r.get("nav", r["name"])) for r, _ in secs]
    nav = "".join(f'<a href="#{i}">{n}</a>' for i, n in nav_items)
    top_entries = [dict(title=t["title"], detail=t["detail"], link=t["link"],
                        meta=html.escape(t["source"]) + (f" · in {t['cov']} Quellen" if t["cov"] > 1 else "")) for t in top]
    body = (block("welt", "Die 3 wichtigsten Ereignisse des Tages", world, 48, "world") if world else "") + (block("top", "Mehr Top-Meldungen aus Deutschland", top_entries, 20) if top else "") + video_block(vids) \
        + "".join(block(slug(r["name"]), r["name"], items, r["hue"]) for r, items in secs)
    script = ""
    if vids:
        j = json.dumps([{"id": v["id"]} for v in vids]).replace("</", "<\\/")
        script = "<script>" + JS.replace("__V__", j) + "</script>"
    page = (PAGE.replace("__DATE__", NOW.strftime("%d.%m.%Y")).replace("__TIME__", NOW.strftime("%H:%M"))
            .replace("__NAV__", nav).replace("__BODY__", body).replace("__SCRIPT__", script))
    Path("docs").mkdir(exist_ok=True)
    Path("docs/index.html").write_text(page, encoding="utf-8")
    print("docs/index.html:", len(world), "Weltthemen,", len(top), "Highlights,", len(vids), "Videos,", {r["name"]: len(i) for r, i in secs})

if __name__ == "__main__": main()
