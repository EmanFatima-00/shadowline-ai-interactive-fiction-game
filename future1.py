"""
================================================================================
 SHADOWLINE — An AI-Driven Psychological Interactive Fiction Engine
================================================================================

WHAT THIS IS
------------
A single-file Python/Tkinter game where a REAL AI model (Google's Gemini,
called through the official generateContent API) acts as a live "game master."

On every turn, the program sends the AI:
    - a compact summary of the whole story so far (state.summary())
    - the last few full exchanges (for tone/continuity)
    - the player's most recent answer
    - the current turn number / target story length

The AI must respond with STRUCTURED JSON describing what happens next:
narration, the next question, 4-5 meaningful choices, how the player's
psychological profile shifts, any new clue/relationship change, and whether
the story should continue or end. The program validates that JSON, updates
the game state, and renders it. There is NO random selection of "the next
question" anywhere in this file — the only randomness used is for picking
flavor details (character names, opening hook, setting) at the very start of
a NEW game, which is explicitly allowed as harmless atmospheric variance.

This version uses Google's Gemini API, because it has a free tier that does
not require a credit card to get started.

HOW TO RUN
----------
1. pip install requests          (only external dependency)
2. Get a free API key at https://aistudio.google.com/apikey (sign in with a
   Google account, click "Create API key" — no card required for the free
   tier, which is generous enough for many playthroughs).
3. python psychology_game.py
4. On the title screen, paste your Gemini API key. It is only kept in memory
   for this run; it is never written to disk or hard-coded.

If you'd rather not paste it every time, you can instead set the environment
variable GEMINI_API_KEY before running the program, and the title screen
will pick it up automatically (still editable).

================================================================================
"""

import os
import sys
import json
import re
import random
import textwrap
import traceback
from datetime import datetime

try:
    import requests
except ImportError:
    print("This program needs the 'requests' library.")
    print("Install it with:  pip install requests")
    sys.exit(1)

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext


# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================

# Google Gemini API (has a free tier — no credit card required to start).
# Get a key at: https://aistudio.google.com/apikey
# We use the "latest" alias (not a pinned version) so this keeps working even
# after Google retires older model versions.
GEMINI_MODEL = "gemini-flash-latest"
API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

# Roughly how many player turns a full playthrough should last.
MIN_TURNS = 35
MAX_TURNS = 40
HARD_END_TURN = 42  # absolute safety cap so a game can never run forever

SAVE_FILE = "psych_game_save.json"

# How many of the most recent full exchanges (situation+question+answer) we
# send to the AI verbatim, in addition to the compressed running summary.
RECENT_HISTORY_WINDOW = 4

# Valid "approach" tags a choice can be labeled with. This keeps choices
# meaningfully different (per spec) instead of yes/maybe/no.
VALID_APPROACHES = {
    "confront", "manipulate", "investigate", "trust", "distrust",
    "remain_silent", "lie", "confess", "take_risk", "protect",
    "sacrifice", "leave", "continue", "negotiate", "deceive_self",
    "seek_help", "isolate", "expose", "forgive", "retaliate"
}

# The psychological dimensions the AI tracks and interprets over the whole
# playthrough. These are never shown to the player during the game.
PSYCH_DIMENSIONS = [
    "trust", "suspicion", "empathy", "fear", "curiosity",
    "impulsivity", "self_control", "honesty", "attachment",
    "risk_tolerance", "independence", "guilt", "emotional_resilience",
]


# ==============================================================================
# 2. ATMOSPHERIC CONTENT POOLS
#    (100+ combinable seed elements — used ONLY to seed a brand-new game with
#    flavor so different players start in different worlds. None of this is
#    ever used to pick "the next question" mid-story; that is always decided
#    by the AI agent from context, per the core requirement.)
# ==============================================================================

OPENING_HOOKS = [
    "A letter arrives at your door with no return address, addressed in handwriting you don't recognize but somehow feel you should.",
    "You wake up in your apartment to find a single chair moved to face your bed, and a half-finished cup of tea on the table that isn't yours.",
    "Your closest friend calls you at 3 a.m., says three words you can barely make out, and hangs up before you can respond.",
    "You find a key taped under your desk at work with a small tag that just says 'DON'T.'",
    "A stranger at a funeral you weren't invited to hands you a photograph and walks away without a word.",
    "Your phone shows a two-hour outgoing call to a number you don't recognize, made while you were asleep.",
    "You receive a package addressed to someone who died years ago, at your current address.",
    "A coworker who was fired last month leaves a voicemail warning you not to trust someone in your office.",
    "You find your own name written in the margin of an old diary that isn't yours, dated before you were born.",
    "Your sibling asks you to keep a secret, then refuses to tell you what it is until you agree first.",
    "A locked drawer in your late parent's house won't open with any key you own — except one you've never seen before, in your own coat pocket.",
    "You're handed an envelope at your job with instructions to deliver it, no questions asked, and a payment that's far too generous.",
    "An old friend reappears after years of silence and seems to know things about your life they shouldn't.",
    "You discover a second set of messages on your partner's old laptop, sent under your name, that you never wrote.",
    "A anonymous note left on your car says: 'They lied to you about that night. Ask them again.'",
]

SETTING_DETAILS = [
    "a fog-heavy coastal town where the tide comes in faster than the maps suggest",
    "a mid-size city where half the streetlights on your block have quietly stopped working",
    "an inherited countryside house with rooms that don't match the blueprint you found in the attic",
    "a cramped apartment building where the elevator only stops on your floor after midnight",
    "a university campus during a long winter break, mostly empty except for a few locked departments",
    "a family business on the edge of bankruptcy, full of employees who've worked there far too long to be this evasive",
    "a small hospital ward where the night shift keeps changing the subject",
    "a rented lake house that the owner keeps 'forgetting' to give you full access to",
]

CHARACTER_ARCHETYPES = [
    ("Mira", "your closest friend, warm but evasive about the last few weeks"),
    ("Daniel", "a sibling who left home young and came back changed"),
    ("Priya", "a coworker who seems to know more about your case than she should"),
    ("Tomas", "an old mentor whose advice has started to feel like instructions"),
    ("Elena", "a neighbor who watches your apartment more than is comfortable"),
    ("Sam", "a partner whose story about last Tuesday keeps shifting in small ways"),
    ("Adrian", "an investigator who claims to be on your side but withholds key facts"),
    ("Naomi", "a stranger who keeps appearing at exactly the wrong (or right) moments"),
    ("Victor", "a family lawyer handling an estate that's more complicated than advertised"),
    ("Clara", "an old friend who vanished after the night everything changed"),
]

TWIST_ELEMENTS = [
    "a photograph that places someone where they swore they weren't",
    "a second phone hidden in a place only one other person knew about",
    "a debt that someone has been quietly paying off in your name",
    "a recorded voicemail that contradicts an alibi",
    "a locked room that someone insists doesn't exist",
    "a shared secret that two people have been lying to each other about",
    "a will that was changed days before a death",
    "a message sent from an account that was supposedly deleted years ago",
    "a witness who recants everything the moment you press further",
    "an old promise that's now being used as leverage",
    "a pattern of small lies that only makes sense once you see all of them together",
    "evidence that someone you trusted was protecting the person you suspect",
    "a location that appears in two people's stories, but neither will explain why",
    "handwriting on an old note that matches someone unexpected",
    "a financial transfer timed suspiciously close to the incident",
]

CLUE_FLAVORS = [
    "a torn receipt", "a spare key on an unfamiliar ring", "an old photograph with someone scratched out",
    "a burner phone", "a half-erased whiteboard note", "a diary page torn out",
    "a voicemail transcript", "a pawn shop ticket", "a hotel keycard", "a printed map with a circled address",
    "a locket with a name filed off", "an old employee badge", "a bank statement with one line highlighted",
    "a letter never sent", "a security log with a gap in it",
]
# ==============================================================================
# 3. GAME STATE
# ==============================================================================

class GameState:
    """
    Holds everything the AI agent needs to reason about the story, and
    everything the ending report needs to explain the player's journey.

    This is intentionally a plain, JSON-serializable structure so it can be
    saved/loaded from a single auto-generated save file.
    """

    def __init__(self):
        self.turn_count = 0
        self.ended = False

        # Psychological profile: running numeric scores, adjusted by the AI's
        # own interpretation each turn (never simple flat "+1 per click").
        self.psych = {dim: 0 for dim in PSYCH_DIMENSIONS}

        # Compact structured memory
        self.scene_history = []        # list of {situation, question, answer, approach}
        self.important_decisions = []  # list of short strings, AI-flagged as significant
        self.characters = {}           # name -> {"description":..., "relationship_notes": [...]}
        self.discovered_clues = []     # list of strings
        self.unresolved_mysteries = [] # list of strings
        self.current_objective = ""
        self.major_consequences = []   # list of strings

        # Seed / flavor chosen once at game start (atmospheric randomness only)
        self.seed = {}

        # Current situation being displayed to the player (set by AI each turn)
        self.current_narration = ""
        self.current_question = ""
        self.current_choices = []      # list of {"id","label","approach"}
        self.current_emotional_tone = ""

        # Final result, filled in when the AI ends the story
        self.ending_title = None
        self.ending_explanation = None

    # ---- persistence -----------------------------------------------------

    def to_dict(self):
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data):
        gs = cls()
        gs.__dict__.update(data)
        return gs

    def save(self, path=SAVE_FILE):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path=SAVE_FILE):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    # ---- helpers used when building AI prompts ---------------------------

    def summary_for_ai(self):
        """A compact, structured summary of the whole game so far. This is
        what lets the AI 'remember' a long story without us sending the full
        transcript every single turn (rule 10: bounded context)."""
        top_psych = sorted(self.psych.items(), key=lambda kv: abs(kv[1]), reverse=True)
        psych_str = ", ".join(f"{k}={v:+d}" for k, v in top_psych if v != 0) or "no strong tendencies yet"

        chars_str = "; ".join(
            f"{name}: {info.get('description','')} (notes: {', '.join(info.get('relationship_notes', [])) or 'none yet'})"
            for name, info in self.characters.items()
        ) or "none introduced yet"

        return textwrap.dedent(f"""
            TURN: {self.turn_count} (target story length: {MIN_TURNS}-{MAX_TURNS} turns)
            OPENING SEED: {self.seed.get('hook','')} | Setting: {self.seed.get('setting','')}
            CURRENT OBJECTIVE: {self.current_objective or 'not yet defined'}
            PSYCHOLOGICAL TENDENCIES SO FAR: {psych_str}
            CHARACTERS SO FAR: {chars_str}
            DISCOVERED CLUES: {', '.join(self.discovered_clues) or 'none yet'}
            UNRESOLVED MYSTERIES: {', '.join(self.unresolved_mysteries) or 'none yet'}
            MAJOR CONSEQUENCES SO FAR: {', '.join(self.major_consequences) or 'none yet'}
            KEY DECISIONS SO FAR: {', '.join(self.important_decisions) or 'none flagged yet'}
        """).strip()

    def recent_history_for_ai(self):
        """Full text of the last few exchanges, for tone/continuity."""
        recent = self.scene_history[-RECENT_HISTORY_WINDOW:]
        lines = []
        for turn in recent:
            lines.append(f"[Situation]: {turn['situation']}")
            lines.append(f"[Question]: {turn['question']}")
            lines.append(f"[Player chose ({turn.get('approach','?')})]: {turn['answer']}")
        return "\n".join(lines) if lines else "(this is the very first turn)"


# ==============================================================================
# 4. THE AI AGENT
# ==============================================================================

class AIAgentError(Exception):
    pass


class AIAgent:
    """
    Wraps calls to the Gemini API and enforces the structured-JSON
    contract described in SYSTEM_PROMPT. This class is the ENTIRE "brain"
    of the game — it decides what happens next based on context, not on any
    pre-written branching tree.
    """

    SYSTEM_PROMPT = textwrap.dedent(f"""
        You are the game master (AI agent) for a dark psychological interactive
        fiction game called SHADOWLINE. You control EVERYTHING that happens next
        in the story, based on the player's previous answer and the accumulated
        game state you are given. You are not selecting from a pre-written list —
        you are actively deciding, like a human game master would, what a
        logical, dramatically satisfying next scene is.

        Before answering, internally reason (do not show this reasoning) about:
        - What exactly just happened, and what did the player choose?
        - What would realistically/dramatically follow from that specific choice?
        - What does the player currently know, and not know?
        - Is a character, clue, or mystery being revisited — if so, escalate or
          complicate it rather than repeating it?
        - Given the turn number, should the story now start moving toward a
          resolution?

        Your job:
        1. Never write a bland "and then something else happened" scene. The
           next situation must be a direct, specific, logical/emotional
           consequence of the player's last choice.
        2. Offer 4-5 CHOICES that represent different psychological approaches
           (examples: confront, manipulate, investigate, trust, distrust,
           remain_silent, lie, confess, take_risk, protect, sacrifice, leave,
           negotiate, seek_help, isolate, expose, forgive, retaliate). Never
           offer generic yes/maybe/no choices.
        3. Track and evolve: characters, relationships, clues, mysteries,
           consequences, and the player's psychological profile. Interpret
           psychology holistically — a small delta (-2 to +2) per relevant
           dimension per turn, based on what the CHOICE reveals about the
           player, not mechanical point-farming.
        4. Never repeat a scene, question, or wording that has already
           appeared. If a topic recurs, it must escalate, complicate, or
           reveal something new.
        5. The story should last approximately {MIN_TURNS}-{MAX_TURNS} total
           player turns. As the turn number approaches {MAX_TURNS}, start
           converging the plot threads toward a resolution. By turn
           {HARD_END_TURN} you MUST end the story (continue_or_end = "end")
           no matter what.
        6. When you end the story, choose an evocative one-to-three-word
           ending title (e.g. "THE OBSERVER", "THE CONFRONTER", "THE
           PROTECTOR", "THE MANIPULATOR", "THE ESCAPIST" — or invent a more
           fitting one) and write a genuine explanation that cites specific
           decisions the player made across the playthrough, not a shallow
           label.

        You MUST respond with ONLY a single JSON object — no prose before or
        after it, no markdown code fences — matching EXACTLY this schema:

        {{
          "narration": "<2-5 sentences describing what just happened as a direct consequence of the player's last choice>",
          "question": "<the next in-story question/prompt for the player>",
          "choices": [
            {{"id": "A", "label": "<short choice text>", "approach": "<one tag from the approach list>"}},
            {{"id": "B", "label": "...", "approach": "..."}},
            {{"id": "C", "label": "...", "approach": "..."}},
            {{"id": "D", "label": "...", "approach": "..."}}
          ],
          "emotional_tone": "<one or two words, e.g. 'tense', 'quietly devastating'>",
          "psychological_interpretation": {{"trust": 0, "suspicion": 0, "empathy": 0, "fear": 0, "curiosity": 0, "impulsivity": 0, "self_control": 0, "honesty": 0, "attachment": 0, "risk_tolerance": 0, "independence": 0, "guilt": 0, "emotional_resilience": 0}},
          "discovered_clue": "<short string, or null if none this turn>",
          "relationship_changes": {{"<character name>": "<short note, e.g. 'trust deepens' or introduce them if new>"}},
          "new_character_descriptions": {{"<character name>": "<one-line description, only for characters introduced this turn>"}},
          "important_decision": "<short string flagging this as a pivotal decision, or null if minor>",
          "new_objective": "<updated current objective string, or null to keep the existing one>",
          "consequence": "<short string describing a major consequence, or null if none>",
          "story_progress": "<one short sentence summarizing this beat, for the running log>",
          "continue_or_end": "continue" or "end",
          "ending_title": "<only present if continue_or_end is 'end'>",
          "ending_explanation": "<only present if continue_or_end is 'end' — 4-8 sentences citing specific choices>"
        }}

        Only include psychological_interpretation deltas that are non-zero for
        dimensions actually relevant to this specific choice; you may omit the
        others (missing = 0). Respond with ONLY the JSON object.
    """).strip()

    def __init__(self, api_key):
        self.api_key = api_key

    def _call_api(self, user_message):
        url = API_URL_TEMPLATE.format(model=GEMINI_MODEL, key=self.api_key)
        headers = {"content-type": "application/json"}
        payload = {
            "system_instruction": {"parts": [{"text": self.SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {
                "maxOutputTokens": 1500,
                # Ask Gemini to return raw JSON directly — we still defensively
                # re-parse/validate it below in case this is ever relaxed.
                "response_mime_type": "application/json",
            },
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=45)
        except requests.exceptions.RequestException as e:
            raise AIAgentError(f"Network error contacting the AI API: {e}")

        if resp.status_code != 200:
            try:
                detail = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                detail = resp.text
            raise AIAgentError(f"AI API error ({resp.status_code}): {detail}")

        try:
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                block_reason = data.get("promptFeedback", {}).get("blockReason")
                if block_reason:
                    raise AIAgentError(f"The AI declined to respond (reason: {block_reason}).")
                raise AIAgentError("The AI API returned no candidates.")
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            if not text:
                raise AIAgentError("The AI API returned an empty response.")
            return text
        except AIAgentError:
            raise
        except Exception as e:
            raise AIAgentError(f"Could not parse AI API response: {e}")

    @staticmethod
    def _extract_json(raw_text):
        """The model is instructed to return ONLY JSON, but we defensively
        extract the first balanced {...} block in case of stray text."""
        raw_text = raw_text.strip()
        # Strip accidental markdown fences
        raw_text = re.sub(r"^```(json)?", "", raw_text).strip()
        raw_text = re.sub(r"```$", "", raw_text).strip()

        start = raw_text.find("{")
        if start == -1:
            raise AIAgentError("AI response did not contain any JSON object.")

        depth = 0
        for i in range(start, len(raw_text)):
            if raw_text[i] == "{":
                depth += 1
            elif raw_text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw_text[start:i + 1]
                    return json.loads(candidate)
        raise AIAgentError("AI response JSON was not well-formed (unbalanced braces).")

    @staticmethod
    def _validate(scene):
        """Make sure the structured response has everything we need before
        we ever touch the GUI or game state with it."""
        required = ["narration", "question", "choices", "continue_or_end"]
        for key in required:
            if key not in scene:
                raise AIAgentError(f"AI response missing required field: '{key}'")

        if not isinstance(scene["choices"], list) or len(scene["choices"]) < 2:
            raise AIAgentError("AI response must include at least 2 choices.")

        for c in scene["choices"]:
            if "label" not in c or not c["label"]:
                raise AIAgentError("A choice is missing its label text.")
            c.setdefault("id", "?")
            c.setdefault("approach", "continue")
            if c["approach"] not in VALID_APPROACHES:
                c["approach"] = "continue"  # tolerate unknown tags rather than crash

        if scene["continue_or_end"] not in ("continue", "end"):
            raise AIAgentError("continue_or_end must be 'continue' or 'end'.")

        if scene["continue_or_end"] == "end":
            scene.setdefault("ending_title", "THE UNRESOLVED")
            scene.setdefault("ending_explanation", "Your story reached its end.")

        scene.setdefault("psychological_interpretation", {})
        scene.setdefault("relationship_changes", {})
        scene.setdefault("new_character_descriptions", {})
        scene.setdefault("discovered_clue", None)
        scene.setdefault("important_decision", None)
        scene.setdefault("new_objective", None)
        scene.setdefault("consequence", None)
        scene.setdefault("emotional_tone", "")
        scene.setdefault("story_progress", scene["narration"][:120])

        return scene

    def request_next_scene(self, state: GameState, player_answer: str, chosen_approach: str, max_attempts=2):
        """
        THE CORE OF THE 'REAL AI AGENT' REQUIREMENT.

        This sends the player's answer + full structured context to the AI
        and asks it to decide what happens next. No random.choice(), no
        pre-written branching table — the AI reasons over context every time.
        """
        user_message = textwrap.dedent(f"""
            === RUNNING STORY SUMMARY (structured memory) ===
            {state.summary_for_ai()}

            === MOST RECENT EXCHANGES (for tone & continuity) ===
            {state.recent_history_for_ai()}

            === PLAYER'S LATEST ANSWER ===
            The player was asked: "{state.current_question}"
            The player chose the approach '{chosen_approach}' and answered:
            "{player_answer}"

            Now decide, using everything above, what happens next. Respond
            with ONLY the JSON object described in your instructions.
        """).strip()

        last_error = None
        for attempt in range(max_attempts):
            try:
                raw = self._call_api(user_message)
                scene = self._extract_json(raw)
                scene = self._validate(scene)
                return scene
            except AIAgentError as e:
                last_error = e
                # On a retry, tell the model exactly what went wrong so it
                # can self-correct, instead of blindly repeating the request.
                user_message += (
                    f"\n\n(NOTE: your previous reply was invalid: {e}. "
                    f"Reply again with ONLY a single corrected JSON object.)"
                )
        raise last_error

    def request_opening_scene(self, state: GameState):
        """Special first call: establish the opening mystery from the chosen
        seed elements, still fully generated by the AI (the seed only gives
        it raw ingredients — the actual prose/question is the AI's work)."""
        user_message = textwrap.dedent(f"""
            This is the very first turn of a brand new game. Use the following
            seed ingredients to open the story in your own words — do not
            copy them verbatim, treat them as inspiration:

            Opening hook idea: {state.seed['hook']}
            Setting: {state.seed['setting']}
            Available character names you may introduce as needed (not all at
            once): {', '.join(name for name, _ in state.seed['character_pool'])}
            A twist element you may plant early or save for later: {state.seed['twist']}

            Establish a strong opening mystery, introduce at most one
            character this turn, and ask the player's first meaningful
            question with 4 psychologically distinct choices. Set an initial
            current objective. Respond with ONLY the JSON object described in
            your instructions. (turn 1 of {MIN_TURNS}-{MAX_TURNS})
        """).strip()

        last_error = None
        for attempt in range(2):
            try:
                raw = self._call_api(user_message)
                scene = self._extract_json(raw)
                scene = self._validate(scene)
                return scene
            except AIAgentError as e:
                last_error = e
                user_message += f"\n\n(NOTE: previous reply was invalid: {e}. Reply again with ONLY corrected JSON.)"
        raise last_error
    # ==============================================================================
# 5. STATE UPDATE LOGIC (applying a validated AI scene to the GameState)
# ==============================================================================

def apply_scene_to_state(state: GameState, scene: dict, is_opening=False, player_answer=None, chosen_approach=None):
    """Merge a validated AI response into the persistent GameState."""

    # Record the exchange that just finished (skip on the very first opening
    # scene, since there's no prior question/answer yet).
    if not is_opening:
        state.scene_history.append({
            "situation": state.current_narration,
            "question": state.current_question,
            "answer": player_answer,
            "approach": chosen_approach,
        })

    # Psychological deltas (clamped to keep numbers sane over a long game)
    for dim, delta in scene.get("psychological_interpretation", {}).items():
        if dim in state.psych:
            try:
                state.psych[dim] = max(-20, min(20, state.psych[dim] + int(delta)))
            except (TypeError, ValueError):
                pass

    # New characters
    for name, desc in scene.get("new_character_descriptions", {}).items():
        state.characters.setdefault(name, {"description": desc, "relationship_notes": []})

    # Relationship changes / notes
    for name, note in scene.get("relationship_changes", {}).items():
        entry = state.characters.setdefault(name, {"description": "", "relationship_notes": []})
        entry["relationship_notes"].append(note)

    if scene.get("discovered_clue"):
        state.discovered_clues.append(scene["discovered_clue"])

    if scene.get("important_decision"):
        state.important_decisions.append(scene["important_decision"])

    if scene.get("new_objective"):
        state.current_objective = scene["new_objective"]

    if scene.get("consequence"):
        state.major_consequences.append(scene["consequence"])

    # Update "current" fields for what's now on screen
    state.current_narration = scene["narration"]
    state.current_question = scene["question"]
    state.current_choices = scene["choices"]
    state.current_emotional_tone = scene.get("emotional_tone", "")

    state.turn_count += 1

    if scene["continue_or_end"] == "end":
        state.ended = True
        state.ending_title = scene.get("ending_title")
        state.ending_explanation = scene.get("ending_explanation")


# ==============================================================================
# 6. GUI — DARK CINEMATIC TKINTER INTERFACE
# ==============================================================================

BG = "#0d0f12"
PANEL = "#15181d"
PANEL_ALT = "#1b1f26"
FG = "#d8d8d8"
FG_DIM = "#8a8f98"
ACCENT = "#8b2b2b"       # muted blood-red accent
ACCENT_SOFT = "#5c3d5c"  # muted violet for secondary accents
FONT_TITLE = ("Georgia", 26, "bold")
FONT_SUB = ("Georgia", 12, "italic")
FONT_BODY = ("Georgia", 13)
FONT_QUESTION = ("Georgia", 15, "bold")
FONT_BUTTON = ("Georgia", 11)
FONT_SMALL = ("Georgia", 9)


class ShadowlineApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SHADOWLINE")
        self.geometry("900x680")
        self.configure(bg=BG)
        self.minsize(760, 560)

        self.agent = None
        self.state = None
        self.busy = False

        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)

        self.show_title_screen()

    # -------------------------------------------------------------- utils

    def _clear(self):
        for widget in self.container.winfo_children():
            widget.destroy()

    def _set_busy(self, busy, message="Thinking..."):
        self.busy = busy
        if busy:
            self.status_var.set(message)
        else:
            self.status_var.set("")
        self.update_idletasks()

    # -------------------------------------------------------- title screen

    def show_title_screen(self):
        self._clear()
        frame = tk.Frame(self.container, bg=BG)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="S H A D O W L I N E", font=FONT_TITLE, fg=FG, bg=BG).pack(pady=(70, 4))
        tk.Label(frame, text="a psychological descent, told by an AI that is always watching your choices",
                 font=FONT_SUB, fg=FG_DIM, bg=BG).pack(pady=(0, 40))

        key_frame = tk.Frame(frame, bg=BG)
        key_frame.pack(pady=8)
        tk.Label(key_frame, text="Gemini API Key:", font=FONT_BODY, fg=FG, bg=BG).pack(side="left", padx=(0, 8))
        self.api_key_var = tk.StringVar(value=os.environ.get("GEMINI_API_KEY", ""))
        entry = tk.Entry(key_frame, textvariable=self.api_key_var, show="•", width=42,
                          bg=PANEL_ALT, fg=FG, insertbackground=FG, relief="flat")
        entry.pack(side="left", ipady=4)

        tk.Label(frame, text="(kept in memory only for this session — never saved to disk or hard-coded)",
                 font=FONT_SMALL, fg=FG_DIM, bg=BG).pack(pady=(4, 30))

        btn_frame = tk.Frame(frame, bg=BG)
        btn_frame.pack(pady=10)

        self._make_button(btn_frame, "Begin a New Story", self.start_new_game).pack(pady=6)

        if os.path.exists(SAVE_FILE):
            self._make_button(btn_frame, "Resume Saved Story", self.resume_game).pack(pady=6)

        self.status_var = tk.StringVar(value="")
        tk.Label(frame, textvariable=self.status_var, font=FONT_SMALL, fg=ACCENT, bg=BG).pack(pady=(20, 0))

    def _make_button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, font=FONT_BUTTON,
                          fg=FG, bg=PANEL_ALT, activebackground=ACCENT, activeforeground="#ffffff",
                          relief="flat", padx=18, pady=10, width=28, cursor="hand2")

    def _require_key(self):
        key = self.api_key_var.get().strip()
        if not key:
            messagebox.showwarning("API key required", "Please enter your Gemini API key to continue.\nGet one free at https://aistudio.google.com/apikey")
            return None
        return key

    # ------------------------------------------------------------- start

    def start_new_game(self):
        key = self._require_key()
        if not key:
            return
        self.agent = AIAgent(key)
        self.state = GameState()

        # Atmospheric-only randomness (allowed by spec): pick opening seed.
        self.state.seed = {
            "hook": random.choice(OPENING_HOOKS),
            "setting": random.choice(SETTING_DETAILS),
            "twist": random.choice(TWIST_ELEMENTS),
            "character_pool": random.sample(CHARACTER_ARCHETYPES, k=4),
        }

        self._set_busy(True, "The story is waking up...")
        self.show_story_screen()
        self.after(50, self._fetch_opening_scene)

    def resume_game(self):
        key = self._require_key()
        if not key:
            return
        try:
            self.state = GameState.load()
        except Exception as e:
            messagebox.showerror("Could not load save", f"The save file appears to be corrupted:\n{e}")
            return
        self.agent = AIAgent(key)
        if self.state.ended:
            self.show_end_screen()
        else:
            self.show_story_screen()

    def _fetch_opening_scene(self):
        try:
            scene = self.agent.request_opening_scene(self.state)
            apply_scene_to_state(self.state, scene, is_opening=True)
            self.state.save()
        except AIAgentError as e:
            self._show_ai_error(str(e), retry=self._fetch_opening_scene)
            return
        except Exception:
            self._show_ai_error("Unexpected error:\n" + traceback.format_exc(), retry=self._fetch_opening_scene)
            return
        self._set_busy(False)
        self.render_current_scene()

    # -------------------------------------------------------- story screen

    def show_story_screen(self):
        self._clear()
        outer = tk.Frame(self.container, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=18)

        top_bar = tk.Frame(outer, bg=BG)
        top_bar.pack(fill="x")
        self.turn_label = tk.Label(top_bar, text="", font=FONT_SMALL, fg=FG_DIM, bg=BG)
        self.turn_label.pack(side="left")
        self.tone_label = tk.Label(top_bar, text="", font=FONT_SMALL, fg=ACCENT_SOFT, bg=BG)
        self.tone_label.pack(side="right")

        divider = tk.Frame(outer, bg=ACCENT, height=1)
        divider.pack(fill="x", pady=(6, 14))

        # Narration panel
        self.narration_box = scrolledtext.ScrolledText(
            outer, wrap="word", height=10, font=FONT_BODY, bg=PANEL, fg=FG,
            relief="flat", padx=16, pady=14, insertbackground=FG, bd=0
        )
        self.narration_box.pack(fill="both", expand=False, pady=(0, 14))
        self.narration_box.configure(state="disabled")

        # Question
        self.question_label = tk.Label(outer, text="", font=FONT_QUESTION, fg=FG, bg=BG,
                                        wraplength=820, justify="left", anchor="w")
        self.question_label.pack(fill="x", pady=(0, 14))

        # Choices
        self.choices_frame = tk.Frame(outer, bg=BG)
        self.choices_frame.pack(fill="x")

        # Free text option
        free_frame = tk.Frame(outer, bg=BG)
        free_frame.pack(fill="x", pady=(16, 0))
        tk.Label(free_frame, text="Or, in your own words:", font=FONT_SMALL, fg=FG_DIM, bg=BG).pack(anchor="w")
        entry_row = tk.Frame(free_frame, bg=BG)
        entry_row.pack(fill="x", pady=(4, 0))
        self.free_text_var = tk.StringVar()
        self.free_entry = tk.Entry(entry_row, textvariable=self.free_text_var, font=FONT_BODY,
                                    bg=PANEL_ALT, fg=FG, insertbackground=FG, relief="flat")
        self.free_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.free_entry.bind("<Return>", lambda e: self._submit_free_text())
        tk.Button(entry_row, text="Act", command=self._submit_free_text, font=FONT_BUTTON,
                  fg=FG, bg=PANEL_ALT, activebackground=ACCENT, relief="flat", padx=14).pack(side="left", padx=(8, 0))

        # Status / save row
        bottom_row = tk.Frame(outer, bg=BG)
        bottom_row.pack(fill="x", pady=(14, 0))
        self.status_var = tk.StringVar(value="")
        tk.Label(bottom_row, textvariable=self.status_var, font=FONT_SMALL, fg=ACCENT, bg=BG).pack(side="left")
        tk.Button(bottom_row, text="Save & Quit", command=self._save_and_quit, font=FONT_SMALL,
                  fg=FG_DIM, bg=BG, relief="flat", cursor="hand2").pack(side="right")

    def render_current_scene(self):
        st = self.state
        self.turn_label.config(text=f"Turn {st.turn_count} of an estimated {MIN_TURNS}-{MAX_TURNS}")
        self.tone_label.config(text=st.current_emotional_tone.upper() if st.current_emotional_tone else "")

        self.narration_box.configure(state="normal")
        self.narration_box.delete("1.0", "end")
        self.narration_box.insert("end", st.current_narration)
        self.narration_box.configure(state="disabled")

        self.question_label.config(text=st.current_question)

        for w in self.choices_frame.winfo_children():
            w.destroy()

        for choice in st.current_choices:
            btn = tk.Button(
                self.choices_frame,
                text=f"{choice['label']}",
                font=FONT_BUTTON, fg=FG, bg=PANEL_ALT, activebackground=ACCENT,
                activeforeground="#ffffff", relief="flat", anchor="w", justify="left",
                wraplength=800, padx=14, pady=10, cursor="hand2",
                command=lambda c=choice: self._submit_choice(c["label"], c["approach"]),
            )
            btn.pack(fill="x", pady=4)

        self.free_text_var.set("")
        self.status_var.set("")

    def _submit_free_text(self):
        text = self.free_text_var.get().strip()
        if not text:
            return
        # A free-typed action is tagged as 'continue' by default; the AI will
        # infer the real psychological approach from the text itself.
        self._submit_choice(text, "continue")

    def _submit_choice(self, answer_text, approach):
        if self.busy:
            return
        self._set_busy(True, "The story is responding to what you just did...")
        self._disable_inputs()
        self.after(50, lambda: self._fetch_next_scene(answer_text, approach))

    def _disable_inputs(self):
        for w in self.choices_frame.winfo_children():
            w.configure(state="disabled")
        self.free_entry.configure(state="disabled")

    def _fetch_next_scene(self, answer_text, approach):
        try:
            scene = self.agent.request_next_scene(self.state, answer_text, approach)
            apply_scene_to_state(self.state, scene, is_opening=False,
                                  player_answer=answer_text, chosen_approach=approach)
            self.state.save()
        except AIAgentError as e:
            self._set_busy(False)
            self._show_ai_error(str(e), retry=lambda: self._fetch_next_scene(answer_text, approach))
            return
        except Exception:
            self._set_busy(False)
            self._show_ai_error("Unexpected error:\n" + traceback.format_exc(),
                                 retry=lambda: self._fetch_next_scene(answer_text, approach))
            return

        self._set_busy(False)
        if self.state.ended or self.state.turn_count >= HARD_END_TURN:
            self.show_end_screen()
        else:
            self.render_current_scene()

    def _show_ai_error(self, message, retry):
        """Never crash the game on an AI/API failure — show it plainly and
        let the player retry without losing any progress (rule 17/18)."""
        answer = messagebox.askretrycancel(
            "AI connection problem",
            "The AI agent could not be reached or returned something invalid.\n\n"
            f"{message}\n\n"
            "Your progress is safe and has been saved. Retry now?"
        )
        self._disable_inputs_reenable()
        if answer:
            self._set_busy(True, "Retrying...")
            self.after(50, retry)
        else:
            self.status_var.set("Paused — the AI connection was not retried. You can Save & Quit and resume later.")
            self._reenable_inputs()

    def _disable_inputs_reenable(self):
        pass  # placeholder hook kept for clarity of intent; see below

    def _reenable_inputs(self):
        for w in self.choices_frame.winfo_children():
            try:
                w.configure(state="normal")
            except tk.TclError:
                pass
        self.free_entry.configure(state="normal")

    def _save_and_quit(self):
        try:
            self.state.save()
            messagebox.showinfo("Saved", "Your story has been saved. You can resume it next time you launch the game.")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
        self.destroy()

    # ---------------------------------------------------------- end screen

    def show_end_screen(self):
        self._clear()
        outer = tk.Frame(self.container, bg=BG)
        outer.pack(fill="both", expand=True, padx=30, pady=30)

        tk.Label(outer, text="YOUR STORY HAS ENDED", font=FONT_SUB, fg=FG_DIM, bg=BG).pack(pady=(10, 0))
        tk.Label(outer, text=self.state.ending_title or "THE UNRESOLVED", font=FONT_TITLE, fg=ACCENT, bg=BG).pack(pady=(4, 18))

        box = scrolledtext.ScrolledText(outer, wrap="word", font=FONT_BODY, bg=PANEL, fg=FG,
                                         relief="flat", padx=16, pady=14, bd=0, height=22)
        box.pack(fill="both", expand=True)
        box.insert("end", (self.state.ending_explanation or "") + "\n\n")

        box.insert("end", "— Your Journey —\n\n", "header")
        box.insert("end", f"Total turns: {self.state.turn_count}\n")
        box.insert("end", f"Final objective: {self.state.current_objective or 'unresolved'}\n\n")

        sorted_psych = sorted(self.state.psych.items(), key=lambda kv: kv[1], reverse=True)
        strongest = [f"{k.replace('_',' ')} ({v:+d})" for k, v in sorted_psych if v != 0][:6]
        box.insert("end", "Dominant psychological tendencies: " + (", ".join(strongest) or "balanced / hard to categorize") + "\n\n")

        if self.state.important_decisions:
            box.insert("end", "Key decisions along the way:\n")
            for d in self.state.important_decisions:
                box.insert("end", f"  • {d}\n")
            box.insert("end", "\n")

        if self.state.discovered_clues:
            box.insert("end", "Clues you uncovered:\n")
            for c in self.state.discovered_clues:
                box.insert("end", f"  • {c}\n")
            box.insert("end", "\n")

        if self.state.characters:
            box.insert("end", "People in your story:\n")
            for name, info in self.state.characters.items():
                notes = "; ".join(info.get("relationship_notes", []))
                box.insert("end", f"  • {name}: {info.get('description','')} — {notes}\n")
            box.insert("end", "\n")

        if self.state.major_consequences:
            box.insert("end", "Consequences that shaped your ending:\n")
            for c in self.state.major_consequences:
                box.insert("end", f"  • {c}\n")

        box.configure(state="disabled")

        btn_row = tk.Frame(outer, bg=BG)
        btn_row.pack(pady=18)
        self._make_button(btn_row, "Begin a New Story", self._restart).pack(side="left", padx=6)
        tk.Button(btn_row, text="Quit", command=self.destroy, font=FONT_BUTTON, fg=FG, bg=PANEL_ALT,
                  activebackground=ACCENT, relief="flat", padx=18, pady=10).pack(side="left", padx=6)

        if os.path.exists(SAVE_FILE):
            try:
                os.remove(SAVE_FILE)
            except OSError:
                pass

    def _restart(self):
        self.show_title_screen()


# ==============================================================================
# 7. ENTRY POINT
# ==============================================================================

def main():
    app = ShadowlineApp()
    app.mainloop()


if __name__ == "__main__":
    main()