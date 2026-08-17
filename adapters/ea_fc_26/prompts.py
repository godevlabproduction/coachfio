"""The words FC 26 uses when talking to the vision model.

core/pipeline/stages.py owns the SHAPE of every prompt - what to return, how to
cite evidence, that timestamps are clip-relative, that an empty list is a real
answer. It must not own the game's WORDS. "Home is the TOP row", "jockey with
L2/LT", "switch to your left winger and hold him level with their six-yard box"
are as specific to this game as a schema field is, and they used to sit in the
game-agnostic core alongside the rule "no game ids in /core".

Each fragment is a template. The core formats in the values it knows (how many
images, which timestamp, which question) and never has to know what the sentence
says. Fragments that depend on which side the player is on are baked here, so
the core does not need to know that home is the top row either.
"""
from __future__ import annotations


def fragments(side: str) -> dict[str, str]:
    """Prompt fragments for a player on `side`. Keys are fixed by the core."""
    srow = "TOP" if side == "home" else "BOTTOM"
    sbadge = "LEFT" if side == "home" else "RIGHT"
    obadge = "RIGHT" if side == "home" else "LEFT"

    return {
        # --- reading the scoreboard ------------------------------------------
        "scoreboard_batch": (
            "These are {n} crops of an FC 26 match scoreboard, in order. The "
            "home team is the TOP row and the away team the BOTTOM row. For EACH image "
            "read the two score NUMBERS exactly.\n"
        ),
        "scoreboard_not_a_board": (
            "If an image is not a normal in-match scoreboard (a replay, "
            "a cutscene, a menu), return its entry with i only and omit home/away."
        ),

        # --- re-watching one conceded goal -----------------------------------
        "score_event_deep": (
            "These frames are the seconds LEADING UP TO a goal the opponent scored against "
            f"the '{side}' player at {{time}} (in time order). {{rosters}}"
            f"Coach the '{side}' player on THIS goal specifically. Identify which of THEIR "
            "defenders was at fault (by name if visible, else by role), what broke down in the "
            "sequence, the ROOT CAUSE (the actual mistake), and the EXACT FC 26 fix (the specific "
            "button/stick input or positioning) that would have prevented it. Do not invent names "
            "outside the squads given."
        ),

        # --- the coaching prompts --------------------------------------------
        "coach_intro": (
            f"You are an elite EA Sports FC 26 coach. Watch this ENTIRE match video and coach the "
            f"'{side}' team (the user). This is human-vs-human, so do NOT use the controlled-player "
            f"arrow to pick the user.\n"
        ),
        "identify_team": (
            f"IDENTIFY THE USER'S TEAM FIRST: the scoreboard (top-left) shows HOME on the TOP row and "
            f"AWAY on the BOTTOM - the user is '{side}', the {srow} row. The bottom bar's {sbadge} "
            f"on-ball name badge is the USER'S player; the {obadge} badge is the OPPONENT - never "
            f"credit the opponent's actions to the user. Follow the user's team by KIT colour across "
            f"half-time (ends switch, kit + scoreboard row stay).\n"
        ),
        "player_names_from_badge": (
            f"NAME the user's players (read from the {sbadge} badge); never invent names. Mark any "
            f"meta/formation advice '(meta - verify post-patch)'.\n"
        ),
        "player_names_from_log": (
            "NAME the user's players (use the names in the observations, e.g. 'Cancelo drifted "
            "inside'). "
        ),
        "controls": (
            "CONTROLS: for every mistake/weakness, prescribe the EXACT FC 26 input the "
            "user should have used at that moment, drawn from the knowledge above - e.g. "
            "'manual-switch (Right Stick flick) to your CB before their through-ball', 'jockey "
            "with L2/LT instead of lunging', 'clear with the shoot button (not a pass)'. Be "
            "specific about the button/stick, not vague.\n"
        ),
        # The RULE ("say who, and what the pass is for") is the core's. The
        # example of what that looks like is this game's.
        "outlet_example": (
            "'switch to your left winger and hold him level with "
            "their six-yard box so their back line drops, which opens the space for your "
            "striker' is coachable; 'use the width' is not.\n"
        ),
        "evidence_example": (
            "Do not "
            "say 'pass to the fullback' or 'switch to the winger' unless that player was on "
            "screen and open. If the better option was to wait, say that instead.\n"
        ),
        "envelope_extras": (
            "FORMATION: set 'formation' = the user's shape if you can tell (e.g. '4-2-3-1'), else ''.\n"
        ),
        "coaching_method": (
            "COACHING METHOD: diagnose the recurring PATTERN, give its ROOT CAUSE (why it cost "
            "you), lead with the single BIGGEST fix first, and where the OPPONENT exploited you "
            "name the specific COUNTER. Reference a meta principle, a formation/role tweak, or "
            "the PRO REFERENCE from the knowledge above when it fits.\n"
        ),

        # --- the watch pass ---------------------------------------------------
        "observe_identify": (
            f"  - Identify the '{side}' team's KIT COLOUR (watch their goalkeeper, or their players "
            f"when the {sbadge} badge is active). Follow the team by KIT across half-time: at "
            f"half-time teams switch ENDS (attack the other way) but keep the SAME kit and the SAME "
            f"scoreboard row - never re-identify by pitch side.\n"
        ),
        "observe_roles": (
            "Only if you truly cannot read a name, use the role ('your CB', 'your striker'). "
        ),
        "role_fallback": "('your CB')",
        "timestamp_example": "'... jockey instead. (03:12)' or '... (01:40, 06:05)'",
        "observe_actions": (
            "When a mistake happens, note the DEFENSIVE/attacking action that was needed (e.g. a "
            "manual switch to a specific player, a jockey, a clearance). "
        ),
        "observe_gaps": (
            "Finally, list in 'knowledge_gaps' up to 5 FC 26-SPECIFIC things you saw but are NOT "
            "certain about (a mechanic, animation, player role, UI element, or term) - phrase each "
            "as a question to look up, e.g. 'What does the X role do in FC 26?'."
        ),

        # --- self-learning ----------------------------------------------------
        "research_query": (
            "In EA Sports FC 26 (current title update), {question} "
            "Answer in 1-3 concise factual sentences; if it is not specifically "
            "a real FC 26 thing, reply exactly 'unknown'."
        ),
        "research_label": "FC 26",
    }
