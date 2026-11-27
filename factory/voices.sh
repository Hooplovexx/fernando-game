#!/bin/zsh
# Generate any missing voice lines with OpenAI TTS (gpt-4o-mini-tts). Only files that do not exist yet are generated (delete a file to redo it).
#   zsh voices.sh                    Fernando's English lines (voice_lines.json) into assets/voice
#   zsh voices.sh pt                 Fernando's Portuguese lines (voice_lines_pt.json) into assets/voice/pt
#   zsh voices.sh en --story <slug>  a story's lines (stories/<slug>/voice_lines.json) into stories/<slug>/voice, in the story's own voices
#   zsh voices.sh pt --story <slug>  the same in Portuguese, into stories/<slug>/voice/pt
# Needs OPENAI_API_KEY in ~/Projects/astra-api/.env. About a cent a line.
set -u
cd "$(dirname "$0")"
[ -f ../../.env ] && { set -a; . ../../.env; set +a; }  # on GitHub Actions the key is already in the environment
export VOICE_LANG="en"
export STORIES_DIR="${STORIES_DIR:-stories}"
export VOICE_STORY=""
for a in "$@"; do
	case "$a" in
		[a-z][a-z]) VOICE_LANG="$a";;  # any language code: pt, es, fr, ja ...
		--story) ;;
		*) VOICE_STORY="$a";;
	esac
done
python3 - <<'PY'
import json, os, urllib.request, pathlib
key = os.environ["OPENAI_API_KEY"]
lang = os.environ.get("VOICE_LANG", "en")
story = os.environ.get("VOICE_STORY", "")
folder = pathlib.Path(os.environ.get("STORIES_DIR", "stories")) / story if story else pathlib.Path(".")
lines_file = folder / ("voice_lines.json" if lang == "en" else "voice_lines_%s.json" % lang)
if not lines_file.exists():
    print("no %s (generate the story first: zsh story.sh gen %s)" % (lines_file, story)); raise SystemExit(2)
lines = json.load(open(lines_file, encoding="utf-8"))
her = "Warm, dry, a little tired. Talking to someone she loves who is being ridiculous. Not performing."
him = "Grave, sincere, deadpan. A man narrating his own epic romance with total conviction."
if story:
    s = json.load(open(folder / "story.json", encoding="utf-8"))
    her = s.get("her_voice") or her
    him = s.get("his_voice") or him
voices = {"JENNIFER": ("nova", her),
          "FERNANDO": ("fable", him),
          "SAT-NAV": ("onyx", "Flat, robotic, indifferent car navigation voice."),
          "KIDS": ("shimmer", "A small child, about seven years old, out of breath and delighted, shouting to their dad across a forest."),
          "REVEREND": ("ash", "Enormous and thunderous, a revival preacher bellowing down at a crowd from a great height. Utterly delighted with himself, always on the edge of laughing.")}
sub = "" if lang == "en" else lang  # voice/<code>/ for every language but English
out = (folder / "voice" / sub) if story else (pathlib.Path("assets/voice") / sub)
out.mkdir(parents=True, exist_ok=True)
n = 0; fail = 0
for k, v in lines.items():
    p = out / f"{k}.mp3"
    if p.exists():
        continue
    voice, instr = voices[v["who"]]
    names = {"pt": "Brazilian Portuguese", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "nl": "Dutch", "ja": "Japanese",
             "ko": "Korean", "zh": "Mandarin Chinese", "ru": "Russian", "tr": "Turkish", "ar": "Arabic", "hi": "Hindi", "sv": "Swedish",
             "da": "Danish", "no": "Norwegian", "fi": "Finnish", "pl": "Polish", "el": "Greek", "he": "Hebrew", "id": "Indonesian",
             "th": "Thai", "vi": "Vietnamese", "mi": "te reo Māori"}
    if lang != "en":
        instr += " Speak %s, natural and unhurried." % names.get(lang, "the language of the text")
    body = json.dumps({"model": "gpt-4o-mini-tts", "voice": voice, "input": v["text"], "instructions": instr, "response_format": "mp3"}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/audio/speech", data=body,
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        p.write_bytes(urllib.request.urlopen(req, timeout=120).read()); n += 1; print("ok", k)
    except Exception as e:
        fail += 1; print("FAIL", k, e)
print(f"voices ({lang}{', story ' + story if story else ''}): {n} generated, {fail} failed, {len(lines)} total lines -> {out}")
PY
