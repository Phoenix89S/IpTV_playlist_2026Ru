
import requests, base64, os
from dataclasses import dataclass
from typing import List, Dict

@dataclass
class Source:
    id: str
    git_repo: str
    git_file: str
    commits_limit: int = 20

class DusyaCommitEngine:
    def __init__(self):
        self._commit_cache = set()

    def get_commits(self, source: Source) -> List[str]:
        url = f"{source.git_repo}/commits?path={source.git_file}&per_page={source.commits_limit}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return [c['sha'] for c in r.json()]

    def fetch_file_at_commit(self, source: Source, sha: str) -> str:
        if sha in self._commit_cache:
            return None
        url = f"{source.git_repo}/contents/{source.git_file}?ref={sha}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        content = r.json()['content']
        self._commit_cache.add(sha)
        return base64.b64decode(content).decode('utf-8')

    def parse_m3u(self, m3u_text: str, tag: str) -> List[Dict]:
        channels = []
        lines = m3u_text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXTINF"):
                name = line.split(",")[-1].strip()
                url = lines[i+1].strip() if i+1 < len(lines) else None
                if url:
                    channels.append({"name": name, "url": url, "tag": tag})
        return channels

    def load_commit_channels(self, source: Source) -> List[Dict]:
        channels = []
        for sha in self.get_commits(source):
            m3u_text = self.fetch_file_at_commit(source, sha)
            if m3u_text:
                channels.extend(self.parse_m3u(m3u_text, f"{source.id}_COMMIT"))
        return channels

def merge_channels(*channel_lists: List[Dict]) -> List[Dict]:
    merged = []
    seen = {}
    for channels in channel_lists:
        for ch in channels:
            key = ch["name"]
            if key not in seen:
                seen[key] = 1
                merged.append(ch)
            else:
                seen[key] += 1
                merged.append({**ch, "name": f"{ch['name']} [{seen[key]}]"})
    return merged

def write_m3u(channels: List[Dict], filename: str):
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            f.write(f"#EXTINF:-1,{ch['name']} ({ch['tag']})\n")
            f.write(f"{ch['url']}\n")

def pipeline_run(sources: List[Source]):
    dusya = DusyaCommitEngine()
    all_channels = []
    for src in sources:
        all_channels.extend(dusya.load_commit_channels(src))

    merged = merge_channels(all_channels)

    # MAIN слой
    main_file = "Dusya_iptv_2026.m3u"
    write_m3u(merged, main_file)

    # OLD слой — по канону начиная со второго запуска
    counter_file = "Dusya_old_counter.txt"
    if os.path.exists(counter_file):
        with open(counter_file, "r+") as f:
            counter = int(f.read().strip())
            counter += 1
            f.seek(0)
            f.write(str(counter))
            f.truncate()
    else:
        with open(counter_file, "w") as f:
            f.write("1")
        return  # первый запуск — только MAIN

    old_file = f"Dusya_iptv_2026_old_{counter:02d}.m3u"
    write_m3u(merged, old_file)

# ==========================
# ИСТОЧНИКИ
# ==========================
SOURCE_A = Source(
    id="A",
    git_repo="https://api.github.com/repos/smolnp/IPTVru",
    git_file="IPTVdonor.m3u",
    commits_limit=6
)

SOURCE_B = Source(
    id="B",
    git_repo="https://api.github.com/repos/smolnp/IPTVru",
    git_file="IPTVxxx.m3u",
    commits_limit=6
)

# ==========================
# MAIN + OLD по канону
# ==========================
if __name__ == "__main__":
    pipeline_run([SOURCE_A, SOURCE_B])