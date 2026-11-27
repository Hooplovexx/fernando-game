#!/usr/bin/env python3
"""A story becomes a game: stories/<slug>/story.json (the form) -> the game's word files, written by GPT-6 Astra, in English and
the story's own language (any language: "language": "es", "fr", "ja", "pt", ...).

  ../../.venv/bin/python gen_story.py <slug> --dry-run   shows what would be sent and the estimated cost; sends nothing
  ../../.venv/bin/python gen_story.py <slug>             writes stories/<slug>/content.json, content_<lang>.json, voice_lines.json,
                                                          voice_lines_<lang>.json, strings_en.json, strings_<lang>.json
  ../../.venv/bin/python gen_story.py <slug> --check     validates the generated files against the game's schema

The schema is Fernando's own files (the same keys, the same array lengths, the same voice keys), plus the adaptive section:
her replies for the path the player is on (talker, romantic, father) and an ending per path (plus driver). The game swaps
Fernando's and Jennifer's names for the story's everywhere, so the generator writes the story's names directly.
For a language other than Portuguese, strings_<lang>.json must carry every screen string (the game has no built-in table for it).
Cost: about $0.60 to $1.20 per story at $10 in / $50 out per million tokens. Key from OPENAI_API_KEY or ../../.env.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORIES = os.environ.get("STORIES_DIR") or os.path.join(HERE, "stories")  # the story factory on GitHub Actions points this at the site's stories/
ENV = os.path.join(HERE, "..", "..", ".env")
MODEL = "gpt-6-astra"
PRICE_IN, PRICE_OUT = 10.0, 50.0
PROFILES = ("talker", "romantic", "father")
ADAPTIVE_LINES = 3  # her replies per profile
LANG_NAMES = {"pt": "Brazilian Portuguese", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "nl": "Dutch", "ja": "Japanese",
              "ko": "Korean", "zh": "Chinese", "ru": "Russian", "tr": "Turkish", "ar": "Arabic", "hi": "Hindi", "sv": "Swedish", "da": "Danish",
              "no": "Norwegian", "fi": "Finnish", "pl": "Polish", "el": "Greek", "he": "Hebrew", "id": "Indonesian", "th": "Thai", "vi": "Vietnamese",
              "mi": "te reo Māori", "en": "English"}


def lang_name(code):
    return LANG_NAMES.get(code, "the language with code '%s'" % code)


def read_json(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def load_key():
    if os.environ.get("OPENAI_API_KEY"):
        return
    if os.path.exists(ENV):
        for line in open(ENV, encoding="utf-8"):
            line = line.strip()
            if line.startswith("OPENAI_API_KEY=") and not line.startswith("#"):
                os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not os.environ.get("OPENAI_API_KEY"):
        print("gen_story: no OPENAI_API_KEY in the environment or ../../.env")
        sys.exit(1)


def expected_voice_keys(base):
    keys = list(base.keys())
    for p in PROFILES:
        for i in range(1, ADAPTIVE_LINES + 1):
            keys.append("jen_adaptive_%s_%d" % (p, i))
    return keys


def validate(out, base_content, base_voice, strings_keys, L):
    """Every file the game reads must have the shape the game expects. Returns a list of problems."""
    errs = []
    for lang in ("en", L):
        c = out.get("content_" + lang)
        if not isinstance(c, dict):
            errs.append("content_%s missing" % lang); continue
        for k, v in base_content.items():
            if k not in c:
                errs.append("content_%s: key %s missing" % (lang, k))
            elif isinstance(v, list) and (not isinstance(c[k], list) or len(c[k]) != len(v)):
                errs.append("content_%s: %s must have %d entries, has %s" % (lang, k, len(v), len(c[k]) if isinstance(c[k], list) else "none"))
            elif isinstance(v, str) and not isinstance(c[k], str):
                errs.append("content_%s: %s must be a string" % (lang, k))
        ad = c.get("adaptive")
        if not isinstance(ad, dict):
            errs.append("content_%s: adaptive section missing" % lang)
        else:
            profs = ad.get("profiles", {})
            for p in PROFILES:
                if not isinstance(profs.get(p), list) or len(profs.get(p)) != ADAPTIVE_LINES:
                    errs.append("content_%s: adaptive.profiles.%s must have %d lines" % (lang, p, ADAPTIVE_LINES))
            ends = ad.get("endings", {})
            for p in PROFILES + ("driver",):
                if not isinstance(ends.get(p), str) or not ends.get(p):
                    errs.append("content_%s: adaptive.endings.%s missing" % (lang, p))
        vl = out.get("voice_lines_" + lang)
        if not isinstance(vl, dict):
            errs.append("voice_lines_%s missing" % lang); continue
        want = expected_voice_keys(base_voice)
        for k in want:
            if k not in vl:
                errs.append("voice_lines_%s: key %s missing" % (lang, k))
            else:
                v = vl[k]
                if not isinstance(v, dict) or v.get("who") not in ("JENNIFER", "FERNANDO", "SAT-NAV", "KIDS", "REVEREND") or not isinstance(v.get("text"), str) or not v["text"].strip():
                    errs.append("voice_lines_%s: %s needs who (JENNIFER/FERNANDO/SAT-NAV/KIDS/REVEREND) and text" % (lang, k))
        for k in vl:
            if k not in want:
                errs.append("voice_lines_%s: unexpected key %s" % (lang, k))
        if isinstance(c, dict) and isinstance(c.get("adaptive"), dict) and isinstance(vl, dict):
            for p in PROFILES:
                for i, line in enumerate(c["adaptive"].get("profiles", {}).get(p, [])[:ADAPTIVE_LINES]):
                    k = "jen_adaptive_%s_%d" % (p, i + 1)
                    if k in vl and vl[k].get("text") != line:
                        errs.append("voice_lines_%s: %s must equal content.adaptive.profiles.%s[%d]" % (lang, k, p, i))
    for lang in ("en", L):
        s = out.get("strings_" + lang)
        if not isinstance(s, dict):
            errs.append("strings_%s missing (an empty object is fine for English)" % lang)
        else:
            for k, v in s.items():
                if not isinstance(v, str):
                    errs.append("strings_%s: %s must be a string" % (lang, k))
            if lang != "en" and lang != "pt":  # no built-in table for this language: every screen string must be here
                missing = [k for k in strings_keys if k not in s]
                if missing:
                    errs.append("strings_%s: %d screen strings missing (the game has no built-in %s), e.g. %s" % (lang, len(missing), lang_name(lang), ", ".join(missing[:4])))
    return errs


def build_prompt(story, base_content, base_content_pt, base_voice, base_voice_pt, strings_pt, L):
    keys = expected_voice_keys(base_voice)
    roles = "JENNIFER = the one who waits (%s), FERNANDO = the one who must drive (%s), SAT-NAV = the car's flat robotic voice, KIDS = the imagined children (%s), REVEREND = an enormous preacher on the road who opens the game bellowing that it is time to get married, and laughing" % (
        story.get("her"), story.get("him"), ", ".join(story.get("kids", [])))
    lname = lang_name(L)
    strings_rule = ('"strings_en", "strings_%s": screen-word overrides keyed by the game\'s English screen strings. ' % L +
                    ('For "%s" (%s) EVERY key in the list below must be present, translated; ' % (L, lname) if L not in ("en", "pt") else "") +
                    'override at least, in both: "THE CAVE", "THE FOREST", "THE VALLEY" (their real place names, upper case), "FOLLOW JENNIFER", '
                    '"FOLLOW JENNIFER TO THE MAILBOX", "WALK UP TO JENNIFER ON THE PORCH", "JENNIFER\'S HOUSE", "FERNANDO\'S FLAT", "THE PORCH", '
                    '"THE HOUSE (NOBODY LIVES HERE)", "GET MARRIED (GEOLOGICAL)", "F + J" (their initials), and the absurdity ladder names when the '
                    'story\'s absurd bits differ: "THE HOUSE (ASSEMBLED, UNASKED)", "JENNIFER (COLOSSAL, ON THE PHONE)", "THE RING (FROM THE SKY)", '
                    '"BRUNO (DOES NOT EXIST)", "SHE SHAKES HER HEAD", "THE POEM (11 PAGES)", "HE CLIMBS A PYLON (SHIRT: OFF)", "THE CAKE (3 TIERS, NO WEDDING)", '
                    '"A CHOIR (IMAGINED, IN TUNE)", "HER STAR (DELIVERED)", "PETALS", "THE CROWN (LOAD-BEARING)". Keep overrides short, UPPER CASE where the key is.')
    system = f"""You write every word of a short surreal romantic comedy game, from one couple's real story. The game (FERNANDO: 40 MINUTES AWAY)
plays like this: she texts a simple question; he answers with an enormous romantic future instead of driving the {story.get('minutes', 40)} minutes
to her; the sat-nav keeps repeating the objective (drive to her house); every stall sends him into a memory (three places) and makes the
world more absurd; either he finally drives (the car flies the last stretch and lands at her door, she gets in, it lifts off) or the
song's last line ends it. Deadpan, dry, warm, plain speech; jokes come from sincerity meeting logistics. Never cruel.

Roles (keep these exact role names in "who"): {roles}.
Write the STORY'S names in the text itself ({story.get('him')}, {story.get('her')}, the children), never Fernando or Jennifer.
Two languages: English, and {lname} (code "{L}"), both natural, not word-for-word translations of each other. Use the couple's real
places, her real lines, his real excuses, the inside jokes and the absurd bits from the form; that is the point.

Output ONE JSON object with exactly these members:
  "content_en", "content_{L}": the same keys and array lengths as the example content (opening, fantasy[{len(base_content['fantasy'])}], declarations[{len(base_content['declarations'])}],
      declaration_replies[{len(base_content['declaration_replies'])}], love_replies[{len(base_content['love_replies'])}], children[{len(base_content['children'])}], quests[{len(base_content['quests'])}], eta_lines[{len(base_content['eta_lines'])}]) PLUS
      "adaptive": {{"profiles": {{"talker": [3 lines], "romantic": [3 lines], "father": [3 lines]}}, "endings": {{"talker": "...", "romantic": "...", "father": "...", "driver": "..."}}}}
      profiles = her replies to a man who keeps declaring / keeps asking if she still loves him / keeps imagining children; endings = the
      end card text for each path, driver = he drove and arrived. The last declaration_reply must be the line that ends the game when he never drives.
  "voice_lines_en", "voice_lines_{L}": objects with EXACTLY these keys, each {{"who": ROLE, "text": "..."}}: {", ".join(keys)}.
      Keys starting jen_adaptive_<profile>_<n> carry the SAME text as content.adaptive.profiles.<profile>[n-1]. fer_place_cave/bush/valley describe
      the couple's REAL three places (the form's memories) as the cave, the forest and the valley of the game. jos_* lines are hers in those places.
  {strings_rule}
No markdown, no commentary: the JSON object only."""
    user = "THE STORY (the form):\n" + json.dumps(story, ensure_ascii=False, indent=1) + \
        "\n\nEXAMPLE content (English, Fernando's; match its shape):\n" + json.dumps(base_content, ensure_ascii=False) + \
        "\n\nEXAMPLE content in Portuguese (shape and tone of a second language):\n" + json.dumps(base_content_pt, ensure_ascii=False) + \
        "\n\nEXAMPLE voice_lines (English; match the keys and the who):\n" + json.dumps(base_voice, ensure_ascii=False) + \
        "\n\nEXAMPLE voice_lines in Portuguese:\n" + json.dumps(base_voice_pt, ensure_ascii=False) + \
        "\n\nThe game's English screen strings (keys) with their Portuguese, as an example of the strings file:\n" + json.dumps(strings_pt, ensure_ascii=False)
    return system, user


def parse(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[a:b + 1])


def files_for(L):
    return (("content.json", "content_en"), ("content_%s.json" % L, "content_" + L), ("voice_lines.json", "voice_lines_en"),
            ("voice_lines_%s.json" % L, "voice_lines_" + L), ("strings_en.json", "strings_en"), ("strings_%s.json" % L, "strings_" + L))


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(2)
    slug = args[0]
    folder = os.path.join(STORIES, slug)
    sp = os.path.join(folder, "story.json")
    if not os.path.exists(sp):
        print("gen_story: no stories/%s/story.json (zsh story.sh new %s)" % (slug, slug)); sys.exit(2)
    story = json.load(open(sp, encoding="utf-8"))
    L = str(story.get("language", "pt")).strip().lower() or "pt"
    if L == "en":  # an English-only story still gets Portuguese as its second language; the game starts in English
        L = "pt"
    base_content, base_content_pt = read_json("content.json"), read_json("content_pt.json")
    base_voice, base_voice_pt = read_json("voice_lines.json"), read_json("voice_lines_pt.json")
    strings_pt = read_json("strings_pt.json")
    if "--check" in args:
        out = {}
        for name, key in files_for(L):
            p = os.path.join(folder, name)
            if os.path.exists(p):
                out[key] = json.load(open(p, encoding="utf-8"))
        errs = validate(out, base_content, base_voice, list(strings_pt.keys()), L)
        if errs:
            print("story %s (%s): %d problem(s)" % (slug, lang_name(L), len(errs)))
            for e in errs[:30]:
                print("   " + e)
            sys.exit(1)
        n_voice = sum(1 for f in os.listdir(os.path.join(folder, "voice")) if f.endswith(".mp3")) if os.path.isdir(os.path.join(folder, "voice")) else 0
        print("story %s ok (%s): files complete; %d English voice files (zsh story.sh voices %s makes them)" % (slug, lang_name(L), n_voice, slug))
        return
    empty = [k for k in ("him", "her", "premise") if not str(story.get(k, "")).strip() or str(story.get(k, "")).startswith("<")]
    if empty:
        print("gen_story: fill in the form first: %s in stories/%s/story.json" % (", ".join(empty), slug)); sys.exit(2)
    system, user = build_prompt(story, base_content, base_content_pt, base_voice, base_voice_pt, strings_pt, L)
    est_in = (len(system) + len(user)) // 4
    est_out = 16000 if L != "pt" else 14000
    est = (est_in * PRICE_IN + est_out * PRICE_OUT) / 1e6
    if "--dry-run" in args:
        print("gen_story dry run for %s in English and %s: about %d input tokens, up to %d output tokens, about $%.2f. Nothing sent." % (slug, lang_name(L), est_in, est_out, est))
        print("the form as it will be sent:")
        print(json.dumps(story, ensure_ascii=False, indent=1))
        return
    load_key()
    from openai import OpenAI
    c = OpenAI()
    t0 = time.time()
    print("gen_story: asking %s for %s in English and %s (about $%.2f)..." % (MODEL, slug, lang_name(L), est))
    r = c.chat.completions.create(model=MODEL, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                                  max_completion_tokens=28000, reasoning_effort="medium")
    u = r.usage
    cost = (u.prompt_tokens * PRICE_IN + u.completion_tokens * PRICE_OUT) / 1e6
    print("gen_story: answered in %.0f s, %d in / %d out, $%.2f, response id %s" % (time.time() - t0, u.prompt_tokens, u.completion_tokens, cost, getattr(r, "id", "?")))
    text = r.choices[0].message.content or ""
    open(os.path.join(folder, "generated_raw.json"), "w", encoding="utf-8").write(text)
    out = parse(text)
    errs = validate(out, base_content, base_voice, list(strings_pt.keys()), L)
    if errs:
        print("gen_story: the answer does not fit the game (%d problems); nothing written except generated_raw.json" % len(errs))
        for e in errs[:30]:
            print("   " + e)
        sys.exit(1)
    for name, key in files_for(L):
        json.dump(out[key], open(os.path.join(folder, name), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("gen_story: wrote stories/%s/ content, content_%s, voice_lines, voice_lines_%s, strings_en, strings_%s (.json)" % (slug, L, L, L))
    print("next:  zsh story.sh voices %s   (about $1.50 of voices)   then   zsh loop.sh --deploy" % slug)


if __name__ == "__main__":
    main()
