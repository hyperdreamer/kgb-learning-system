"""Sentence-based unfamiliar-item validation.

Matching is Unicode-safe, case-insensitive, and whitespace-normalized.
Primary path is literal substring match. When that fails, a flexible
token-sequence path accepts common English inflections (tense / number)
so a lemma like ``insist on`` matches surface forms such as ``insists on``.

All matching treats item text as literal content (regex metacharacters are
escaped on the literal path). Local matching uses no network; optional AI
fallback lives outside this module and only runs when local matching fails.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


def normalize_sentence(text: str) -> str:
    """Normalize text for searching: NFC, casefold, collapse whitespace, strip.

    Used on both the sentence and each unfamiliar item before matching.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Escape special regex metacharacters so item text is treated literally.
_RE_ESCAPE_RE = re.compile(r"([.^$*+?{}[\]\\|()])")

# Leading/trailing punctuation stripped from tokens for flex matching.
_PUNCT_STRIP = ".,!?;:\"'“”‘’()[]{}…—–-«»"

# Comprehensive irregular English verb / modal forms → shared lemma family.
# Regular -s/-ed/-ing still handled by the stemmer; this map covers forms
# that suffix rules cannot link (go/went/gone, choose/chose/chosen, ...).
# Overlapping spellings that would false-merge distinct lemmas are avoided
# where practical (e.g. recline-lie omits past "lay" to keep lay/laid separate).
_IRREGULAR_VERB_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"abide", "abided", "abides", "abiding", "abode"}),
    frozenset({"am", "are", "be", "been", "being", "is", "was", "were"}),
    frozenset({"ate", "eat", "eaten", "eating", "eats"}),
    frozenset({"bear", "bearing", "bears", "bore", "born", "borne", "forbear", "forbearing", "forbears", "forbore", "forborne"}),
    frozenset({"beat", "beaten", "beating", "beats"}),
    frozenset({"began", "begin", "beginning", "begins", "begun"}),
    frozenset({"beget", "begets", "begetting", "begot", "begotten"}),
    frozenset({"bend", "bending", "bends", "bent"}),
    frozenset({"bereave", "bereaved", "bereaves", "bereaving", "bereft"}),
    frozenset({"bet", "bets", "betting"}),
    frozenset({"bade", "bid", "bidden", "bidding", "bids", "forbade", "forbid", "forbidden", "forbidding", "forbids", "outbid", "outbidding", "outbids", "rebid", "rebidding", "rebids", "underbid", "underbidding", "underbids"}),
    frozenset({"bind", "binding", "binds", "bound", "unbind", "unbinding", "unbinds", "unbound"}),
    frozenset({"bit", "bite", "bites", "biting", "bitten"}),
    frozenset({"bled", "bleed", "bleeding", "bleeds"}),
    frozenset({"blend", "blended", "blending", "blends", "blent"}),
    frozenset({"blew", "blow", "blowing", "blown", "blows"}),
    frozenset({"break", "breaking", "breaks", "broke", "broken"}),
    frozenset({"bred", "breed", "breeding", "breeds"}),
    frozenset({"bring", "bringing", "brings", "brought"}),
    frozenset({"build", "building", "builds", "built", "rebuild", "rebuilding", "rebuilds", "rebuilt"}),
    frozenset({"burn", "burned", "burning", "burns", "burnt"}),
    frozenset({"burst", "bursting", "bursts"}),
    frozenset({"bust", "busted", "busting", "busts"}),
    frozenset({"bought", "buy", "buying", "buys"}),
    frozenset({"became", "become", "becomes", "becoming", "came", "come", "comes", "coming", "overcame", "overcome", "overcomes", "overcoming"}),
    frozenset({"can", "could"}),
    frozenset({"broadcast", "broadcasting", "broadcasts", "cast", "casting", "casts", "forecast", "forecasting", "forecasts", "telecast", "telecasting", "telecasts"}),
    frozenset({"catch", "catches", "catching", "caught"}),
    frozenset({"chid", "chidden", "chide", "chided", "chides", "chiding"}),
    frozenset({"choose", "chooses", "choosing", "chose", "chosen"}),
    frozenset({"clad", "clothe", "clothed", "clothes", "clothing"}),
    frozenset({"cleave", "cleaved", "cleaves", "cleaving", "cleft", "clove", "cloven"}),
    frozenset({"cling", "clinging", "clings", "clung"}),
    frozenset({"cost", "costing", "costs"}),
    frozenset({"creep", "creeping", "creeps", "crept"}),
    frozenset({"cut", "cuts", "cutting", "undercut", "undercuts", "undercutting"}),
    frozenset({"deal", "dealing", "deals", "dealt"}),
    frozenset({"dig", "digging", "digs", "dug"}),
    frozenset({"dive", "dived", "dives", "diving", "dove"}),
    frozenset({"did", "do", "does", "doing", "done", "outdid", "outdo", "outdoes", "outdoing", "outdone", "overdid", "overdo", "overdoes", "overdoing", "overdone", "redid", "redo", "redoes", "redoing", "redone", "undid", "undo", "undoes", "undoing", "undone"}),
    frozenset({"drank", "drink", "drinking", "drinks", "drunk"}),
    frozenset({"draw", "drawing", "drawn", "draws", "drew", "overdraw", "overdrawing", "overdrawn", "overdraws", "overdrew", "withdraw", "withdrawing", "withdrawn", "withdraws", "withdrew"}),
    frozenset({"dream", "dreamed", "dreaming", "dreams", "dreamt"}),
    frozenset({"drive", "driven", "drives", "driving", "drove"}),
    frozenset({"dwell", "dwelled", "dwelling", "dwells", "dwelt"}),
    frozenset({"befall", "befallen", "befalling", "befalls", "befell", "fall", "fallen", "falling", "falls", "fell"}),
    frozenset({"fed", "feed", "feeding", "feeds"}),
    frozenset({"feel", "feeling", "feels", "felt"}),
    frozenset({"fight", "fighting", "fights", "fought", "outfight", "outfighting", "outfights", "outfought"}),
    frozenset({"find", "finding", "finds", "found"}),
    frozenset({"fit", "fits", "fitted", "fitting"}),
    frozenset({"fled", "flee", "fleeing", "flees"}),
    frozenset({"fling", "flinging", "flings", "flung"}),
    frozenset({"flew", "flies", "flown", "fly", "flying"}),
    frozenset({"forgave", "forgive", "forgiven", "forgives", "forgiving"}),
    frozenset({"forget", "forgets", "forgetting", "forgot", "forgotten"}),
    frozenset({"freeze", "freezes", "freezing", "froze", "frozen"}),
    frozenset({"gave", "give", "given", "gives", "giving"}),
    frozenset({"get", "gets", "getting", "got", "gotten"}),
    frozenset({"gild", "gilded", "gilding", "gilds", "gilt"}),
    frozenset({"gird", "girded", "girding", "girds", "girt"}),
    frozenset({"forego", "foregoes", "foregoing", "foregone", "forewent", "forgo", "forgoes", "forgoing", "forgone", "forwent", "go", "goes", "going", "gone", "undergo", "undergoes", "undergoing", "undergone", "underwent", "went"}),
    frozenset({"grew", "grow", "growing", "grown", "grows", "outgrew", "outgrow", "outgrowing", "outgrown", "outgrows"}),
    frozenset({"grind", "grinding", "grinds", "ground"}),
    frozenset({"had", "has", "have", "having"}),
    frozenset({"hang", "hanged", "hanging", "hangs", "hung"}),
    frozenset({"hear", "heard", "hearing", "hears", "overhear", "overheard", "overhearing", "overhears"}),
    frozenset({"beheld", "behold", "beholding", "beholds", "held", "hold", "holding", "holds", "upheld", "uphold", "upholding", "upholds", "withheld", "withhold", "withholding", "withholds"}),
    frozenset({"hew", "hewed", "hewing", "hewn", "hews"}),
    frozenset({"hid", "hidden", "hide", "hides", "hiding"}),
    frozenset({"hit", "hits", "hitting"}),
    frozenset({"heave", "heaved", "heaves", "heaving", "hove"}),
    frozenset({"hurt", "hurting", "hurts"}),
    frozenset({"keep", "keeping", "keeps", "kept"}),
    frozenset({"kneel", "kneeled", "kneeling", "kneels", "knelt"}),
    frozenset({"knew", "know", "knowing", "known", "knows"}),
    frozenset({"knit", "knits", "knitted", "knitting"}),
    frozenset({"lade", "laded", "laden", "lades", "lading", "load", "loaded", "loading", "loads"}),
    frozenset({"inlaid", "inlay", "inlaying", "inlays", "laid", "lay", "laying", "lays", "mislaid", "mislay", "mislaying", "mislays", "waylaid", "waylay", "waylaying", "waylays"}),
    frozenset({"lean", "leaned", "leaning", "leans", "leant"}),
    frozenset({"leap", "leaped", "leaping", "leaps", "leapt"}),
    frozenset({"learn", "learned", "learning", "learns", "learnt"}),
    frozenset({"lead", "leading", "leads", "led", "mislead", "misleading", "misleads", "misled"}),
    frozenset({"leave", "leaves", "leaving", "left"}),
    frozenset({"lend", "lending", "lends", "lent"}),
    frozenset({"let", "lets", "letting", "sublet", "sublets", "subletting"}),
    frozenset({"lain", "lie", "lies", "lying"}),
    frozenset({"alight", "alighted", "alighting", "alights", "alit", "light", "lighted", "lighting", "lights", "lit", "relight", "relighted", "relighting", "relights", "relit"}),
    frozenset({"lose", "loses", "losing", "lost"}),
    frozenset({"made", "make", "makes", "making", "remade", "remake", "remakes", "remaking"}),
    frozenset({"may", "might"}),
    frozenset({"mean", "meaning", "means", "meant"}),
    frozenset({"melt", "melted", "melting", "melts", "molten"}),
    frozenset({"meet", "meeting", "meets", "met"}),
    frozenset({"mow", "mowed", "mowing", "mown", "mows"}),
    frozenset({"must"}),
    frozenset({"paid", "pay", "paying", "pays", "repaid", "repay", "repaying", "repays"}),
    frozenset({"pen", "penned", "penning", "pens", "pent"}),
    frozenset({"plead", "pleaded", "pleading", "pleads", "pled"}),
    frozenset({"prove", "proved", "proven", "proves", "proving"}),
    frozenset({"input", "inputs", "inputting", "output", "outputs", "outputting", "put", "puts", "putting"}),
    frozenset({"quit", "quits", "quitting"}),
    frozenset({"ran", "run", "running", "runs"}),
    frozenset({"rang", "ring", "ringing", "rings", "rung"}),
    frozenset({"read", "reading", "reads"}),
    frozenset({"rend", "rending", "rends", "rent"}),
    frozenset({"rid", "ridding", "rids"}),
    frozenset({"overridden", "override", "overrides", "overriding", "overrode", "ridden", "ride", "rides", "riding", "rode"}),
    frozenset({"arise", "arisen", "arises", "arising", "arose", "rise", "risen", "rises", "rising", "rose"}),
    frozenset({"sang", "sing", "singing", "sings", "sung"}),
    frozenset({"sank", "sink", "sinking", "sinks", "sunk"}),
    frozenset({"sat", "sit", "sits", "sitting"}),
    frozenset({"foresaw", "foresee", "foreseeing", "foreseen", "foresees", "oversaw", "oversee", "overseeing", "overseen", "oversees", "saw", "see", "seeing", "seen", "sees"}),
    frozenset({"said", "say", "saying", "says", "unsaid", "unsay", "unsaying", "unsays"}),
    frozenset({"beseech", "beseeched", "beseeches", "beseeching", "besought", "seek", "seeking", "seeks", "sought"}),
    frozenset({"resell", "reselling", "resells", "resold", "sell", "selling", "sells", "sold"}),
    frozenset({"send", "sending", "sends", "sent"}),
    frozenset({"inset", "insets", "insetting", "offset", "offsets", "offsetting", "reset", "resets", "resetting", "set", "sets", "setting", "typeset", "typesets", "typesetting", "upset", "upsets", "upsetting"}),
    frozenset({"sew", "sewed", "sewing", "sewn", "sews"}),
    frozenset({"shake", "shaken", "shakes", "shaking", "shook"}),
    frozenset({"shall", "should"}),
    frozenset({"shave", "shaved", "shaven", "shaves", "shaving"}),
    frozenset({"shear", "sheared", "shearing", "shears", "shore", "shorn"}),
    frozenset({"shed", "shedding", "sheds"}),
    frozenset({"shine", "shined", "shines", "shining", "shone"}),
    frozenset({"shoot", "shooting", "shoots", "shot"}),
    frozenset({"show", "showed", "showing", "shown", "shows"}),
    frozenset({"shrank", "shrink", "shrinking", "shrinks", "shrunk"}),
    frozenset({"shut", "shuts", "shutting"}),
    frozenset({"slain", "slay", "slaying", "slays", "slew"}),
    frozenset({"sleep", "sleeping", "sleeps", "slept"}),
    frozenset({"slid", "slide", "slides", "sliding"}),
    frozenset({"sling", "slinging", "slings", "slung"}),
    frozenset({"slit", "slits", "slitting"}),
    frozenset({"smell", "smelled", "smelling", "smells", "smelt"}),
    frozenset({"smite", "smites", "smiting", "smitten", "smote"}),
    frozenset({"sneak", "sneaked", "sneaking", "sneaks", "snuck"}),
    frozenset({"sow", "sowed", "sowing", "sown", "sows"}),
    frozenset({"spat", "spit", "spits", "spitting"}),
    frozenset({"bespeak", "bespeaking", "bespeaks", "bespoke", "bespoken", "speak", "speaking", "speaks", "spoke", "spoken"}),
    frozenset({"sped", "speed", "speeded", "speeding", "speeds"}),
    frozenset({"spell", "spelled", "spelling", "spells", "spelt"}),
    frozenset({"spend", "spending", "spends", "spent"}),
    frozenset({"spill", "spilled", "spilling", "spills", "spilt"}),
    frozenset({"spin", "spinning", "spins", "spun"}),
    frozenset({"split", "splits", "splitting"}),
    frozenset({"spoil", "spoiled", "spoiling", "spoils", "spoilt"}),
    frozenset({"sprang", "spring", "springing", "springs", "sprung"}),
    frozenset({"spread", "spreading", "spreads"}),
    frozenset({"misunderstand", "misunderstanding", "misunderstands", "misunderstood", "stand", "standing", "stands", "stood", "understand", "understanding", "understands", "understood", "withstand", "withstanding", "withstands", "withstood"}),
    frozenset({"stank", "stink", "stinking", "stinks", "stunk"}),
    frozenset({"steal", "stealing", "steals", "stole", "stolen"}),
    frozenset({"stick", "sticking", "sticks", "stuck"}),
    frozenset({"sting", "stinging", "stings", "stung"}),
    frozenset({"strew", "strewed", "strewing", "strewn", "strews"}),
    frozenset({"stridden", "stride", "strides", "striding", "strode"}),
    frozenset({"stricken", "strike", "strikes", "striking", "struck"}),
    frozenset({"string", "stringing", "strings", "strung"}),
    frozenset({"strive", "strived", "striven", "strives", "striving", "strove"}),
    frozenset({"swam", "swim", "swimming", "swims", "swum"}),
    frozenset({"forswear", "forswearing", "forswears", "forswore", "forsworn", "swear", "swearing", "swears", "swore", "sworn"}),
    frozenset({"sweat", "sweated", "sweating", "sweats"}),
    frozenset({"sweep", "sweeping", "sweeps", "swept"}),
    frozenset({"swell", "swelled", "swelling", "swells", "swollen"}),
    frozenset({"swing", "swinging", "swings", "swung"}),
    frozenset({"forsake", "forsaken", "forsakes", "forsaking", "forsook", "mistake", "mistaken", "mistakes", "mistaking", "mistook", "overtake", "overtaken", "overtakes", "overtaking", "overtook", "partake", "partaken", "partakes", "partaking", "partook", "retake", "retaken", "retakes", "retaking", "retook", "take", "taken", "takes", "taking", "took", "undertake", "undertaken", "undertakes", "undertaking", "undertook"}),
    frozenset({"taught", "teach", "teaches", "teaching"}),
    frozenset({"tear", "tearing", "tears", "tore", "torn"}),
    frozenset({"foretell", "foretelling", "foretells", "foretold", "retell", "retelling", "retells", "retold", "tell", "telling", "tells", "told"}),
    frozenset({"rethink", "rethinking", "rethinks", "rethought", "think", "thinking", "thinks", "thought"}),
    frozenset({"threw", "throw", "throwing", "thrown", "throws"}),
    frozenset({"thrive", "thrived", "thriven", "thrives", "thriving", "throve"}),
    frozenset({"thrust", "thrusting", "thrusts"}),
    frozenset({"tread", "treading", "treads", "trod", "trodden"}),
    frozenset({"vex", "vexed", "vexes", "vexing", "vext"}),
    frozenset({"awake", "awakes", "awaking", "awoke", "awoken", "wake", "wakes", "waking", "woke", "woken"}),
    frozenset({"outwear", "outwearing", "outwears", "outwore", "outworn", "wear", "wearing", "wears", "wore", "worn"}),
    frozenset({"wed", "wedded", "wedding", "weds"}),
    frozenset({"weep", "weeping", "weeps", "wept"}),
    frozenset({"wet", "wets", "wetted", "wetting"}),
    frozenset({"will", "would"}),
    frozenset({"win", "winning", "wins", "won"}),
    frozenset({"rewind", "rewinding", "rewinds", "rewound", "unwind", "unwinding", "unwinds", "unwound", "wind", "winding", "winds", "wound"}),
    frozenset({"work", "worked", "working", "works", "wreak", "wreaked", "wreaking", "wreaks", "wrought"}),
    frozenset({"weave", "weaves", "weaving", "wove", "woven"}),
    frozenset({"wring", "wringing", "wrings", "wrung"}),
    frozenset({"rewrite", "rewrites", "rewriting", "rewritten", "rewrote", "underwrite", "underwrites", "underwriting", "underwritten", "underwrote", "write", "writes", "writing", "written", "wrote"}),
)

_IRREGULAR_LOOKUP: dict[str, frozenset[str]] = {
    form: group for group in _IRREGULAR_VERB_GROUPS for form in group
}


def _escape_regex(text: str) -> str:
    """Escape regex metacharacters in *text*."""
    return _RE_ESCAPE_RE.sub(r"\\\1", text)


def _literal_find(pattern: str, haystack: str) -> bool:
    """Check whether normalized *pattern* occurs literally in *haystack*.

    For single-token alphabetic patterns, require whole-token equality so
    short lemmas like ``go`` do not accidentally match inside ``gone`` /
    ``going`` via pure substring. Multi-word / non-alpha patterns keep
    plain substring matching.
    """
    if not pattern:
        return False
    # Single simple alphabetic token → whole-token only (not substring).
    if re.fullmatch(r"[a-z]+", pattern):
        return any(_strip_token_punct(tok) == pattern for tok in haystack.split(" "))
    # Phrases and patterns with punctuation/metacharacters: literal substring.
    escaped = _escape_regex(pattern)
    return bool(re.search(escaped, haystack))


def _strip_token_punct(token: str) -> str:
    return token.strip(_PUNCT_STRIP)


def _stem_candidates(token: str) -> set[str]:
    """Return a small set of stem-like candidates for *token*.

    Enough for common English tense/number variants; not a full stemmer.
    Always includes the token itself. Irregular verb groups (go/went/gone)
    are expanded via a compact lookup. Suffix stripping usually requires a
    base of length ≥ 3; short irregular bases like go/do (from goes/does)
    are allowed at length ≥ 2 for -s/-es only.
    """
    t = _strip_token_punct(token)
    if not t:
        return set()

    cands: set[str] = {t}
    n = len(t)

    # Irregular verb family (go ↔ went ↔ gone, etc.)
    irregular = _IRREGULAR_LOOKUP.get(t)
    if irregular is not None:
        cands.update(irregular)

    def add_base(base: str, min_len: int = 3) -> None:
        if len(base) >= min_len:
            cands.add(base)
            # If the derived base is itself irregular, expand that family too.
            group = _IRREGULAR_LOOKUP.get(base)
            if group is not None:
                cands.update(group)

    # studies / tries → study / try
    if n >= 5 and t.endswith("ies"):
        add_base(t[:-3] + "y")
        add_base(t[:-3])

    # tried / carried → try / carry
    if n >= 5 and t.endswith("ied"):
        add_base(t[:-3] + "y")
        add_base(t[:-3])

    # insisting / running / lying
    if n >= 5 and t.endswith("ing"):
        base = t[:-3]
        add_base(base)
        if len(base) >= 4 and base[-1] == base[-2] and base[-1].isalpha():
            add_base(base[:-1])  # running → run
        if base.endswith("i") and len(base) >= 3:
            add_base(base[:-1] + "y")  # lying → ly / y form

    # insisted / stopped / liked
    if n >= 4 and t.endswith("ed"):
        base = t[:-2]
        add_base(base)
        if len(base) >= 4 and base[-1] == base[-2] and base[-1].isalpha():
            add_base(base[:-1])  # stopped → stop
        if base.endswith("i") and len(base) >= 3:
            add_base(base[:-1] + "y")
        # liked → like (restore silent e)
        add_base(base + "e")

    # goes / does / watches / boxes
    if n >= 4 and t.endswith("es"):
        base = t[:-2]
        # Allow short bases (goes→go, does→do); longer bases keep min 3.
        add_base(base, min_len=2)
        if base.endswith("i"):
            add_base(base[:-1] + "y")

    # insists / cats — single trailing s (not ss)
    if n >= 4 and t.endswith("s") and not t.endswith("ss"):
        add_base(t[:-1], min_len=2)

    # taller / biggest / quickly (light extras)
    if n >= 5 and t.endswith("er"):
        add_base(t[:-2])
    if n >= 6 and t.endswith("est"):
        add_base(t[:-3])
    if n >= 5 and t.endswith("ly"):
        add_base(t[:-2])

    return cands


def _tokens_flex_equal(a: str, b: str) -> bool:
    """True if two tokens are equal or share an inflection stem candidate."""
    sa = _strip_token_punct(a)
    sb = _strip_token_punct(b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    # Direct irregular-family membership (fast path).
    group_a = _IRREGULAR_LOOKUP.get(sa)
    if group_a is not None and sb in group_a:
        return True
    group_b = _IRREGULAR_LOOKUP.get(sb)
    if group_b is not None and sa in group_b:
        return True
    return bool(_stem_candidates(sa) & _stem_candidates(sb))


def _tokenize(text: str) -> list[str]:
    """Whitespace-tokenize a normalized string; drop empty after punct strip."""
    if not text:
        return []
    raw = text.split(" ")
    return [t for t in raw if _strip_token_punct(t)]


def _flexible_phrase_match(norm_item: str, norm_sentence: str) -> bool:
    """Match item as a consecutive token sequence under flex token equality.

    Requires both sides to yield at least one whitespace-separated token so
    continuous CJK strings stay on the literal-only path.
    """
    # Need real whitespace separation for this path to be meaningful.
    if " " not in norm_item and " " not in norm_sentence:
        # Single-token item vs multi-token sentence still allowed.
        if " " not in norm_sentence:
            return False

    item_tokens = _tokenize(norm_item)
    sent_tokens = _tokenize(norm_sentence)
    if not item_tokens or not sent_tokens:
        return False
    if len(item_tokens) > len(sent_tokens):
        return False

    k = len(item_tokens)
    for i in range(0, len(sent_tokens) - k + 1):
        window = sent_tokens[i : i + k]
        if all(_tokens_flex_equal(it, st) for it, st in zip(item_tokens, window)):
            return True
    return False


@dataclass
class ValidationResult:
    valid: bool
    missing: list[str] = field(default_factory=list)


def validate_unfamiliar_items(
    sentence: str,
    unfamiliar_items: list[str],
) -> ValidationResult:
    """Check that every unfamiliar item occurs in *sentence*.

    Matching is:
    - Case-insensitive (casefold)
    - Unicode-normalized (NFC)
    - Whitespace-collapsed
    - Literal substring first (regex metacharacters in items are escaped)
    - Then inflection-tolerant consecutive token match for spaced languages
      (e.g. ``insist on`` ↔ ``insists on``), including irregular verb families
    - Continuous scripts without spaces stay on the literal path

    Local only — no network / AI. Optional AI residual checks belong in the
    UI layer and must re-verify any claimed surface form with
    :func:`surface_form_in_sentence`.

    Returns a ValidationResult with .valid and .missing fields.
    """
    if not unfamiliar_items:
        return ValidationResult(valid=True, missing=[])

    norm_sentence = normalize_sentence(sentence)
    missing: list[str] = []

    for item in unfamiliar_items:
        norm_item = normalize_sentence(item)
        if not norm_item:
            continue
        if _literal_find(norm_item, norm_sentence):
            continue
        if _flexible_phrase_match(norm_item, norm_sentence):
            continue
        missing.append(item)

    return ValidationResult(
        valid=len(missing) == 0,
        missing=missing,
    )


def surface_form_in_sentence(sentence: str, surface: str) -> bool:
    """True if *surface* occurs in *sentence* as a real span.

    Used to verify AI membership claims. Prefers whole-token / phrase
    presence after normalization; does not run the full inflection map
    (the AI already proposed the surface form).
    """
    norm_sentence = normalize_sentence(sentence)
    norm_surface = normalize_sentence(surface)
    if not norm_surface:
        return False
    # Multi-word or punct-bearing: literal substring after escape.
    if " " in norm_surface or not re.fullmatch(r"[a-z]+", norm_surface):
        return _literal_find(norm_surface, norm_sentence)
    # Single alphabetic token: whole-token only.
    return any(
        _strip_token_punct(tok) == norm_surface
        for tok in norm_sentence.split(" ")
    )


def apply_ai_membership_claims(
    sentence: str,
    missing_items: list[str],
    claims: list,
) -> ValidationResult:
    """Reduce *missing_items* using AI claims that pass local surface checks.

    *claims* is a sequence of objects with ``.expression``, ``.found``,
    and ``.surface`` (see ``MembershipClaim``). An item is accepted only when:
    - the claim's expression matches that missing item under normalization,
    - found is true,
    - surface is non-empty and :func:`surface_form_in_sentence` is true.

    AI alone never authorizes acceptance without a verifiable surface span.
    """
    if not missing_items:
        return ValidationResult(valid=True, missing=[])

    # Map normalized expression -> first matching claim
    claim_by_norm: dict[str, object] = {}
    for claim in claims:
        expr = getattr(claim, "expression", "")
        key = normalize_sentence(str(expr))
        if key and key not in claim_by_norm:
            claim_by_norm[key] = claim

    still_missing: list[str] = []
    for item in missing_items:
        key = normalize_sentence(item)
        claim = claim_by_norm.get(key)
        if claim is None:
            still_missing.append(item)
            continue
        found = bool(getattr(claim, "found", False))
        surface = str(getattr(claim, "surface", "") or "").strip()
        if found and surface and surface_form_in_sentence(sentence, surface):
            continue
        still_missing.append(item)

    return ValidationResult(
        valid=len(still_missing) == 0,
        missing=still_missing,
    )


def deduplicate_unfamiliar_items(items: list[str]) -> list[str]:
    """Remove duplicate unfamiliar items, considering normalization.

    Two items that normalize to the same string are considered duplicates.
    The first occurrence is kept.
    """
    seen: set[str] = set()
    result: list[str] = []

    for item in items:
        key = normalize_sentence(item)
        if not key:
            continue
        if key not in seen:
            seen.add(key)
            result.append(item)

    return result
