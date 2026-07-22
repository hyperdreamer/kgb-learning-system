"""Sentence-based unfamiliar-item validation.

Matching is Unicode-safe, case-insensitive, and whitespace-normalized.
Primary path is literal substring match. When that fails, a flexible
token-sequence path accepts common English inflections (tense / number)
so a lemma like ``insist on`` matches surface forms such as ``insists on``.
Hyphen compounds also match: lemma ``staple`` is found inside ``non-staple``.

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
# Hyphen is intentionally NOT stripped here — compounds like non-staple stay
# intact so we can match lemmas against individual hyphen segments.
_PUNCT_STRIP = ".,!?;:\"'“”‘’()[]{}…«»"
# Hyphen-like characters that split English compounds (non-staple, well–known).
_HYPHEN_CHARS = frozenset("-–—")

# Comprehensive irregular English verb / modal forms → shared lemma family.
# Regular -s/-ed/-ing still handled by the stemmer; this map covers forms
# that suffix rules cannot link (go/went/gone, choose/chose/chosen, ...).
# Overlapping spellings that would false-merge distinct lemmas are avoided
# where practical (e.g. recline-lie omits past "lay" to keep lay/laid separate).
_IRREGULAR_VERB_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"abide", "abided", "abides", "abiding", "abode"}),
    frozenset({"am", "are", "be", "been", "being", "is", "was", "were"}),
    frozenset({"ate", "eat", "eaten", "eating", "eats"}),
    frozenset({"bear", "bearing", "bears", "bore", "born", "borne"}),
    frozenset({"forbear", "forbearing", "forbears", "forbore", "forborne"}),
    frozenset({"beat", "beaten", "beating", "beats"}),
    frozenset({"began", "begin", "beginning", "begins", "begun"}),
    frozenset({"beget", "begets", "begetting", "begot", "begotten"}),
    frozenset({"bend", "bending", "bends", "bent"}),
    frozenset({"bereave", "bereaved", "bereaves", "bereaving", "bereft"}),
    frozenset({"bet", "bets", "betting"}),
    frozenset({"bade", "bid", "bidden", "bidding", "bids"}),
    frozenset({"forbade", "forbid", "forbidden", "forbidding", "forbids"}),
    frozenset({"outbid", "outbidding", "outbids"}),
    frozenset({"rebid", "rebidding", "rebids"}),
    frozenset({"underbid", "underbidding", "underbids"}),
    frozenset({"bind", "binding", "binds", "bound"}),
    frozenset({"unbind", "unbinding", "unbinds", "unbound"}),
    frozenset({"bit", "bite", "bites", "biting", "bitten"}),
    frozenset({"bled", "bleed", "bleeding", "bleeds"}),
    frozenset({"blend", "blended", "blending", "blends", "blent"}),
    frozenset({"blew", "blow", "blowing", "blown", "blows"}),
    frozenset({"break", "breaking", "breaks", "broke", "broken"}),
    frozenset({"bred", "breed", "breeding", "breeds"}),
    frozenset({"bring", "bringing", "brings", "brought"}),
    frozenset({"build", "building", "builds", "built"}),
    frozenset({"rebuild", "rebuilding", "rebuilds", "rebuilt"}),
    frozenset({"burn", "burned", "burning", "burns", "burnt"}),
    frozenset({"burst", "bursting", "bursts"}),
    frozenset({"bust", "busted", "busting", "busts"}),
    frozenset({"bought", "buy", "buying", "buys"}),
    frozenset({"became", "become", "becomes", "becoming"}),
    frozenset({"came", "come", "comes", "coming"}),
    frozenset({"overcame", "overcome", "overcomes", "overcoming"}),
    frozenset({"can", "could"}),
    frozenset({"broadcast", "broadcasting", "broadcasts"}),
    frozenset({"cast", "casting", "casts"}),
    frozenset({"forecast", "forecasting", "forecasts"}),
    frozenset({"telecast", "telecasting", "telecasts"}),
    frozenset({"catch", "catches", "catching", "caught"}),
    frozenset({"chid", "chidden", "chide", "chided", "chides", "chiding"}),
    frozenset({"choose", "chooses", "choosing", "chose", "chosen"}),
    frozenset({"clad", "clothe", "clothed", "clothes", "clothing"}),
    frozenset({"cleave", "cleaved", "cleaves", "cleaving", "cleft", "clove", "cloven"}),
    frozenset({"cling", "clinging", "clings", "clung"}),
    frozenset({"cost", "costing", "costs"}),
    frozenset({"creep", "creeping", "creeps", "crept"}),
    frozenset({"cut", "cuts", "cutting"}),
    frozenset({"undercut", "undercuts", "undercutting"}),
    frozenset({"deal", "dealing", "deals", "dealt"}),
    frozenset({"dig", "digging", "digs", "dug"}),
    frozenset({"dive", "dived", "dives", "diving", "dove"}),
    frozenset({"did", "do", "does", "doing", "done"}),
    frozenset({"outdid", "outdo", "outdoes", "outdoing", "outdone"}),
    frozenset({"overdid", "overdo", "overdoes", "overdoing", "overdone"}),
    frozenset({"redid", "redo", "redoes", "redoing", "redone"}),
    frozenset({"undid", "undo", "undoes", "undoing", "undone"}),
    frozenset({"drank", "drink", "drinking", "drinks", "drunk"}),
    frozenset({"draw", "drawing", "drawn", "draws", "drew"}),
    frozenset({"overdraw", "overdrawing", "overdrawn", "overdraws", "overdrew"}),
    frozenset({"withdraw", "withdrawing", "withdrawn", "withdraws", "withdrew"}),
    frozenset({"dream", "dreamed", "dreaming", "dreams", "dreamt"}),
    frozenset({"drive", "driven", "drives", "driving", "drove"}),
    frozenset({"dwell", "dwelled", "dwelling", "dwells", "dwelt"}),
    frozenset({"befall", "befallen", "befalling", "befalls", "befell"}),
    frozenset({"fall", "fallen", "falling", "falls", "fell"}),
    frozenset({"fed", "feed", "feeding", "feeds"}),
    frozenset({"feel", "feeling", "feels", "felt"}),
    frozenset({"fight", "fighting", "fights", "fought"}),
    frozenset({"outfight", "outfighting", "outfights", "outfought"}),
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
    frozenset({"forego", "foregoes", "foregoing", "foregone", "forewent"}),
    frozenset({"forgo", "forgoes", "forgoing", "forgone", "forwent"}),
    frozenset({"go", "goes", "going", "gone", "went"}),
    frozenset({"undergo", "undergoes", "undergoing", "undergone", "underwent"}),
    frozenset({"grew", "grow", "growing", "grown", "grows"}),
    frozenset({"outgrew", "outgrow", "outgrowing", "outgrown", "outgrows"}),
    frozenset({"grind", "grinding", "grinds", "ground"}),
    frozenset({"had", "has", "have", "having"}),
    frozenset({"hang", "hanged", "hanging", "hangs", "hung"}),
    frozenset({"hear", "heard", "hearing", "hears"}),
    frozenset({"overhear", "overheard", "overhearing", "overhears"}),
    frozenset({"beheld", "behold", "beholding", "beholds"}),
    frozenset({"held", "hold", "holding", "holds"}),
    frozenset({"upheld", "uphold", "upholding", "upholds"}),
    frozenset({"withheld", "withhold", "withholding", "withholds"}),
    frozenset({"hew", "hewed", "hewing", "hewn", "hews"}),
    frozenset({"hid", "hidden", "hide", "hides", "hiding"}),
    frozenset({"hit", "hits", "hitting"}),
    frozenset({"heave", "heaved", "heaves", "heaving", "hove"}),
    frozenset({"hurt", "hurting", "hurts"}),
    frozenset({"keep", "keeping", "keeps", "kept"}),
    frozenset({"kneel", "kneeled", "kneeling", "kneels", "knelt"}),
    frozenset({"knew", "know", "knowing", "known", "knows"}),
    frozenset({"knit", "knits", "knitted", "knitting"}),
    frozenset({"lade", "laded", "laden", "lades", "lading"}),
    frozenset({"load", "loaded", "loading", "loads"}),
    frozenset({"inlaid", "inlay", "inlaying", "inlays"}),
    frozenset({"laid", "lay", "laying", "lays"}),
    frozenset({"mislaid", "mislay", "mislaying", "mislays"}),
    frozenset({"waylaid", "waylay", "waylaying", "waylays"}),
    frozenset({"lean", "leaned", "leaning", "leans", "leant"}),
    frozenset({"leap", "leaped", "leaping", "leaps", "leapt"}),
    frozenset({"learn", "learned", "learning", "learns", "learnt"}),
    frozenset({"lead", "leading", "leads", "led"}),
    frozenset({"mislead", "misleading", "misleads", "misled"}),
    frozenset({"leave", "leaves", "leaving", "left"}),
    frozenset({"lend", "lending", "lends", "lent"}),
    frozenset({"let", "lets", "letting"}),
    frozenset({"sublet", "sublets", "subletting"}),
    frozenset({"lain", "lie", "lies", "lying"}),
    frozenset({"alight", "alighted", "alighting", "alights", "alit"}),
    frozenset({"light", "lighted", "lighting", "lights", "lit"}),
    frozenset({"relight", "relighted", "relighting", "relights", "relit"}),
    frozenset({"lose", "loses", "losing", "lost"}),
    frozenset({"made", "make", "makes", "making"}),
    frozenset({"remade", "remake", "remakes", "remaking"}),
    frozenset({"may", "might"}),
    frozenset({"mean", "meaning", "means", "meant"}),
    frozenset({"melt", "melted", "melting", "melts", "molten"}),
    frozenset({"meet", "meeting", "meets", "met"}),
    frozenset({"mow", "mowed", "mowing", "mown", "mows"}),
    frozenset({"must"}),
    frozenset({"paid", "pay", "paying", "pays"}),
    frozenset({"repaid", "repay", "repaying", "repays"}),
    frozenset({"pen", "penned", "penning", "pens", "pent"}),
    frozenset({"plead", "pleaded", "pleading", "pleads", "pled"}),
    frozenset({"prove", "proved", "proven", "proves", "proving"}),
    frozenset({"input", "inputs", "inputting"}),
    frozenset({"output", "outputs", "outputting"}),
    frozenset({"put", "puts", "putting"}),
    frozenset({"quit", "quits", "quitting"}),
    frozenset({"ran", "run", "running", "runs"}),
    frozenset({"rang", "ring", "ringing", "rings", "rung"}),
    frozenset({"read", "reading", "reads"}),
    frozenset({"rend", "rending", "rends", "rent"}),
    frozenset({"rid", "ridding", "rids"}),
    frozenset({"overridden", "override", "overrides", "overriding", "overrode"}),
    frozenset({"ridden", "ride", "rides", "riding", "rode"}),
    frozenset({"arise", "arisen", "arises", "arising", "arose"}),
    frozenset({"rise", "risen", "rises", "rising", "rose"}),
    frozenset({"sang", "sing", "singing", "sings", "sung"}),
    frozenset({"sank", "sink", "sinking", "sinks", "sunk"}),
    frozenset({"sat", "sit", "sits", "sitting"}),
    frozenset({"foresaw", "foresee", "foreseeing", "foreseen", "foresees"}),
    frozenset({"oversaw", "oversee", "overseeing", "overseen", "oversees"}),
    frozenset({"saw", "see", "seeing", "seen", "sees"}),
    frozenset({"said", "say", "saying", "says"}),
    frozenset({"unsaid", "unsay", "unsaying", "unsays"}),
    frozenset({"beseech", "beseeched", "beseeches", "beseeching", "besought"}),
    frozenset({"seek", "seeking", "seeks", "sought"}),
    frozenset({"resell", "reselling", "resells", "resold"}),
    frozenset({"sell", "selling", "sells", "sold"}),
    frozenset({"send", "sending", "sends", "sent"}),
    frozenset({"inset", "insets", "insetting"}),
    frozenset({"offset", "offsets", "offsetting"}),
    frozenset({"reset", "resets", "resetting"}),
    frozenset({"set", "sets", "setting"}),
    frozenset({"typeset", "typesets", "typesetting"}),
    frozenset({"upset", "upsets", "upsetting"}),
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
    frozenset({"bespeak", "bespeaking", "bespeaks", "bespoke", "bespoken"}),
    frozenset({"speak", "speaking", "speaks", "spoke", "spoken"}),
    frozenset({"sped", "speed", "speeded", "speeding", "speeds"}),
    frozenset({"spell", "spelled", "spelling", "spells", "spelt"}),
    frozenset({"spend", "spending", "spends", "spent"}),
    frozenset({"spill", "spilled", "spilling", "spills", "spilt"}),
    frozenset({"spin", "spinning", "spins", "spun"}),
    frozenset({"split", "splits", "splitting"}),
    frozenset({"spoil", "spoiled", "spoiling", "spoils", "spoilt"}),
    frozenset({"sprang", "spring", "springing", "springs", "sprung"}),
    frozenset({"spread", "spreading", "spreads"}),
    frozenset({"misunderstand", "misunderstanding", "misunderstands", "misunderstood"}),
    frozenset({"stand", "standing", "stands", "stood"}),
    frozenset({"understand", "understanding", "understands", "understood"}),
    frozenset({"withstand", "withstanding", "withstands", "withstood"}),
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
    frozenset({"forswear", "forswearing", "forswears", "forswore", "forsworn"}),
    frozenset({"swear", "swearing", "swears", "swore", "sworn"}),
    frozenset({"sweat", "sweated", "sweating", "sweats"}),
    frozenset({"sweep", "sweeping", "sweeps", "swept"}),
    frozenset({"swell", "swelled", "swelling", "swells", "swollen"}),
    frozenset({"swing", "swinging", "swings", "swung"}),
    frozenset({"forsake", "forsaken", "forsakes", "forsaking", "forsook"}),
    frozenset({"mistake", "mistaken", "mistakes", "mistaking", "mistook"}),
    frozenset({"overtake", "overtaken", "overtakes", "overtaking", "overtook"}),
    frozenset({"partake", "partaken", "partakes", "partaking", "partook"}),
    frozenset({"retake", "retaken", "retakes", "retaking", "retook"}),
    frozenset({"take", "taken", "takes", "taking", "took"}),
    frozenset({"undertake", "undertaken", "undertakes", "undertaking", "undertook"}),
    frozenset({"taught", "teach", "teaches", "teaching"}),
    frozenset({"tear", "tearing", "tears", "tore", "torn"}),
    frozenset({"foretell", "foretelling", "foretells", "foretold"}),
    frozenset({"retell", "retelling", "retells", "retold"}),
    frozenset({"tell", "telling", "tells", "told"}),
    frozenset({"rethink", "rethinking", "rethinks", "rethought"}),
    frozenset({"think", "thinking", "thinks", "thought"}),
    frozenset({"threw", "throw", "throwing", "thrown", "throws"}),
    frozenset({"thrive", "thrived", "thriven", "thrives", "thriving", "throve"}),
    frozenset({"thrust", "thrusting", "thrusts"}),
    frozenset({"tread", "treading", "treads", "trod", "trodden"}),
    frozenset({"vex", "vexed", "vexes", "vexing", "vext"}),
    frozenset({"awake", "awakes", "awaking", "awoke", "awoken"}),
    frozenset({"wake", "wakes", "waking", "woke", "woken"}),
    frozenset({"outwear", "outwearing", "outwears", "outwore", "outworn"}),
    frozenset({"wear", "wearing", "wears", "wore", "worn"}),
    frozenset({"wed", "wedded", "wedding", "weds"}),
    frozenset({"weep", "weeping", "weeps", "wept"}),
    frozenset({"wet", "wets", "wetted", "wetting"}),
    frozenset({"will", "would"}),
    frozenset({"win", "winning", "wins", "won"}),
    frozenset({"rewind", "rewinding", "rewinds", "rewound"}),
    frozenset({"unwind", "unwinding", "unwinds", "unwound"}),
    frozenset({"wind", "winding", "winds", "wound"}),
    frozenset({"work", "worked", "working", "works", "wrought"}),
    frozenset({"wreak", "wreaked", "wreaking", "wreaks"}),
    frozenset({"weave", "weaves", "weaving", "wove", "woven"}),
    frozenset({"wring", "wringing", "wrings", "wrung"}),
    frozenset({"rewrite", "rewrites", "rewriting", "rewritten", "rewrote"}),
    frozenset(
        {"underwrite", "underwrites", "underwriting", "underwritten", "underwrote"}
    ),
    frozenset({"write", "writes", "writing", "written", "wrote"}),
)

# Irregular noun plural forms (mouse/mice, child/children, etc.)
_IRREGULAR_NOUN_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"mouse", "mice"}),
    frozenset({"child", "children"}),
    frozenset({"foot", "feet"}),
    frozenset({"tooth", "teeth"}),
    frozenset({"goose", "geese"}),
    frozenset({"man", "men"}),
    frozenset({"woman", "women"}),
    frozenset({"ox", "oxen"}),
    frozenset({"die", "dice"}),
    frozenset({"crisis", "crises"}),
    frozenset({"phenomenon", "phenomena"}),
    frozenset({"criterion", "criteria"}),
    frozenset({"louse", "lice"}),
    frozenset({"person", "people", "persons"}),
    frozenset({"sheep", "sheep"}),
    frozenset({"deer", "deer"}),
    frozenset({"fish", "fishes"}),
    frozenset({"species", "species"}),
    frozenset({"series", "series"}),
    frozenset({"datum", "data"}),
    frozenset({"medium", "media"}),
    frozenset({"bacterium", "bacteria"}),
    frozenset({"curriculum", "curricula"}),
    frozenset({"index", "indices", "indexes"}),
    frozenset({"appendix", "appendices", "appendixes"}),
    frozenset({"matrix", "matrices"}),
    frozenset({"vertex", "vertices"}),
    frozenset({"axis", "axes"}),
    frozenset({"thesis", "theses"}),
    frozenset({"hypothesis", "hypotheses"}),
    frozenset({"parenthesis", "parentheses"}),
    frozenset({"synthesis", "syntheses"}),
    frozenset({"analysis", "analyses"}),
    frozenset({"diagnosis", "diagnoses"}),
    frozenset({"oasis", "oases"}),
    frozenset({"cactus", "cacti", "cactuses"}),
    frozenset({"fungus", "fungi", "funguses"}),
    frozenset({"nucleus", "nuclei"}),
    frozenset({"stimulus", "stimuli"}),
    frozenset({"syllabus", "syllabi", "syllabuses"}),
    frozenset({"focus", "foci", "focuses"}),
    frozenset({"radius", "radii"}),
    frozenset({"alumnus", "alumni"}),
    frozenset({"larva", "larvae"}),
    frozenset({"alga", "algae"}),
    frozenset({"formula", "formulae", "formulas"}),
    frozenset({"nebula", "nebulae", "nebulas"}),
    frozenset({"vertebra", "vertebrae"}),
    frozenset({"antenna", "antennae", "antennas"}),
    frozenset({"vita", "vitae"}),
    frozenset({"addendum", "addenda"}),
    frozenset({"erratum", "errata"}),
    frozenset({"memorandum", "memoranda", "memorandums"}),
    frozenset({"ovum", "ova"}),
    frozenset({"stratum", "strata"}),
    frozenset({"symposium", "symposia", "symposiums"}),
    frozenset({"automaton", "automata", "automatons"}),
    frozenset({"codex", "codices"}),
    frozenset({"apex", "apices", "apexes"}),
    frozenset({"vortex", "vortices", "vortexes"}),
)

_IRREGULAR_LOOKUP: dict[str, frozenset[str]] = {
    form: group
    for groups in (_IRREGULAR_VERB_GROUPS, _IRREGULAR_NOUN_GROUPS)
    for group in groups
    for form in group
}


def _iter_hyphen_segments(token: str) -> list[tuple[str, int, int]]:
    """Return ``(segment, rel_start, rel_end)`` for hyphen-separated parts.

    Offsets are relative to *token*. Leading/trailing punctuation is skipped
    so ``non-staple,`` yields ``non`` and ``staple``. Unhyphenated tokens
    yield a single segment (the core). Empty segments from ``--`` are dropped.
    """
    if not token:
        return []
    left = 0
    right = len(token)
    while left < right and token[left] in _PUNCT_STRIP:
        left += 1
    while right > left and token[right - 1] in _PUNCT_STRIP:
        right -= 1
    if left >= right:
        return []

    segments: list[tuple[str, int, int]] = []
    i = left
    while i < right:
        if token[i] in _HYPHEN_CHARS:
            i += 1
            continue
        j = i
        while j < right and token[j] not in _HYPHEN_CHARS:
            j += 1
        if j > i:
            segments.append((token[i:j], i, j))
        i = j
    return segments


def _token_matches_lemma(surface_token: str, lemma: str) -> bool:
    """True if *surface_token* equals *lemma* (flex) or contains it as a segment.

    Whole-token flex first (``insists`` ↔ ``insist``). Then hyphen compounds:
    lemma ``staple`` matches surface ``non-staple`` / ``non-staples``. Does
    **not** match letter-substrings of solid words (``go`` ⊄ ``cargo``).
    """
    if _tokens_flex_equal(lemma, surface_token):
        return True
    segments = _iter_hyphen_segments(surface_token)
    if len(segments) <= 1:
        return False
    return any(_tokens_flex_equal(lemma, seg) for seg, _a, _b in segments)


def _escape_regex(text: str) -> str:
    """Escape regex metacharacters in *text*."""
    return _RE_ESCAPE_RE.sub(r"\\\1", text)


def _literal_find(pattern: str, haystack: str) -> bool:
    """Check whether normalized *pattern* occurs literally in *haystack*.

    For single-token alphabetic patterns, require whole-token equality so
    short lemmas like ``go`` do not accidentally match inside ``gone`` /
    ``going`` via pure substring. Hyphen compounds are an exception:
    ``staple`` matches inside ``non-staple`` as a segment, not a letter
    substring. Multi-word / non-alpha patterns keep plain substring matching.
    """
    if not pattern:
        return False
    # Single simple alphabetic token → whole-token or hyphen-segment only.
    if re.fullmatch(r"[a-z]+", pattern):
        for tok in haystack.split(" "):
            if _strip_token_punct(tok) == pattern:
                return True
            # Hyphen compound: match lemma against a segment (staple ⊂ non-staple).
            if any(
                _strip_token_punct(seg).casefold() == pattern
                for seg, _a, _b in _iter_hyphen_segments(tok)
            ):
                return True
        return False
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

    Matching is case-insensitive: input is casefolded after punctuation strip
    so surface forms like ``Exacted`` still stem to ``exact``.
    """
    t = _strip_token_punct(token).casefold()
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
    """True if two tokens are equal or share an inflection stem candidate.

    Comparison is case-insensitive so lemma ``exact`` matches surface
    ``Exacted`` / ``EXACTED``.
    """
    sa = unicodedata.normalize("NFC", _strip_token_punct(a)).casefold()
    sb = unicodedata.normalize("NFC", _strip_token_punct(b)).casefold()
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

    Whitespace-tokenized phrases and single ASCII-word tokens are eligible;
    continuous scripts stay on the literal-only path.
    """
    # Continuous scripts stay literal-only.  A pair of single ASCII-word
    # tokens, however, is a normal spaced-language case even though neither
    # value happens to contain whitespace (for example, ``go`` ↔ ``Went``).
    # The inflection rules below are English-oriented, so do not apply them to
    # arbitrary continuous Unicode text such as CJK.
    if " " not in norm_item and " " not in norm_sentence:
        item_token = _strip_token_punct(norm_item)
        sentence_token = _strip_token_punct(norm_sentence)
        if not (
            re.fullmatch(r"[a-z]+", item_token)
            and re.fullmatch(r"[a-z]+", sentence_token)
        ):
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
        if all(_token_matches_lemma(st, it) for it, st in zip(item_tokens, window)):
            return True
    return False


@dataclass
class ValidationResult:
    valid: bool
    missing: list[str] = field(default_factory=list)
    # Lemma → verified surface form accepted by residual checks (e.g. AI).
    # Used so insert/update can re-verify the same surface without requiring
    # the lemma to match via local inflection rules alone.
    accepted_surfaces: dict[str, str] = field(default_factory=dict)


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
    # Single alphabetic token: whole-token or hyphen-segment only.
    for tok in norm_sentence.split(" "):
        if _strip_token_punct(tok) == norm_surface:
            return True
        if any(
            _strip_token_punct(seg).casefold() == norm_surface
            for seg, _a, _b in _iter_hyphen_segments(tok)
        ):
            return True
    return False


def _ws_tokens_with_spans(text: str) -> list[tuple[str, int, int]]:
    """Whitespace-split *text* into (token, start, end) original spans."""
    tokens: list[tuple[str, int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        j = i
        while j < n and not text[j].isspace():
            j += 1
        tokens.append((text[i:j], i, j))
        i = j
    return tokens


def _nfc_casefold_with_original_spans(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Return NFC/casefolded *text* together with offsets into the input.

    Normalizing an entire string can change its length (notably for NFD
    characters), so indices into that normalized string cannot be used to
    slice the original sentence.  Normalize each base character and its
    following combining marks as a unit instead, retaining the original span
    for every resulting character.  Canonical Hangul Jamo L+V(+T) sequences
    are also one unit: unlike Latin accents, their components have combining
    class zero, but NFC composes the sequence into one syllable.
    """
    normalized_parts: list[str] = []
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        start = i
        codepoint = ord(text[i])
        i += 1

        # NFC composes a canonical Hangul leading Jamo followed by a vowel
        # Jamo and, optionally, a trailing Jamo.  Group it before normalizing
        # so the resulting syllable maps back to the full original sequence.
        if (
            0x1100 <= codepoint <= 0x115F
            and i < len(text)
            and 0x1160 <= ord(text[i]) <= 0x11A7
        ):
            i += 1
            if i < len(text) and 0x11A8 <= ord(text[i]) <= 0x11FF:
                i += 1
        while i < len(text) and unicodedata.combining(text[i]):
            i += 1
        part = unicodedata.normalize("NFC", text[start:i]).casefold()
        normalized_parts.append(part)
        spans.extend([(start, i)] * len(part))
    return "".join(normalized_parts), spans


def _normalized_literal_span(sentence: str, item: str) -> tuple[int, int] | None:
    """Locate a NFC/NFD-equivalent literal while returning original offsets."""
    needle = unicodedata.normalize("NFC", item).casefold()
    if not needle:
        return None
    haystack, spans = _nfc_casefold_with_original_spans(sentence)
    start = haystack.find(needle)
    if start < 0:
        return None
    end = start + len(needle)
    return spans[start][0], spans[end - 1][1]


def locate_item_surface_span(
    sentence: str,
    item: str,
    preferred_surface: str | None = None,
) -> tuple[int, int] | None:
    """Return ``(start, end)`` of the first surface match for *item* in *sentence*.

    Uses the same local matching ideas as validation:
    1. Preferred surface (AI residual / stored span), if provided and present
    2. Inflection-tolerant consecutive whitespace tokens (English phrases)
    3. Case-insensitive literal substring (CJK / continuous / exact)
    4. Whole-token flex match for single alphabetic lemmas

    Offsets are into the original *sentence* string (not normalized).
    """
    if not sentence or not item:
        return None

    # Preferred surface first (e.g. lemma lie with surface lay).
    pref = (preferred_surface or "").strip()
    if pref and pref.casefold() != str(item).strip().casefold():
        span = locate_item_surface_span(sentence, pref, preferred_surface=None)
        if span is not None:
            return span

    item_tokens = _tokenize(normalize_sentence(item))
    sent_tokens = _ws_tokens_with_spans(sentence)

    # Path 1: consecutive token flex match → original span covering the window.
    if item_tokens and sent_tokens and len(item_tokens) <= len(sent_tokens):
        k = len(item_tokens)
        for i in range(0, len(sent_tokens) - k + 1):
            window = sent_tokens[i : i + k]
            if all(
                _token_matches_lemma(st, it)
                for it, (st, _a, _b) in zip(item_tokens, window)
            ):
                # Prefer the matched hyphen segment when lemma is only a part
                # of a compound (staple ⊂ non-staple); else whole token window.
                if len(item_tokens) == 1 and len(window) == 1:
                    tok, t_start, t_end = window[0]
                    segs = _iter_hyphen_segments(tok)
                    if len(segs) > 1:
                        for seg, rel_a, rel_b in segs:
                            if _tokens_flex_equal(item_tokens[0], seg):
                                return (t_start + rel_a, t_start + rel_b)
                start = window[0][1]
                end = window[-1][2]
                return (start, end)

    # Path 2: Unicode-normalized, case-insensitive literal substring.
    # Useful for continuous scripts and multi-word/punct spans.  The mapped
    # offsets keep an NFD surface sliceable from the original sentence.
    # Single alphabetic English lemmas must NOT substring-match inside
    # longer words (go ⊂ cargo) — those use whole-token path 3.
    raw = item.strip()
    if not raw:
        return None
    norm_item = normalize_sentence(item)
    single_alpha = bool(re.fullmatch(r"[a-z]+", norm_item))
    if not single_alpha:
        span = _normalized_literal_span(sentence, raw)
        if span is not None:
            return span

    # Path 3: whole-token or hyphen-segment match for single alphabetic lemmas.
    if single_alpha:
        for tok, start, end in sent_tokens:
            if _token_matches_lemma(tok, norm_item):
                segs = _iter_hyphen_segments(tok)
                if len(segs) > 1:
                    for seg, rel_a, rel_b in segs:
                        if _tokens_flex_equal(norm_item, seg):
                            return (start + rel_a, start + rel_b)
                return (start, end)
    return None


def _item_expression_and_surface(item) -> tuple[str, str | None]:
    """Unpack expression (+ optional preferred surface) from a structured item."""
    if isinstance(item, (tuple, list)):
        expr = str(item[0] or "")
        surface = None
        if len(item) > 3 and item[3]:
            surface = str(item[3]).strip() or None
        return expr, surface
    return str(item or ""), None


def locate_unfamiliar_spans(
    sentence: str,
    items: list,
) -> list[tuple[int, int]]:
    """Locate non-overlapping surface spans for *items* in *sentence*.

    Longer spans win over shorter overlapping ones; earlier items win ties.
    Returns sorted ``(start, end)`` pairs.

    *items* may be expression strings or structured rows with an optional
    preferred surface at index 3 (``surface_form``).
    """
    candidates: list[tuple[int, int, int]] = []  # start, end, -length
    for item in items:
        if not item:
            continue
        expr, preferred = _item_expression_and_surface(item)
        if not expr:
            continue
        span = locate_item_surface_span(sentence, expr, preferred_surface=preferred)
        if span is None:
            continue
        start, end = span
        if start < 0 or end <= start or end > len(sentence):
            continue
        candidates.append((start, end, start - end))  # third key: prefer longer

    # Prefer earlier start, then longer span.
    candidates.sort(key=lambda t: (t[0], t[2]))
    chosen: list[tuple[int, int]] = []
    for start, end, _neg_len in candidates:
        if any(not (end <= cs or start >= ce) for cs, ce in chosen):
            continue
        chosen.append((start, end))
    chosen.sort(key=lambda t: t[0])
    return chosen


def sort_items_by_sentence_order(sentence: str, items: list) -> list:
    """Order unfamiliar items by first surface appearance in *sentence*.

    Items that cannot be located keep relative order after all located ones.
    Accepts structured ``(expression, meaning, ...)`` tuples or plain strings.
    Optional preferred surface at index 3 is used for location.
    """
    if not items:
        return []

    located: list[tuple[int, int, object]] = []  # (start, original_idx, item)
    missing: list[tuple[int, object]] = []
    for idx, item in enumerate(items):
        expr, preferred = _item_expression_and_surface(item)
        span = locate_item_surface_span(
            sentence or "", str(expr or ""), preferred_surface=preferred
        )
        if span is None:
            missing.append((idx, item))
        else:
            located.append((span[0], idx, item))
    located.sort(key=lambda t: (t[0], t[1]))
    return [item for _start, _idx, item in located] + [item for _idx, item in missing]


def format_sentence_meaning_lines(items: list) -> list[str]:
    """Format expression+meaning lines for a sentence card back.

    One item: ``**expr**: meaning`` (no number).
    Multiple items: numbered ``1. **expr**: meaning``, one line each.
    """
    rows: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, (tuple, list)):
            expr = str(item[0] or "").strip()
            meaning = str(item[1] or "").strip() if len(item) > 1 else ""
        else:
            expr = str(item or "").strip()
            meaning = ""
        if not expr:
            continue
        rows.append((expr, meaning))
    if not rows:
        return []
    numbered = len(rows) > 1
    lines: list[str] = []
    for i, (expr, meaning) in enumerate(rows, start=1):
        body = f"**{expr}**: {meaning}" if meaning else f"**{expr}**"
        lines.append(f"{i}. {body}" if numbered else body)
    return lines


def highlight_unfamiliar_in_sentence(
    sentence: str,
    items: list,
) -> str:
    """Return Markdown with matched unfamiliar surface spans wrapped in ``**bold**``.

    The rest of the sentence stays normal weight. If no span is found for an
    item, that item is simply not highlighted (the sentence text is preserved).

    *items* may be expression strings or structured rows; optional preferred
    surface at index 3 (AI residual) is used when local inflection fails.
    """
    if not sentence:
        return ""
    spans = locate_unfamiliar_spans(sentence, items)
    if not spans:
        return sentence

    parts: list[str] = []
    pos = 0
    for start, end in spans:
        if start < pos:
            continue
        # Shrink span so leading/trailing punctuation stays outside the bold
        # mark (e.g. ``Exacted!`` → bold ``Exacted`` + bare ``!``).
        while start < end and sentence[start] in _PUNCT_STRIP:
            start += 1
        while end > start and sentence[end - 1] in _PUNCT_STRIP:
            end -= 1
        if start >= end:
            continue
        if start < pos:
            continue
        parts.append(sentence[pos:start])
        surface = sentence[start:end]
        # Avoid nested markdown if the surface already starts/ends with **.
        if surface.startswith("**") and surface.endswith("**") and len(surface) >= 4:
            parts.append(surface)
        else:
            parts.append(f"**{surface}**")
        pos = end
    parts.append(sentence[pos:])
    return "".join(parts)


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
    Accepted lemma→surface pairs are returned in ``.accepted_surfaces`` so
    callers can re-verify them at insert/update time.
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
    accepted_surfaces: dict[str, str] = {}
    for item in missing_items:
        key = normalize_sentence(item)
        claim = claim_by_norm.get(key)
        if claim is None:
            still_missing.append(item)
            continue
        found = bool(getattr(claim, "found", False))
        surface = str(getattr(claim, "surface", "") or "").strip()
        if found and surface and surface_form_in_sentence(sentence, surface):
            # Keep the original lemma spelling as the key (what the UI stores).
            accepted_surfaces[item] = surface
            accepted_surfaces[key] = surface
            continue
        still_missing.append(item)

    return ValidationResult(
        valid=len(still_missing) == 0,
        missing=still_missing,
        accepted_surfaces=accepted_surfaces,
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
