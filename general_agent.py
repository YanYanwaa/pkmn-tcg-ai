"""
general_agent.py

A SIMPLE, DECK-AGNOSTIC agent for the Pokemon TCG sim, intended as a
"better than random" training opponent for a neural network.

Unlike offence.py / defence.py, this agent contains NO hardcoded card IDs.
It scores every choice using generic signals available on any card/pokemon:
    - card_table[id].cardType   (stringified, so we don't need to know every
                                  enum member name — robust to SDK versions)
    - card_table[id].basic / stage2 / ex / megaEx / tera
    - card_table[id].weakness / resistance / energyType
    - Pokemon.hp / Pokemon.energies
    - lib.pokemon_score(...) and lib.prize_count(...) when available

USAGE
-----
    from general_agent import make_general_agent

    my_agent  = make_general_agent(deck1)
    opp_agent = make_general_agent(deck2)

    obs_dict, _ = battle_start(deck1, deck2)
    while obs_dict["current"]["result"] < 0:
        idx = obs_dict["current"]["yourIndex"]
        action = my_agent(obs_dict) if idx == 0 else opp_agent(obs_dict)
        obs_dict = battle_select(action)

Each call to make_general_agent(deck) returns a fresh, independent agent
function with its own turn/prize state, so you can safely instantiate many
of these (e.g. one per opponent deck in a training pool) without them
interfering with each other.

NOTE ON UNKNOWN SDK INTERNALS
------------------------------
I do not have the source of `lib.py` / `sdk.api`, only what's inferable
from your offence.py. Every call into `lib` helpers below is wrapped so
that if a signature doesn't match your actual SDK, the agent falls back
to a simple built-in heuristic instead of crashing. If you see it always
hitting the fallback path, check the corresponding `try` block signature
against your real `lib.py` and adjust.
"""

from collections import defaultdict

from lib import (
    greedy_select_cards,
    prize_count,
    pokemon_score,
    set_card_counts,
    get_card,
    main_option_proc,
)
from sdk.api import (
    AreaType,
    CardType,
    LogType,
    Observation,
    SelectContext,
    OptionType,
    Card,
    Pokemon,
    State,
    all_card_data,
    to_observation_class,
    EnergyType,
)

_all_card = all_card_data()
CARD_TABLE = {c.cardId: c for c in _all_card}


def _ctype_str(card_id: int) -> str:
    """Stringify a card's cardType so we can pattern-match without needing
    to know every CardType enum member that exists in this SDK build."""
    try:
        return str(CARD_TABLE[card_id].cardType)
    except Exception:
        return ""


def _safe_attr(obj, name, default=None):
    try:
        val = getattr(obj, name)
        return default if val is None else val
    except Exception:
        return default


def make_general_agent(deck: list[int]):
    """Return a new stateful agent function bound to `deck`."""

    state_box = {
        "pre_turn_log": [],
        "current_turn_log": [],
        "prize": [],
        "card_counts": defaultdict(int),
    }

    def generic_pokemon_value(pokemon) -> float:
        """Higher = more valuable pokemon to keep / develop / play."""
        try:
            v = pokemon_score(pokemon)
            if v is not None:
                return float(v)
        except Exception:
            pass
        # Fallback: bigger hp + more energy invested + more evolved = better
        hp = _safe_attr(pokemon, "hp", 60) or 60
        energies = _safe_attr(pokemon, "energies", []) or []
        card_id = _safe_attr(pokemon, "id", None)
        stage_bonus = 0
        if card_id is not None and card_id in CARD_TABLE:
            cd = CARD_TABLE[card_id]
            if _safe_attr(cd, "ex", False) or _safe_attr(cd, "megaEx", False) or _safe_attr(cd, "tera", False):
                stage_bonus += 60
            if _safe_attr(cd, "stage2", False):
                stage_bonus += 30
        return hp + len(energies) * 15 + stage_bonus

    def generic_prize_value(pokemon) -> int:
        try:
            v = prize_count(pokemon, True)
            if v is not None:
                return int(v)
        except Exception:
            pass
        card_id = _safe_attr(pokemon, "id", None)
        if card_id is not None and card_id in CARD_TABLE:
            cd = CARD_TABLE[card_id]
            if _safe_attr(cd, "ex", False) or _safe_attr(cd, "megaEx", False) or _safe_attr(cd, "tera", False):
                return 2
        return 1

    def generic_hand_value(card_id: int, my_state) -> float:
        """Score a card sitting in hand / deck / discard, purely from its
        generic type — no card-specific knowledge."""
        if card_id not in CARD_TABLE:
            return 0.0
        cd = CARD_TABLE[card_id]
        ctype = _ctype_str(card_id).upper()

        if "POKEMON" in ctype:
            score = 500.0
            if _safe_attr(cd, "basic", False):
                score += 100
            if _safe_attr(cd, "ex", False) or _safe_attr(cd, "megaEx", False) or _safe_attr(cd, "tera", False):
                score += 150
            if _safe_attr(cd, "stage2", False):
                score += 60
            return score

        if "SUPPORTER" in ctype:
            return 600.0 if not _safe_attr(my_state, "supporterPlayed", False) else -1.0

        if "ENERGY" in ctype:
            # Slightly prefer basic energy over special (more universally usable)
            return 550.0 if "SPECIAL" not in ctype else 450.0

        if "STADIUM" in ctype:
            return 250.0

        if "TOOL" in ctype:
            return 200.0

        # Generic item / trainer
        return 400.0

    def agent(obs_dict: dict) -> list[int]:
        obs = to_observation_class(obs_dict)
        if obs.select is None:
            return deck

        state = obs.current
        select = obs.select
        context = select.context
        my_index = state.yourIndex
        my_state = state.players[my_index]
        op_state = state.players[1 - my_index]

        # --- per-turn bookkeeping, mirrors offence.py's scaffolding ---
        if state.turn == 0:
            state_box["prize"].clear()
            state_box["pre_turn_log"].clear()
            state_box["current_turn_log"].clear()
        else:
            for log in obs.logs:
                state_box["current_turn_log"].append(log)
                if log.type == LogType.TURN_END:
                    state_box["pre_turn_log"] = state_box["current_turn_log"]
                    state_box["current_turn_log"] = []

        try:
            if select.deck is not None:
                set_card_counts(obs, my_index)
                for card in select.deck:
                    state_box["card_counts"][card.id] -= 1
                state_box["prize"].clear()
                for cid in state_box["card_counts"]:
                    for _ in range(state_box["card_counts"][cid]):
                        state_box["prize"].append(cid)
            set_card_counts(obs, my_index)
            for cid in state_box["prize"]:
                state_box["card_counts"][cid] -= 1
        except Exception:
            pass
        deck_counts = state_box["card_counts"]

        my_active = my_state.active[0] if my_state.active else None
        op_active = op_state.active[0] if op_state.active else None
        my_bench = my_state.bench or []
        op_bench = op_state.bench or []

        hand_counts = defaultdict(int)
        for card in my_state.hand:
            hand_counts[card.id] += 1

        def hand_score(card_id: int, _in_progress=frozenset()):
            if card_id in _in_progress:
                return 0.0
            return generic_hand_value(card_id, my_state)

        try:
            main_option_proc(obs, 0, False)
        except Exception:
            pass

        # ---- score every option generically ----
        scores = []
        for o in select.option:
            score = 0.0

            if o.type == OptionType.NUMBER:
                score = o.number

            elif o.type == OptionType.YES:
                score = -1 if context == SelectContext.IS_FIRST else 1

            elif o.type == OptionType.CARD:
                card = get_card(obs, o.area, o.index, o.playerIndex)
                if card is not None:
                    is_pkmn = isinstance(card, Pokemon)
                    hp = _safe_attr(card, "hp", 0) or 0
                    energies = _safe_attr(card, "energies", []) or []

                    if context in (SelectContext.SWITCH, SelectContext.TO_ACTIVE, SelectContext.SETUP_ACTIVE_POKEMON):
                        if o.playerIndex == my_index:
                            score = generic_pokemon_value(card) if is_pkmn else 0
                        elif is_pkmn:
                            # choosing an opponent's pokemon (e.g. gust effect):
                            # prefer high prize value, low remaining hp
                            score = generic_prize_value(card) * 200 - hp

                    elif context == SelectContext.SETUP_BENCH_POKEMON:
                        score = generic_pokemon_value(card) if is_pkmn else 0

                    elif context in (SelectContext.TO_BENCH, SelectContext.TO_HAND):
                        score = hand_score(card.id)
                        if context == SelectContext.TO_HAND:
                            hand_counts[card.id] += 1

                    elif context == SelectContext.DISCARD:
                        score = -hand_score(card.id)

                    elif context in (SelectContext.DAMAGE_COUNTER, SelectContext.DAMAGE_COUNTER_ANY):
                        score = (500 - hp) * generic_prize_value(card)

                    elif context == SelectContext.ATTACH_FROM:
                        # prefer pulling energy from a well-stocked, non-active pokemon
                        score = len(energies) * 10
                        if o.inPlayArea != AreaType.ACTIVE:
                            score += 15

                    elif context == SelectContext.ATTACH_TO:
                        # prefer feeding energy to whoever has the least
                        score = 1000 - len(energies) * 50
                        if o.inPlayArea == AreaType.ACTIVE:
                            score += 300

                    else:
                        score = generic_pokemon_value(card) if is_pkmn else hand_score(card.id)

            elif o.type in (OptionType.ENERGY_CARD, OptionType.ENERGY):
                if o.playerIndex != my_index:
                    score = 20 if o.inPlayArea == AreaType.BENCH else 10
                    card = get_card(obs, o.area, o.index, o.playerIndex)
                    if card is not None and "SPECIAL" in _ctype_str(card.id).upper():
                        score += 10

            elif o.type == OptionType.PLAY:
                card = get_card(obs, AreaType.HAND, o.index, my_index)
                if card is not None:
                    ctype = _ctype_str(card.id).upper()
                    if "POKEMON" in ctype:
                        score = hand_score(card.id)
                        if not my_bench:
                            score += 500  # build the board out first
                    else:
                        score = hand_score(card.id)

            elif o.type == OptionType.ATTACH:
                card = get_card(obs, o.area, o.index, my_index)
                pokemon = get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
                if pokemon is not None:
                    energies = _safe_attr(pokemon, "energies", []) or []
                    score = 1000 - len(energies) * 50
                    if o.inPlayArea == AreaType.ACTIVE:
                        score += 300

            elif o.type == OptionType.EVOLVE:
                pokemon = get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
                score = 5000
                if pokemon is not None:
                    score += len(_safe_attr(pokemon, "energies", []) or []) * 10

            elif o.type == OptionType.ABILITY:
                # Generically favor abilities; no card-specific knowledge here.
                score = 1000

            elif o.type == OptionType.RETREAT:
                if my_active is not None:
                    active_val = generic_pokemon_value(my_active)
                    best_bench_val = max((generic_pokemon_value(p) for p in my_bench), default=-1)
                    hp = _safe_attr(my_active, "hp", 999) or 999
                    if hp <= 40 or best_bench_val > active_val + 30:
                        score = 5000
                    else:
                        score = -1
                else:
                    score = -1

            elif o.type == OptionType.ATTACK:
                # No reliable generic damage number available (attack schema
                # unknown), so approximate: prefer attacks whose id is offered
                # last in the list (commonly the strongest/most-evolved attack
                # in these datasets), same convention used in offence.py.
                attacks = [opt.attackId for opt in select.option if opt.type == OptionType.ATTACK]
                best_attack_id = max(attacks) if attacks else None
                score = 1 if o.attackId == best_attack_id else -1

            scores.append(score)

        # ---- resolve final selection ----
        output = []
        if context in (SelectContext.TO_HAND, SelectContext.TO_BENCH) and select.deck is not None:
            try:
                output = greedy_select_cards(select, obs, hand_counts, hand_score, deck_counts)
            except Exception:
                output = []
        if not output and scores:
            sorted_scores = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
            for i in range(select.maxCount):
                if i >= len(sorted_scores):
                    break
                if (
                    sorted_scores[i][1] >= 0
                    or select.minCount > i
                    or context not in (SelectContext.TO_BENCH, SelectContext.SETUP_BENCH_POKEMON)
                ):
                    output.append(sorted_scores[i][0])

        return output

    return agent