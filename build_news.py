#!/usr/bin/env python3
"""Erzeugt docs/index.html: Tages-Highlights, YouTube-Playlist, Ressorts. Nur Standardbibliothek.
Optional: ANTHROPIC_API_KEY setzen -> Highlights werden von Claude redaktionell zusammengefasst."""
import html, json, os, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TOP_FEEDS = [("Tagesschau", "https://www.tagesschau.de/index~rss2.xml"),
             ("Spiegel", "https://www.spiegel.de/schlagzeilen/index.rss"),
             ("DW", "https://rss.dw.com/rdf/rss-de-all")]
FEEDS = {  # Ressort -> [(Quelle, URL)]  (URLs bitte einmal im Browser prüfen)
    "Deutschland": [("Tagesschau", "https://www.tagesschau.de/inland/index~rss2.xml"),
                    ("Spiegel", "https://www.spiegel.de/schlagzeilen/index.rss")],
    "Welt": [("Tagesschau", "https://www.tagesschau.de/ausland/index~rss2.xml"),
             ("DW", "https://rss.dw.com/rdf/rss-de-all")],
    "Wirtschaft": [("Tagesschau", "https://www.tagesschau.de/wirtschaft/index~rss2.xml")],
    "Tech": [("heise", "https://www.heise.de/rss/heise-atom.xml")],
}
YT_CHANNELS = {"tagesschau": "tagesschau", "ZDFheute": "ZDFheute", "DW Deutsch": "dwdeutsch",
               "WELT": "WELTNachrichtensender"}  # Anzeigename -> @Handle (ggf. anpassen)
PER_SECTION, N_TOP, N_VIDEOS = 8, 7, 12
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
def chat(title, link):
    q = (f"Ich möchte über diesen Nachrichtenartikel sprechen: {title}\n{link}\n\n"
         "Lies den Artikel bitte, fasse ihn kurz zusammen und beantworte danach meine Fragen dazu.")
    return (f'<a class="chat" href="https://claude.ai/new?q={urllib.parse.quote(q)}" target="_blank" rel="noopener">'
            '💬 Mit Claude besprechen</a>')

def card(i):
    t = i["date"].astimezone(TZ).strftime("%H:%M") if i["date"] else ""
    s = f'<p>{html.escape(i["summary"])}</p>' if i["summary"] else ""
    return (f'<article><a href="{html.escape(i["link"])}" rel="noopener">{html.escape(i["title"])}</a>{s}'
            f'<small>{html.escape(i["source"])}{" · " + t + " Uhr" if t else ""}</small>{chat(i["title"], i["link"])}</article>')

def hero(top):
    if not top: return ""
    li = "".join(
        f'<li><span class="n">{n}</span><div><a href="{html.escape(t["link"])}" rel="noopener">{html.escape(t["title"])}</a>'
        f'<p>{html.escape(t["detail"])}</p><small>{html.escape(t["source"])}'
        f'{" · in " + str(t["cov"]) + " Quellen" if t["cov"] > 1 else ""}</small>{chat(t["title"], t["link"])}</div></li>' for n, t in enumerate(top, 1))
    return f'<section id="top" class="hero"><h2>Die größten Ereignisse des Tages</h2><ol>{li}</ol></section>'

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
.hero{background:linear-gradient(135deg,#1c1917,#431407);color:#fafaf9;border-radius:16px;padding:8px 24px 24px;margin-top:20px}
.hero h2{border-color:#fb923c;color:#fff}.hero ol{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px 28px}
.hero li{display:flex;gap:14px}.n{font:700 2.2rem/1 Georgia,serif;color:#fb923c;min-width:2rem}
.hero a{color:#fff;font-weight:700;font-size:1.08rem;text-decoration:none}.hero a:hover{color:#fdba74}
.hero p{margin:2px 0;color:#d6d3d1;font-size:.93rem}.hero small{color:#fdba74}
.vwrap{display:grid;grid-template-columns:2fr 1fr;gap:16px}.stage{aspect-ratio:16/9;background:#000;border-radius:12px;overflow:hidden}
.stage iframe,.stage #player{width:100%;height:100%;border:0}
.ctl{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px;color:var(--mu);font-size:.9rem}
.ctl button{background:var(--card);color:var(--tx);border:1px solid var(--bd);border-radius:8px;padding:6px 12px;cursor:pointer}
.list{display:flex;flex-direction:column;gap:8px;max-height:430px;overflow-y:auto}
.v{display:flex;gap:10px;text-align:left;background:var(--card);color:var(--tx);border:1px solid var(--bd);border-radius:10px;padding:6px;cursor:pointer;font:inherit;flex:none}
.v img{width:112px;aspect-ratio:16/9;object-fit:cover;border-radius:6px}.v b{font-size:.88rem;line-height:1.25;display:block}.v.on{border-color:var(--ac);box-shadow:0 0 0 2px var(--ac)}
.v span{display:flex;flex-direction:column;gap:2px}
@media(max-width:800px){.vwrap{grid-template-columns:1fr}.list{flex-direction:row;max-height:none;overflow-x:auto}.v{width:260px}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
article{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px 16px;display:flex;flex-direction:column;gap:6px}
article a{color:var(--tx);font-weight:650;text-decoration:none;line-height:1.3}article a:hover{color:var(--ac)}
article p{margin:0;color:var(--mu);font-size:.92rem}small{color:var(--mu);display:block;margin-top:auto}
a.chat{font-size:.82rem;font-weight:600!important;color:var(--ac)!important;margin-top:4px;display:inline-block}
.hero a.chat{color:#fdba74!important;font-size:.82rem;font-weight:600}
footer{text-align:center;color:var(--mu);padding:20px;font-size:.85rem}
</style></head><body>
<header><h1>Nachrichten-Überblick</h1><p>__DATE__ · aktualisiert um __TIME__ Uhr</p></header>
<nav>__NAV__</nav><main>__BODY__</main>
<footer>Schlagzeilen aus den öffentlichen RSS-Feeds der genannten Quellen, Videos von YouTube – Klick führt zum Original.</footer>
__SCRIPT__</body></html>"""

def main():
    pool = top_pool()
    top = ai_top(pool) or heuristic_top(pool)
    vids = videos()
    data = {}
    for sec, feeds in FEEDS.items():
        seen, items = set(), []
        for s, u in feeds: items += fetch(s, u)
        items.sort(key=lambda i: i["date"].timestamp() if i["date"] else 0, reverse=True)
        data[sec] = [i for i in items if not (i["title"] in seen or seen.add(i["title"]))][:PER_SECTION]
    now = datetime.now(TZ)
    names = (["Top"] if top else []) + (["Videos"] if vids else []) + list(data)
    nav = "".join(f'<a href="#{n.lower()}">{n}</a>' for n in names)
    body = hero(top) + video_block(vids) + "".join(
        f'<section id="{s.lower()}"><h2>{s}</h2><div class="grid">'
        + ("".join(card(i) for i in items) or "<p>Aktuell keine Meldungen abrufbar.</p>") + "</div></section>"
        for s, items in data.items())
    script = ""
    if vids:
        j = json.dumps([{"id": v["id"]} for v in vids]).replace("</", "<\\/")
        script = "<script>" + JS.replace("__V__", j) + "</script>"
    page = (PAGE.replace("__DATE__", now.strftime("%d.%m.%Y")).replace("__TIME__", now.strftime("%H:%M"))
            .replace("__NAV__", nav).replace("__BODY__", body).replace("__SCRIPT__", script))
    Path("docs").mkdir(exist_ok=True)
    Path("docs/index.html").write_text(page, encoding="utf-8")
    print("docs/index.html:", len(top), "Highlights,", len(vids), "Videos,", {s: len(v) for s, v in data.items()})

if __name__ == "__main__": main()
