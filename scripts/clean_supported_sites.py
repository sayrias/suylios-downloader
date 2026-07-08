#!/usr/bin/env python3
"""
Clean and populate missing domains and URLs in SUPPORTED_SITES.json and regenerate SUPPORTED_SITES.md.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
JSON_PATH = ROOT / "SUPPORTED_SITES.json"
MD_PATH = ROOT / "SUPPORTED_SITES.md"

FAMOUS_MAPPINGS = {
    "10play": "10play.com.au",
    "17live": "17.live",
    "1news": "1news.co.nz",
    "1tv": "1tv.ru",
    "20min": "20min.ch",
    "23video": "23video.com",
    "247sports": "247sports.com",
    "24tv.ua": "24tv.ua",
    "3qsdn": "3qsdn.com",
    "3sat": "3sat.de",
    "9gag": "9gag.com",
    "abc": "abc.com",
    "abcnews": "abcnews.go.com",
    "abema": "abema.tv",
    "acast": "acast.com",
    "addanime": "addanime.net",
    "aeon": "aeon.co",
    "aftonbladet": "aftonbladet.se",
    "afreecatv": "afreecatv.com",
    "airmozilla": "mozilla.org",
    "aljazeera": "aljazeera.com",
    "allo": "allo.pl",
    "allure": "allure.com",
    "alphaporno": "alphaporno.com",
    "amara": "amara.org",
    "ancestry": "ancestry.com",
    "angel": "angel.com",
    "aparat": "aparat.com",
    "appleconnect": "apple.com",
    "appletrailers": "apple.com",
    "ard": "ardmediathek.de",
    "arte": "arte.tv",
    "asahi": "asahi.co.jp",
    "atv": "atv.at",
    "audimedia": "audi.com",
    "audioboom": "audioboom.com",
    "audiomack": "audiomack.com",
    "azmedien": "azmedien.ch",
    "baidu": "baidu.com",
    "bandcamp": "bandcamp.com",
    "bbc": "bbc.co.uk",
    "beacon": "beacon.com",
    "beatport": "beatport.com",
    "behoimi": "behoimi.org",
    "bellmedia": "bellmedia.ca",
    "bigflix": "bigflix.com",
    "bilibili": "bilibili.com",
    "biobiochiletv": "biobiochile.cl",
    "bitchute": "bitchute.com",
    "bittube": "bittube.tv",
    "bleacherreport": "bleacherreport.com",
    "blogger": "blogger.com",
    "bloomberg": "bloomberg.com",
    "booru": "booru.org",
    "bostonglobe": "bostonglobe.com",
    "brave": "brave.com",
    "brk": "brk.de",
    "brnet": "br.de",
    "bunkr": "bunkr.si",
    "buzzfeed": "buzzfeed.com",
    "cammodels": "cammodels.com",
    "charliefrance": "charliefrance.com",
    "chaturbate": "chaturbate.com",
    "chilloutzone": "chilloutzone.net",
    "chirb": "chirb.it",
    "cinemax": "cinemax.com",
    "cinedoc": "cinedoc.org",
    "cisco": "cisco.com",
    "clippit": "clippituser.tv",
    "clip hunter": "cliphunter.com",
    "clubic": "clubic.com",
    "clyp": "clyp.it",
    "cnet": "cnet.com",
    "cnn": "cnn.com",
    "comedycentral": "cc.com",
    "conan": "teamcoco.com",
    "condenast": "condenast.com",
    "coub": "coub.com",
    "cozytv": "cozy.tv",
    "cracked": "cracked.com",
    "crackle": "crackle.com",
    "crooksandliars": "crooksandliars.com",
    "crunchyroll": "crunchyroll.com",
    "c-span": "c-span.org",
    "cctv": "cctv.com",
    "curiositystream": "curiositystream.com",
    "curse": "curseforge.com",
    "cyberdrop": "cyberdrop.me",
    "dailymotion": "dailymotion.com",
    "danbooru": "danbooru.donmai.us",
    "daum": "daum.net",
    "deadspin": "deadspin.com",
    "derstandard": "derstandard.at",
    "deviantart": "deviantart.com",
    "dgtle": "dgtle.com",
    "discovery": "discovery.com",
    "discoveryplus": "discoveryplus.com",
    "disneyplus": "disneyplus.com",
    "dotsub": "dotsub.com",
    "douyu": "douyu.com",
    "dplay": "dplay.com",
    "dramafever": "dramafever.com",
    "dropline": "dropline.com",
    "dropbox": "dropbox.com",
    "dtube": "d.tube",
    "duckduckgo": "duckduckgo.com",
    "dumpert": "dumpert.nl",
    "dw": "dw.com",
    "e-hentai": "e-hentai.org",
    "exhentai": "exhentai.org",
    "eporner": "eporner.com",
    "erome": "erome.com",
    "ero-video": "ero-video.net",
    "espn": "espn.com",
    "eurogamer": "eurogamer.net",
    "euronews": "euronews.com",
    "facebook": "facebook.com",
    "fembed": "fembed.com",
    "flickr": "flickr.com",
    "footyroom": "footyroom.com",
    "formula1": "formula1.com",
    "foxnews": "foxnews.com",
    "foxsports": "foxsports.com",
    "france24": "france24.com",
    "francetv": "france.tv",
    "funker530": "funker530.com",
    "furaffinity": "furaffinity.net",
    "gab": "gab.com",
    "gagaoolala": "gagaoolala.com",
    "gameinformer": "gameinformer.com",
    "gamespot": "gamespot.com",
    "gfycat": "gfycat.com",
    "giantbomb": "giantbomb.com",
    "gofile": "gofile.io",
    "gog": "gog.com",
    "goodreads": "goodreads.com",
    "golem": "golem.de",
    "google": "google.com",
    "googledrive": "drive.google.com",
    "gorillavid": "gorillavid.in",
    "gothamist": "gothamist.com",
    "gutefrage": "gutefrage.net",
    "hbo": "hbo.com",
    "hbomax": "hbomax.com",
    "hboasia": "hboasia.com",
    "hearst": "hearst.com",
    "hellogiggle": "hellogiggles.com",
    "hgtv": "hgtv.com",
    "historicfilms": "historicfilms.com",
    "history": "history.com",
    "hitbox": "hitbox.tv",
    "hotmail": "hotmail.com",
    "howstuffworks": "howstuffworks.com",
    "hudl": "hudl.com",
    "huffpost": "huffpost.com",
    "hulu": "hulu.com",
    "humblebundle": "humblebundle.com",
    "hypem": "hypem.com",
    "hyperallergic": "hyperallergic.com",
    "ign": "ign.com",
    "iheart": "iheart.com",
    "imgur": "imgur.com",
    "imdb": "imdb.com",
    "ina": "ina.fr",
    "inc": "inc.com",
    "indiewire": "indiewire.com",
    "infoq": "infoq.com",
    "instagram": "instagram.com",
    "internazionale": "internazionale.it",
    "internetarchive": "archive.org",
    "invidious": "invidious.io",
    "iplayer": "bbc.co.uk/iplayer",
    "iracing": "iracing.com",
    "ironman": "ironman.com",
    "itinerary": "itinerary.com",
    "itv": "itv.com",
    "iwara": "iwara.tv",
    "ixigua": "ixigua.com",
    "izlesene": "izlesene.com",
    "jamendo": "jamendo.com",
    "jiosaavn": "jiosaavn.com",
    "joMonster": "joMonster.org",
    "jstream": "jstream.jp",
    "kakalot": "kakalot.com",
    "kaltura": "kaltura.com",
    "kankan": "kankan.com",
    "karaoketv": "karaoketv.co.il",
    "kelbyone": "kelbyone.com",
    "khanacademy": "khanacademy.org",
    "kick": "kick.com",
    "kicker": "kicker.de",
    "kinopoisk": "kinopoisk.ru",
    "knowyourmeme": "knowyourmeme.com",
    "kofi": "ko-fi.com",
    "kompas": "kompas.com",
    "koreus": "koreus.com",
    "kuaishou": "kuaishou.com",
    "ku6": "ku6.com",
    "kusi": "kusi.com",
    "kuwo": "kuwo.cn",
    "la7": "la7.it",
    "lci": "tf1info.fr",
    "leeco": "leeco.com",
    "viki": "viki.com",
    "vk": "vk.com",
    "vimeo": "vimeo.com",
    "vine": "vine.co",
    "vipprom": "vipprom.net",
    "viu": "viu.com",
    "wdr": "wdr.de",
    "weibo": "weibo.com",
    "vogue": "vogue.com",
    "washingtonpost": "washingtonpost.com",
    "wattpad": "wattpad.com",
    "webtoon": "webtoons.com",
    "weibo": "weibo.com",
    "wired": "wired.com",
    "wisden": "wisden.com",
    "wistia": "wistia.com",
    "wwe": "wwe.com",
    "wykop": "wykop.pl",
    "xboxclips": "xboxclips.com",
    "xhamster": "xhamster.com",
    "ximalaya": "ximalaya.com",
    "xinpuhui": "xinpuhui.com",
    "vrt": "vrt.be",
    "xvideos": "xvideos.com",
    "yandex": "yandex.ru",
    "yapfiles": "yapfiles.ru",
    "yle": "yle.fi",
    "yoohoo": "yoohoo.com",
    "youku": "youku.com",
    "younow": "younow.com",
    "youporn": "youporn.com",
    "youtube": "youtube.com",
    "zdf": "zdf.de",
    "zeenews": "zeenews.india.com",
    "zhihu": "zhihu.com",
    "zoom": "zoom.us",
}

def guess_domain(name: str) -> str:
    clean = name.strip()
    clean_l = clean.lower()
    if clean_l in FAMOUS_MAPPINGS:
        return FAMOUS_MAPPINGS[clean_l]
    # Check if name already has domain extensions like .com, .org, .net, .tv, .ru, .jp, .fr, .de, .ua, .io, .pl, etc.
    if re.search(r'\.(com|org|net|tv|ru|jp|fr|de|it|es|uk|pl|ua|ch|se|no|fi|nl|br|au|nz|ca|io|pro|live|cc|co|si|us|be|at|cl|in|il|cn|id|my|ph|vn|tw|hk|kr|az|by|kz|eu|me|info|biz|xxx|tube|donmai\.us)$', clean_l):
        # Clean prefix like /v/ or http
        dom = re.sub(r'^https?://', '', clean_l).strip('/')
        return dom
    return f"{re.sub(r'[^a-z0-9-]', '', clean_l)}.com"

def main():
    if not JSON_PATH.exists():
        print("JSON file not found.")
        return

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated_count = 0
    for item in data:
        dom = item.get("domain", "").strip()
        url = item.get("url", "").strip()
        name = item.get("name", "").strip()

        if not dom:
            dom = guess_domain(name)
            item["domain"] = dom
            updated_count += 1
        
        if not url or url == "http://" or url == "https://":
            item["url"] = f"https://{dom}"
            updated_count += 1

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Updated {updated_count} fields in SUPPORTED_SITES.json.")

    # Regenerate SUPPORTED_SITES.md
    md_lines = [
        "# Suylios Downloader — Supported Sites",
        "",
        "All listed sites are potentially NSFW.<br>",
        "Not every site is guaranteed to work — extractors can break when sites update.",
        "",
        f"**{len(data)} sites** supported across `yt-dlp`, `gallery-dl` and `cyberdrop-dl`.",
        "",
        "| Site | URL | Features | Auth | Engine |",
        "|---|---|---|---|---|"
    ]

    for s in data:
        name = s.get("name", "")
        url = s.get("url", "")
        dom = s.get("domain", url.replace("https://", "").replace("http://", "").strip("/"))
        feat = s.get("features", "")
        auth = s.get("auth", "No")
        eng = s.get("engine", "")

        url_cell = f"[{dom}]({url})" if url else dom
        md_lines.append(f"| {name} | {url_cell} | {feat} | {auth} | {eng} |")

    with open(MD_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    print(f"Regenerated {MD_PATH} successfully.")

if __name__ == "__main__":
    main()
