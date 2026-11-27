#!/bin/bash
# The story factory's hands (bash: it runs on GitHub's Ubuntu runner, see make_story.yml). One form in, one game out:
#   PAYLOAD  = {"story": {...the form...}, "site": "https://.../fernando-game"}   (from the relay)
#   1. stories/<slug>/story.json + status.json (queued)          pushed
#   2. gen_story.py: every word, both languages (GPT-6 Astra)    status: writing
#   3. voices.sh: the voices, English and the story's language   status: voices
#   4. a token; the link; status: done                           pushed
# Every push re-fetches origin/main first, so a fresh force-push of the game from the Mac never loses a story or breaks a run.
# Check locally without spending:   PAYLOAD='{"story":{"him":"A","her":"B","premise":"x","language":"pt"}}' bash worker/factory.sh --check
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"   # the site root: index.html, stories/, factory/
cd "$ROOT"
export STORIES_DIR="$ROOT/stories"
CHECK=0; [ "${1:-}" = "--check" ] && CHECK=1
[ -n "${PAYLOAD:-}" ] || { echo "factory: no PAYLOAD"; exit 2; }

# ---- 1. intake: the form becomes stories/<slug>/story.json; how to reach the tester goes to this log only
read -r SLUG SLANG SITE < <(python3 - <<'PY'
import datetime, json, os, re, sys
p = json.loads(os.environ["PAYLOAD"])
s = p.get("story") or {}
site = str(p.get("site") or "").rstrip("/")
slug = str(s.get("slug") or "")
if not re.fullmatch(r"[a-z0-9-]{3,60}", slug):
    print("factory: bad slug %r" % slug, file=sys.stderr); sys.exit(2)
lang = str(s.get("language") or "pt").lower()
if not re.fullmatch(r"[a-z]{2}", lang):
    lang = "pt"
s["language"] = lang
contact = s.pop("contact", "")
s["tokens"] = {}
d = os.path.join(os.environ["STORIES_DIR"], slug)
os.makedirs(d, exist_ok=True)
json.dump(s, open(os.path.join(d, "story.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("factory: story %s (%s & %s, %s, %s min); contact: %s" % (slug, s.get("him"), s.get("her"), lang, s.get("minutes", 40), contact or "none given"), file=sys.stderr)
print(slug, lang, site)
PY
)
[ -n "${SLUG:-}" ] || { echo "factory: intake failed"; exit 2; }
export SLUG SLANG SITE  # SLANG, not LANG: LANG is the shell's locale
DIR="stories/$SLUG"

status() {  # status <state> <step> <message> [link]
	python3 - "$1" "$2" "$3" "${4:-}" <<'PY'
import datetime, json, os, sys
state, step, msg, link = sys.argv[1:5]
p = os.path.join(os.environ["STORIES_DIR"], os.environ["SLUG"], "status.json")
old = json.load(open(p)) if os.path.exists(p) else {}
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
d = {"slug": os.environ["SLUG"], "language": os.environ["SLANG"], "state": state, "step": int(step), "of": 4, "message": msg,
     "started": old.get("started", now), "updated": now, "link": link or old.get("link", "")}
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("factory: %s (%s/4) %s" % (state, step, msg))
PY
}

push() {  # push <message>: this story's folder onto whatever origin/main is now (the Mac may have force-pushed a new game meanwhile)
	[ "$CHECK" = 1 ] && { echo "factory: (check) would push: $1"; return 0; }
	local tmp; tmp=$(mktemp -d)
	cp -R "$DIR" "$tmp/"
	local i
	for i in 1 2 3; do
		git fetch -q origin main
		git reset -q --hard origin/main
		rm -rf "$DIR"; mkdir -p stories; cp -R "$tmp/$SLUG" "$DIR"
		git add -A "$DIR"
		git -c user.name="story factory" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" commit -qm "story $SLUG: $1" || true
		if git push -q origin HEAD:main; then rm -rf "$tmp"; return 0; fi
		echo "factory: push $i did not land; fetching again"; sleep 5
	done
	rm -rf "$tmp"; echo "factory: could not push"; return 1
}

failed() {
	status failed 0 "the factory stopped: $1"
	push "failed" || true
	exit 1
}
trap 'failed "step $BASH_COMMAND"' ERR

status queued 1 "your story arrived"
push "queued"
[ "$CHECK" = 1 ] && { echo "factory check ok: $DIR/story.json and status.json written, nothing sent"; python3 factory/gen_story.py "$SLUG" --dry-run | head -3; exit 0; }

# ---- 2. the words
status writing 2 "Astra is writing your words, in English and ${SLANG}"
push "writing"
ok=0
for attempt in 1 2; do
	if python3 factory/gen_story.py "$SLUG"; then ok=1; break; fi
	echo "factory: generation attempt $attempt did not fit the game; trying once more"
done
[ "$ok" = 1 ] || failed "the words did not come out right twice"
rm -f "$DIR/generated_raw.json"

# ---- 3. the voices (both languages at once; only missing files are made, so a rerun finishes the job)
status voices 3 "recording the voices"
push "words"
bash factory/voices.sh en --story "$SLUG" &
P1=$!
if [ "$SLANG" != "en" ]; then bash factory/voices.sh "$SLANG" --story "$SLUG" & P2=$!; else P2=""; fi
wait $P1
[ -n "$P2" ] && wait $P2
python3 factory/gen_story.py "$SLUG" --check

# ---- 4. their token and their link
LINK=$(python3 - <<'PY'
import datetime, hashlib, json, os, secrets
d = os.path.join(os.environ["STORIES_DIR"], os.environ["SLUG"])
p = os.path.join(d, "story.json")
s = json.load(open(p, encoding="utf-8"))
tok = secrets.token_urlsafe(8).lower().replace("-", "").replace("_", "")[:10]
s.setdefault("tokens", {})[hashlib.sha256(tok.encode()).hexdigest()] = {"name": "maker", "added": datetime.date.today().isoformat()}
json.dump(s, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("%s/?story=%s&lang=%s&w=%s" % (os.environ["SITE"], os.environ["SLUG"], os.environ["SLANG"], tok))
PY
)
status done 4 "your game is ready" "$LINK"
push "done"
echo "factory: done, $LINK"
