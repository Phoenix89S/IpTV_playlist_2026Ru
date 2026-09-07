#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IPTV Failover / FULL PATH HLS MATRIX

Логика:
  1) Проверить исходный playlist.
  2) Определить упавшие каналы.
  3) Из трёх SKALA raw-плейлистов собрать ВСЕ реально наблюдавшиеся:
       - URL
       - aliases / tvg-id / names
       - hosts
       - paths
       - alias -> paths
  4) Для каждого упавшего канала построить полный URL-кандидат:
       scheme + observed host + transformed/observed full HLS path
     и проверить именно ПОЛНЫЙ URL.
  5) Все подтверждённые варианты добавляются в megred_auto.m3u.

Новые sXXXXX не угадываются: используются только уже наблюдавшиеся hosts.
Проверка параллельная, с глобальным cache, чтобы не проверять один URL дважды.
"""

import argparse
import concurrent.futures
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
HEADERS = {
    "User-Agent": "Mozilla/5.0 IPTV-Failover/2.0",
    "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, */*",
    "Connection": "close",
}

EXTERNAL_SOURCES = [
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://naggdd.github.io/iptv/ru.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_russia.m3u8",
    "https://raw.githubusercontent.com/IPTVRU2026/IPTVMIR/main/IPTV_MEGA_PLAYLIST.m3u",
]

SCAN_PLAYLISTS = [
    "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/ngSuperscan_SKALA_2.m3u",
    "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/ngenix_found_10.m3u",
    "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/ngSKALA.m3u",
]

CHANNEL_ALIASES = {
    "karusel": ["карусель", "karusel"], "rentv": ["рен тв", "ren tv", "rentv"],
    "tv3": ["тв-3", "тв 3", "tv3", "tv-3"], "mir": ["мир", "mir"],
    "ocean_tv": ["ocean tv", "океан"], "viasat_nature": ["viju nature", "viasat nature"],
    "viasat_explore": ["viju explore", "viasat explore"], "viasat_history": ["viju history", "viasat history"],
    "tiji": ["tiji", "тижи"], "gulli": ["gulli", "гулли"], "nickelodeon": ["nickelodeon"],
    "nicktoons": ["nicktoons"], "baby_tv": ["baby tv", "babytv"], "match_planeta": ["матч планета", "match planeta"],
    "fightbox": ["fightbox"], "trace_sport_stars": ["trace sport stars", "trace sport"],
    "amedia_1": ["amedia 1", "a1"], "amedia_2": ["amedia 2", "a2"], "amedia_premium_hd": ["amedia premium"],
    "amedia_hit": ["amedia hit"], "filmbox": ["filmbox"], "filmbox_arthouse": ["filmbox arthouse"],
    "amc": ["amc"], "dom_kino": ["дом кино", "dom kino"], "dom_kino_premium_hd": ["дом кино премиум", "dom kino premium"],
    "evrokino": ["еврокино", "evrokino"], "illusion_plus": ["иллюзион", "illusion"], "mir_seriala": ["мир сериала"],
    "tv_xxi": ["тв xxi", "tv xxi", "тв 21"], "365_dney_tv": ["365 дней", "365"], "galaxy": ["galaxy"],
    "sony_channel": ["sony channel", "sony"], "sony_turbo": ["sony turbo"], "history_2": ["history 2", "history2"],
    "docubox": ["docubox"], "nostalgia": ["ностальгия", "nostalgia"], "da_vinci": ["da vinci", "да винчи"],
    "kitchen_tv": ["kitchen tv"], "mezzo": ["mezzo"], "tnt_music": ["тнт music", "tnt music"],
    "rtvi": ["rtvi", "ртви"], "tv5_monde": ["tv5 monde"], "fashion_tv": ["fashion tv", "fashion"],
    "viasat_sport": ["viju plus sport", "viasat sport"], "vip_premiere": ["viju plus premiere", "vip premiere"],
    "vip_megahit": ["viju plus megahit", "vip megahit"], "vip_comedy": ["viju plus comedy", "vip comedy"],
    "vip_serial": ["viju plus serial", "vip serial"],
}

# Observed NGENIX inventory from the supplied constellation data.
NGENIX_S_HOSTS = ['s70370','s70371','s70372','s70373','s70374','s70375','s70376','s70377','s70378','s70379','s70380','s70381','s70382','s70383','s70384','s70385','s70386','s70387','s70388','s70389','s70390','s91030','s14131','s20441','s25617','s26881','s34351','s37630','s45177','s55766','s68149','s78511','s80718','s97982','s18209','s92263','s34776','s12662','s69362','s12917','s13511','s14553','s27836','s68400','s79369','s80078','s81121','s84942','s22674','s35761','s40403','s41654','s42963','s64022','s68717','s70205','s72169','s73767','s74794','s95979','s98217']
NGENIX_NAMED_HOSTS = ['zabava-htlive','zabava-htvod','zabava-block-htvod','kprf-htlive','tvgubernia-htlive','vgtrk-htvod','ct-cdn','mos-cdn','rt-mos-htlive','rt-nw-spb-htlive','rt-nw-klgr-htlive','rt-nw-pzav-htlive','rt-nw-komi-htlive','rt-nw-arkh-htlive','rt-nw-vol-htlive','rt-nw-kostroma-htlive','rt-nw-novg-htlive','rt-nw-murm-htlive','rt-nw-kostroma-htlive','rt-ct-tver-htlive','rt-ct-orl-htlive','rt-ct-bryansk-htlive','rt-ct-tula-htlive','rt-ct-yarl-htlive','rt-ct-vlad-htlive','rt-ct-ivan-htlive','rt-ct-belg-htlive','rt-ct-lipetsk-htlive','rt-ct-ryaz-htlive','rt-ct-vrzh-htlive','rt-ct-kursk-htlive','rt-ct-tamb-htlive','rt-vlg-nn-htlive','rt-vlg-samara-htlive','rt-vlg-ul-htlive','rt-vlg-saratov-htlive','rt-vlg-kzn-htlive','rt-vlg-penza-htlive','rt-vlg-chr-htlive','rt-vlg-kirov-htlive','rt-vlg-izhsk-htlive','rt-vlg-srnk-htlive','rt-vlg-yola-htlive','rt-ural-ekt-htlive','rt-ural-chel-htlive','rt-ural-sur-htlive','rt-ural-tum-htlive','rt-sib-omsk-htlive','rt-sib-irk-htlive','rt-sib-krsk-htlive','rt-sib-nsk-htlive','rt-sib-kem-htlive','rt-sib-uude-htlive','rt-sib-abakan-htlive','rt-sib-bul-htlive','rt-sth-krdar-htlive','rt-sth-rd-htlive','rt-sth-elista-htlive','rt-sth-cherks-htlive','rt-sth-vgrad-htlive']
NGENIX_LB_HOSTS = ['rt-sib-omsk-htlive-lb','rt-ural-chel-htlive-lb','rt-vlg-nn-htlive-lb','rt-vlg-samara-htlive-lb','rt-ct-tver-htlive-lb','rt-vlg-kirov-htlive-lb','rt-sib-krsk-htlive-lb','rt-nw-komi-htlive-lb','rt-ct-bryansk-htlive-lb','rt-sib-kem-htlive-lb','rt-sth-krdar-htlive-lb']
NGENIX_ACCOUNT_HOSTS = ['a3569457567-s70378','a3569457435-s78511','a3569458063-s26881','a3569455801-s26881','a3569455919-s26881','a3569458298-s26881','a3569458677-zabava-htlive','a3569458686-zabava-htlive','a787200757-zabava-htlive','a1566399135-s27836','a1566400063-s27836','a1311338307-s26881','a1311338266-vgtrk-htvod','a3569458506-s22674','a3569457538-s72169','a3569455668-s95979','a3569458353-s98217','a3569458406-s81121','a635215904-s73767','a1566400203-s35761','a1566398612-s40403','a775797930-rt-vlg-penza-htlive','a787200748-rt-ct-kostroma-htlive','a787201926-s78511','a3569457767-s70378','a3285275841-s70378','a787200760-s91030','a635216794-s91030','a3285274823-s14131','a3569455826-s14131','a3285275592-s97982']
OBSERVED_SERVICE_HOSTS = ['hlsstr01.svc.iptv.rt.ru']
NGENIX_HOSTS = [f'{x}.cdn.ngenix.net' for x in NGENIX_S_HOSTS + NGENIX_NAMED_HOSTS + NGENIX_LB_HOSTS + NGENIX_ACCOUNT_HOSTS] + OBSERVED_SERVICE_HOSTS

NGENIX_GENERIC_PATHS = [
 '/hls/CH_1TVSD/variant.m3u8','/hls/CH_1TV/variant.m3u8','/hls/CH_RUSSIA1/variant.m3u8','/hls/CH_NTV/variant.m3u8','/hls/CH_5TV/variant.m3u8','/hls/CH_MATCHTV/variant.m3u8','/hls/CH_STS/variant.m3u8','/hls/CH_TNT/variant.m3u8','/hls/CH_KARUSEL/variant.m3u8','/hls/CH_2X2/variant.m3u8','/hls/CH_RUSSIAK/variant.m3u8','/hls/CH_RUSSIA24/variant.m3u8','/hls/CH_PYATNIZZA/variant.m3u8','/hls/CH_DOMASHNIY/variant.m3u8','/hls/CH_PERETZ/variant.m3u8','/hls/CH_TVC/variant.m3u8','/hls/CH_MIR/variant.m3u8','/hls/CH_ZVEZDA/variant.m3u8','/hls/CH_OTR/variant.m3u8','/hls/CH_SUPER/variant.m3u8','/hls/CH_SPAS/variant.m3u8','/hls/CH_DISNEY/variant.m3u8','/hls/CH_TV3/variant.m3u8','/hls/CH_RENTV/variant.m3u8','/hls/CH_CHE/variant.m3u8','/hls/CH_MUZTV/variant.m3u8','/hls/CH_TNT4/variant.m3u8','/hls/CH_NTV_HD/variant.m3u8','/hls/CH_TNTHD/variant.m3u8','/hls/CH_C05_RUSSIA1HD/variant.m3u8','/hls/CH_C03_IZVESTIYAHD/variant.m3u8','/hls/CH_PODMOSKOVIEHD/variant.m3u8','/hls/CH_MOSKVA24HD/variant.m3u8','/hls/CH_VSETVHD/variant.m3u8','/hls/CH_AIVAHD/variant.m3u8','/hls/CH_WEAPON/variant.m3u8','/hls/CH_OHOTAIRYBALKS/variant.m3u8','/hls/CH_STSLOVE/variant.m3u8','/hls/CH_U/variant.m3u8','/hls/CH_CGTNRUS/variant.m3u8','/hls/CH_FUTBALL1HD/variant.m3u8','/index.m3u8','/variant.m3u8','/playlist.m3u8','/rtk_block.m3u8']

SPECIAL_PATHS = {
 's55766.cdn.ngenix.net':['/s55766-media-origin/rline_high/tracks-v1a1/mono.m3u8','/s55766-media-origin/rline_high/index.m3u8'],
 's68149.cdn.ngenix.net':['/s68149-media-origin/lvs/tvgub/tracks-v1a1/mono.m3u8'],
 's78511.cdn.ngenix.net':['/open/_definst_/TVRain_noaudio/chunklist_DVR.m3u8'],
 's26881.cdn.ngenix.net':['/live/smil:russiak.smil/chunklist_b1600000.m3u8'],
 's80718.cdn.ngenix.net':['/hls/CH_KINOMANHD/variant.m3u8'],
 's27836.cdn.ngenix.net':['/hls/radio_rus/playlist_3.m3u8'],
 's92263.cdn.ngenix.net':['/hls-live/streams/channelone/channelone.m3u8'],
 'kprf-htlive.cdn.ngenix.net':['/live/_definst_/stream_high/playlist.m3u8?version=2'],
 'tvgubernia-htlive.cdn.ngenix.net':['/hls/tvgub/index.m3u8'],
}

# Global cache: URL -> bool. This makes the merge stage practically free for URLs already tested.
CHECK_CACHE = {}
CACHE_LOCK = __import__('threading').Lock()


def fetch_url(url, timeout=12):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception:
        return None


def check_stream(url, timeout=3.5):
    """Fast full-URL HLS probe. It checks the actual requested path, not just the host."""
    with CACHE_LOCK:
        if url in CHECK_CACHE:
            return CHECK_CACHE[url]
    ok = False
    try:
        headers = dict(HEADERS)
        headers['Range'] = 'bytes=0-4095'
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            status = getattr(resp, 'status', 200)
            ctype = (resp.headers.get('Content-Type') or '').lower()
            data = resp.read(4096)
            # For m3u8 we require an actual playlist marker; do not call a bare 200 host alive.
            if status < 400:
                sample = data.decode('utf-8', errors='ignore').lstrip('\ufeff\r\n ')
                if '#EXTM3U' in sample or '#EXT-X-' in sample:
                    ok = True
                elif 'mpegurl' in ctype and len(data) > 40:
                    ok = True
                elif not url.lower().split('?',1)[0].endswith('.m3u8') and len(data) > 100:
                    ok = True
    except Exception:
        ok = False
    with CACHE_LOCK:
        CHECK_CACHE[url] = ok
    return ok


def check_many(urls, timeout=3.5, workers=32):
    urls = list(dict.fromkeys(urls))
    if not urls:
        return []
    alive = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(urls))) as pool:
        futs = {pool.submit(check_stream, u, timeout): u for u in urls}
        for f in concurrent.futures.as_completed(futs):
            if f.result():
                alive.append(futs[f])
    return alive


def parse_m3u(text):
    channels = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#EXTINF'):
            tvg = re.search(r'tvg-id="([^"]*)"', line)
            grp = re.search(r'group-title="([^"]*)"', line)
            name = re.sub(r'^\d+\.\s*', '', line.split(',')[-1].strip())
            tid = tvg.group(1) if tvg else name.lower()
            current = {'tvg_id': tid, 'group': grp.group(1) if grp else '', 'name': name, 'streams': []}
            channels.setdefault(tid, current)
        elif line and not line.startswith('#') and current:
            current['streams'].append({'url': line, 'alive': False, 'source': 'original'})
    return channels


def parse_m3u_entries(text, source):
    out, current = [], None
    for line in (text or '').splitlines():
        line = line.strip()
        if line.startswith('#EXTINF'):
            tvg_id = re.search(r'tvg-id="([^"]*)"', line)
            tvg_name = re.search(r'tvg-name="([^"]*)"', line)
            grp = re.search(r'group-title="([^"]*)"', line)
            current = {
                'tvg_id': tvg_id.group(1) if tvg_id else '',
                'tvg_name': tvg_name.group(1) if tvg_name else '',
                'group': grp.group(1) if grp else '',
                'name': line.split(',',1)[1].strip() if ',' in line else '',
                'url': '', 'source': source,
            }
        elif line and not line.startswith('#') and current:
            current['url'] = line
            out.append(current)
            current = None
    return out


def _norm(v):
    return re.sub(r'[^a-z0-9а-яё]+', '', (v or '').lower())


def _alias_variants(v):
    if not v:
        return set()
    v = re.sub(r'^\d+\.\s*', '', v.strip().lower())
    base = {v, v.replace('-', '_'), v.replace(' ', '_'), v.replace('_', ' '), re.sub(r'\s+', '', v)}
    return {x for x in base if x}


def _url_parts(url):
    try:
        u = urlparse(url)
        return u.scheme.lower(), u.netloc.lower(), u.path, u.query
    except Exception:
        return '', '', '', ''


def build_scan_inventory(texts):
    entries, aliases, hosts, paths = [], set(), set(), set()
    alias_to_paths, path_to_aliases = {}, {}
    for source, text in texts:
        for e in parse_m3u_entries(text, source):
            entries.append(e)
            local = set()
            for field in ('tvg_id','tvg_name','name'):
                local |= _alias_variants(e.get(field,''))
            _, host, path, query = _url_parts(e['url'])
            full_path = path + (('?' + query) if query else '')
            if host: hosts.add(host)
            if full_path: paths.add(full_path)
            aliases |= local
            for a in local:
                alias_to_paths.setdefault(a, set()).add(full_path)
            for comp in [x for x in path.split('/') if x]:
                comp = re.sub(r'\.(m3u8?|ts)$','',comp,flags=re.I)
                if comp not in {'hls','index','variant','playlist','master'}:
                    aliases |= _alias_variants(comp)
                    alias_to_paths.setdefault(comp.lower(), set()).add(full_path)
    return {'entries':entries,'aliases':aliases,'hosts':hosts,'paths':paths,'alias_to_paths':alias_to_paths,'path_to_aliases':path_to_aliases}


def _canonical_keys(ch):
    vals = [ch.get('tvg_id',''), ch.get('name','')]
    keys = set()
    for v in vals: keys |= _alias_variants(v)
    for key, av in CHANNEL_ALIASES.items():
        universe = {key, *av}
        if any(_norm(v) in {_norm(x) for x in universe} for v in vals if v):
            keys.add(key)
    return {_norm(x) for x in keys if x}


def _entry_matches_channel(e, ch):
    target = _canonical_keys(ch)
    if not target: return False
    vals = []
    for f in ('tvg_id','tvg_name','name'):
        vals += list(_alias_variants(e.get(f,'')))
    if any(_norm(v) in target for v in vals): return True
    _, _, path, _ = _url_parts(e.get('url',''))
    np = _norm(path)
    return any(k and k in np for k in target)


def scan_playlist_candidates(ch, entries):
    return list({e['url']: e for e in entries if _entry_matches_channel(e,ch)}.values())


def _channel_alias_strings(ch):
    keys = set()
    vals = [ch.get('tvg_id',''), ch.get('name','')]
    for v in vals: keys |= _alias_variants(v)
    for k, av in CHANNEL_ALIASES.items():
        if any(_norm(v) in {_norm(k), *[_norm(x) for x in av]} for v in vals if v):
            keys.add(k); keys.update(av)
    return {x.lower() for x in keys if x}


def _path_alias_token(path):
    parts = [x for x in path.split('/') if x]
    if len(parts) >= 2 and parts[0].lower() == 'hls':
        return re.sub(r'\.(m3u8?|ts)$','',parts[1],flags=re.I).lower()
    return ''


def _path_family(path):
    """Full-path transformations, based only on forms observed in the source scans."""
    p, q = (path.split('?',1)+[''])[:2] if '?' in path else (path,'')
    if not p.startswith('/'): p='/'+p
    out={p + (('?'+q) if q else '')}
    parts=[x for x in p.split('/') if x]
    if len(parts)>=2 and parts[0].lower()=='hls':
        name=parts[1]
        out.update({f'/hls/{name}/index.m3u8',f'/hls/{name}/variant.m3u8',f'/hls/{name}/playlist.m3u8',f'/hls/{name}/master.m3u8'})
        # observed numbered forms
        for n in ('1','2','3'):
            out.update({f'/hls/{name}/{n}/index.m3u8',f'/hls/{name}/{n}/variant.m3u8'})
    return out


def _alias_path_forms(alias):
    """Turn an alias into the common observed HLS path forms, including CH_ form."""
    a = re.sub(r'[^a-zA-Z0-9]+','_',alias).strip('_').lower()
    if not a: return set()
    compact = a.replace('_','')
    forms = {a, compact, a.upper(), compact.upper()}
    if a == 'tv3' or compact == 'tv3': forms |= {'tv_3','TV_3','CH_TV3','CH_R01_TV3'}
    if a == 'sony_turbo': forms |= {'SONYTURBO','CH_SONYTURBO','sony_turbo'}
    return {f'/hls/{x}/index.m3u8' for x in forms} | {f'/hls/{x}/variant.m3u8' for x in forms}


def generated_candidates(ch, inventory, matched, max_candidates=180):
    """Generate FULL URL candidates, not host-only probes."""
    urls=[]
    add=lambda u: urls.append(u) if u not in seen else None
    seen=set()

    # A) exact observed URLs for this channel — highest priority
    for e in matched:
        u=e['url']
        if u not in seen: seen.add(u); urls.append(u)

    aliases=_channel_alias_strings(ch)
    target=_canonical_keys(ch)

    # B) exact observed paths associated with the channel and their observed families
    paths=set()
    for e in matched:
        _,_,p,q=_url_parts(e['url'])
        if p: paths |= _path_family(p + (('?'+q) if q else ''))

    # C) alias -> every path observed for that alias in the 3 scans
    for a, ps in inventory['alias_to_paths'].items():
        if _norm(a) in target or any(_norm(a)==_norm(x) for x in aliases):
            for p in ps: paths |= _path_family(p)

    # D) transform aliases into full observed-style HLS paths
    for a in aliases:
        paths |= _alias_path_forms(a)

    # E) generic paths only when their channel token matches the target.
    for p in NGENIX_GENERIC_PATHS:
        tok=_norm(_path_alias_token(p))
        if tok and (tok in target or any(tok in _norm(a) or _norm(a) in tok for a in aliases)):
            paths.add(p)

    # F) special exact paths if their basename/path token matches alias.
    for host, ps in SPECIAL_PATHS.items():
        for p in ps:
            token=_norm(_path_alias_token(p) or p)
            if any(token and (_norm(a) in token or token in _norm(a)) for a in aliases):
                paths.add(p)

    # Hosts: observed hosts from scans + fixed observed NGENIX inventory + RT service host.
    hosts=set(inventory['hosts']) | set(NGENIX_HOSTS)
    # Keep candidate count bounded, but always cover every path against the most relevant hosts first.
    matched_hosts=[]
    for e in matched:
        _,h,_,_=_url_parts(e['url'])
        if h and h not in matched_hosts: matched_hosts.append(h)
    host_order=matched_hosts + [h for h in sorted(hosts) if h not in matched_hosts]

    # Host affinity: if a path was observed on a host, test that exact pairing first.
    exact_pairs=[(h,e['url']) for e in matched for _,h,_,_ in [_url_parts(e['url'])]]
    for h,u in exact_pairs:
        if u not in seen: seen.add(u); urls.append(u)

    for h in host_order:
        schemes=['http','https'] if h.endswith('.svc.iptv.rt.ru') else ['https','http']
        for p in sorted(paths):
            for scheme in schemes:
                u=f'{scheme}://{h}{p}'
                if u not in seen:
                    seen.add(u); urls.append(u)
                    if len(urls)>=max_candidates: return urls
    return urls


def merge_found(ch, url, source):
    for s in ch['streams']:
        if s['url']==url:
            s['alive']=True
            return
    ch['streams'].append({'url':url,'alive':True,'source':source})


def write_playlist(channels, output):
    lines=['#EXTM3U']; idx=1; seen=set()
    for ch in channels.values():
