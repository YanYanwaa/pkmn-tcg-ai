import csv
import os
import time
import traceback
from collections import defaultdict

from sdk.game import battle_start, battle_finish, battle_select
from sdk.api import to_observation_class, OptionType, AreaType, SelectContext
from main import agent as main_agent
from setup_agent import agent as setup_agent_fn, setup_progress, card_table
from setup_agent import agent as setup_agent_weak
from setup_agent import EX_WALL_IDS, EX_WALL_EARLY_WARNING_IDS
from original import agent as boss_agent
from defence import defence_agent
from offence import offence_agent
NUM_GAMES = 10000
OUTPUT_DIR = "results"
CSV_PATH = os.path.join(OUTPUT_DIR, "batch_results.csv")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Deck configuration: P0 and P1 can now run different decks. Both
# default to hydrapple.csv (the original mirrored-deck behavior) - change
# DECK_P1_PATH to run an asymmetric matchup, e.g. "decks/threat_test.csv"
# to test P0 against a Crustle/Sylveon-heavy opponent.
DECK_P0_PATH = "deck.csv"
DECK_P1_PATH = "deck.csv"


def _load_deck(path):
    with open(path) as f:
        return [int(line) for line in f.readlines() if line.strip()]


deck_p0 = _load_deck(DECK_P0_PATH)
deck_p1 = _load_deck(DECK_P1_PATH)


def with_deck(agent_fn, deck):
    """Wraps an agent function so it explicitly declares `deck` on the
    initial obs.select-is-None call, instead of whatever the underlying
    agent function would submit on its own. Several agents in this
    project (random_agent in particular) hardcode their own deck-loading
    path rather than using anything passed into battle_start() - this
    wrapper makes deck assignment correct regardless of whether that's
    actually consulted by this harness, since it's a strict override on
    the one call where it matters and a no-op on every other call."""
    def wrapped(obs_dict):
        if obs_dict.get("select") is None:
            return deck
        return agent_fn(obs_dict)
    return wrapped


# Card IDs used for milestone tracking (match setup_agent.py's constants)
MEGANIUM = 710
HYDRAPPLE_EX = 150
OGERPON = 96
CHIKORITA = 917
APPLIN = 149
BAYLEEF = 918
DIPPLIN = 93
TAPU_BULU = 920

MILESTONE_NAMES = [
    "chikorita_or_applin_played",
    "any_evolution",
    "meganium_in_play",
    "meganium_benched",
    "hydrapple_attack_ready",
    "ogerpon_attack_ready",
    "any_attacker_ready",
    "full_target_state",
]


def _ex_wall_threat_in_play(op_active, op_bench):
    """Same detection setup_agent_against_weak.py itself uses (imported
    constants, not re-derived) - True if the evolved wall OR its
    pre-evolution is anywhere on the opponent's board."""
    board = list(op_bench or [])
    if op_active is not None:
        board = board + [op_active]
    wall_on_board = any(p is not None and p.id in EX_WALL_IDS for p in board)
    early_warning = any(p is not None and p.id in EX_WALL_EARLY_WARNING_IDS for p in board)
    return wall_on_board or early_warning


def get_setup_scores(obs_dict):
    """Compute setup_progress for both players from the current obs_dict."""
    obs = to_observation_class(obs_dict)
    state = obs.current

    stadium_id = 0
    for card in (state.stadium or []):
        stadium_id = card.id

    scores = []
    for i in range(2):
        p_state = state.players[i]
        active = [p for p in (p_state.active or []) if p is not None]
        bench = [p for p in (p_state.bench or []) if p is not None]
        field_counts = defaultdict(int)
        for p in active + bench:
            field_counts[p.id] += 1
        scores.append(setup_progress(field_counts, p_state, stadium_id))

    return scores[0], scores[1]


def _milestones(active, bench):
    """Returns a dict of milestone_name -> bool, checked against current board state."""
    field_counts = defaultdict(int)
    for p in active + bench:
        field_counts[p.id] += 1

    meganium_bench = any(p.id == MEGANIUM for p in bench)
    meganium_active = any(p.id == MEGANIUM for p in active)
    hydrapple_ready = any(p.id == HYDRAPPLE_EX and len(p.energies) >= 2 for p in active + bench)
    ogerpon_ready = any(p.id == OGERPON and len(p.energies) >= 3 for p in active + bench)
    attacker_active = any(p.id in (HYDRAPPLE_EX, OGERPON) for p in active)

    return {
        "chikorita_or_applin_played": field_counts[CHIKORITA] > 0 or field_counts[APPLIN] > 0,
        "any_evolution": field_counts[BAYLEEF] > 0 or field_counts[DIPPLIN] > 0,
        "meganium_in_play": meganium_bench or meganium_active,
        "meganium_benched": meganium_bench,
        "hydrapple_attack_ready": hydrapple_ready,
        "ogerpon_attack_ready": ogerpon_ready,
        "any_attacker_ready": hydrapple_ready or ogerpon_ready,
        "full_target_state": meganium_bench and attacker_active,
    }


def _board_serials(active, bench):
    """Set of card serials currently on this player's board (active+bench).
    Used to detect when a Pokemon disappears from play between snapshots -
    the most common cause is a KO, though forced discards from other
    effects would also show up here. Good enough as a proxy for 'my
    Pokemon got knocked out' without needing to parse attack logs."""
    serials = set()
    for p in active + bench:
        s = getattr(p, "serial", None)
        if s is not None:
            serials.add(s)
    return serials


def _get_card_br(obs, area, index, player_index):
    """Minimal standalone card fetch, mirroring the agents' own helper,
    so batch_runner can inspect what a chosen option actually refers to
    without importing agent internals."""
    ps = obs.current.players[player_index]
    if area == AreaType.DECK:
        return obs.select.deck[index]
    elif area == AreaType.HAND:
        return ps.hand[index]
    elif area == AreaType.DISCARD:
        return ps.discard[index]
    elif area == AreaType.ACTIVE:
        return ps.active[index]
    elif area == AreaType.BENCH:
        return ps.bench[index]
    elif area == AreaType.PRIZE:
        return ps.prize[index]
    elif area == AreaType.STADIUM:
        return obs.current.stadium[index]
    elif area == AreaType.LOOKING:
        return obs.current.looking[index]
    return None


def _classify_meganium_trigger(obs, select, action, decider_index, pre_bench_count):
    """Look at the option(s) actually chosen on this decision and
    determine whether it results in Meganium becoming this player's
    active Pokemon, and by what mechanism:
      - evolve_active_empty_bench: evolved into active because the bench
        was empty at the time - effectively unavoidable that turn.
      - evolve_active_with_bench_available: evolved into active despite
        having bench room - the evolve-in-active penalty didn't win out.
      - forced_or_chosen_switch: a CARD selection under SWITCH/TO_ACTIVE
        context picked Meganium (e.g. promoted after a KO, or a
        self-directed retreat swap).
      - setup_active: picked as the starting active during setup -
        shouldn't be legal for a Stage 2 in a real game, flagged in case
        it shows up anyway.
    Returns a label string, or None if this decision isn't related.
    """
    for idx in action:
        if idx < 0 or idx >= len(select.option):
            continue
        o = select.option[idx]
        if o.type == OptionType.EVOLVE and o.inPlayArea == AreaType.ACTIVE:
            evo_card = _get_card_br(obs, AreaType.HAND, o.index, decider_index)
            if evo_card is not None and evo_card.id == MEGANIUM:
                return ("evolve_active_empty_bench" if pre_bench_count == 0
                        else "evolve_active_with_bench_available")
        elif o.type == OptionType.CARD and o.playerIndex == decider_index:
            card = _get_card_br(obs, o.area, o.index, decider_index)
            if card is not None and card.id == MEGANIUM:
                if select.context == SelectContext.SETUP_ACTIVE_POKEMON:
                    return "setup_active"
                elif select.context in (SelectContext.SWITCH, SelectContext.TO_ACTIVE):
                    return "forced_or_chosen_switch"
    return None


def _describe_chosen(obs, select, action, decider_index):
    """Human-readable summary of what was actually chosen on a decision -
    used to log what outscored a lineage PLAY option that was available
    but not taken. Returns a list of {"type": str, "detail": str} dicts,
    one per selected index (almost always just one for MAIN decisions)."""
    chosen = []
    for idx in action:
        if idx < 0 or idx >= len(select.option):
            continue
        o = select.option[idx]
        try:
            if o.type == OptionType.PLAY:
                card = _get_card_br(obs, AreaType.HAND, o.index, decider_index)
                name = card_table[card.id].name if card and card.id in card_table else str(card)
                chosen.append({"type": "PLAY", "detail": name})
            elif o.type == OptionType.EVOLVE:
                evo_card = _get_card_br(obs, AreaType.HAND, o.index, decider_index)
                name = card_table[evo_card.id].name if evo_card and evo_card.id in card_table else str(evo_card)
                chosen.append({"type": "EVOLVE", "detail": f"-> {name}"})
            elif o.type == OptionType.ATTACH:
                pokemon = _get_card_br(obs, o.inPlayArea, o.inPlayIndex, decider_index)
                name = card_table[pokemon.id].name if pokemon and pokemon.id in card_table else str(pokemon)
                chosen.append({"type": "ATTACH", "detail": f"-> {name}"})
            elif o.type == OptionType.ABILITY:
                card = _get_card_br(obs, o.area, o.index, decider_index)
                name = card_table[card.id].name if card and card.id in card_table else str(card)
                chosen.append({"type": "ABILITY", "detail": name})
            elif o.type == OptionType.ATTACK:
                chosen.append({"type": "ATTACK", "detail": f"id={o.attackId}"})
            elif o.type == OptionType.RETREAT:
                chosen.append({"type": "RETREAT", "detail": ""})
            else:
                chosen.append({"type": str(o.type), "detail": ""})
        except Exception:
            chosen.append({"type": "UNKNOWN", "detail": ""})
    return chosen


def _classify_retreat_unavailable(decider_state, active_pokemon, last_retreat_turn_for_player, current_turn):
    """When RETREAT isn't a legal option while a lineage piece is active,
    figure out why - status condition, already used this turn's retreat,
    or genuinely not enough energy attached to pay the retreat cost. Only
    the last one is something scoring changes could actually fix."""
    if getattr(decider_state, "asleep", False) or getattr(decider_state, "paralyzed", False):
        return "status_blocked"
    if last_retreat_turn_for_player == current_turn:
        return "already_retreated_this_turn"
    retreat_cost = 0
    if active_pokemon.id in card_table:
        retreat_cost = card_table[active_pokemon.id].retreatCost or 0
    if len(active_pokemon.energies) < retreat_cost:
        return "insufficient_energy"
    return "other_unknown"


def run_game(agent_p0, agent_p1, deck_p0, deck_p1):
    wrapped_p0 = with_deck(agent_p0, deck_p0)
    wrapped_p1 = with_deck(agent_p1, deck_p1)

    obs_dict, _ = battle_start(deck_p0, deck_p1)
    peak_setup = [0, 0]
    milestone_turns = [{}, {}]       # index 0 = P0, index 1 = P1
    milestone_state = [{}, {}]       # last-seen bool per milestone, per player
    milestone_regressed = [{}, {}]   # name -> True if it was reached, then later false again
    milestone_regress_turn = [{}, {}]  # name -> turn the regression first happened
    ko_counts = [0, 0]               # own Pokemon lost from board, per player
    prev_serials = [None, None]
    prev_active_id = [None, None]        # last known active card id, per player
    last_meganium_trigger = [None, None]  # classification of each player's most recent decision
    meganium_active_events = [[], []]     # list of {"turn": int, "trigger": str}, per player

    # --- Chikorita/Bayleef/Meganium lineage tracking, for classifying
    # WHY meganium_in_play was never reached in a given game ---
    hand_seen_precursor = [False, False]  # Chikorita/Bayleef/Meganium ever appeared in hand
    line_serial_id = [{}, {}]             # serial -> latest known id, for lineage cards on the field
    line_serial_ko = [False, False]       # a lineage serial was lost from the field before reaching Meganium
    lineage_active_tracker = [None, None]  # tracks the currently-active Chikorita/Bayleef's exposure history
    lineage_ko_details = [[], []]         # classified details for each lineage KO event, per player
    last_retreat_turn = [None, None]      # turn number each player last actually executed a RETREAT

    # Every time a PLAY option for Chikorita/Bayleef/Meganium was legal
    # but NOT the option chosen, on a P0 decision - records what won
    # instead, for diagnosing "drawn_never_played" failures.
    foregone_lineage_plays = []

    # --- EX-wall (Crustle/Sylveon) response tracking, P0's perspective
    # only - measures the wall-counter strategy directly, independent of
    # win/loss, since setup_agent deliberately avoids fighting most of
    # the time. See test_ex_wall.py for the original standalone version
    # of this same idea; this folds it into the full milestone-tracking
    # batch run instead of a separate script. ---
    ex_wall_threat_first_turn = None
    ex_wall_counter_ready_turn = None   # first turn (after threat_first_turn)
                                         # P0 has Tapu Bulu in play OR Dipplin active
    ex_wall_flee_moments = 0
    ex_wall_flee_successes = 0
    ex_wall_flee_pending = False
    ex_wall_flee_never_resolved = False

    final_turn = 0

    while True:
        if obs_dict["current"]["result"] >= 0:
            break

        obs = to_observation_class(obs_dict)
        state = obs.current
        final_turn = state.turn  # track the most recent turn number we saw

        p0_score, p1_score = get_setup_scores(obs_dict)
        peak_setup[0] = max(peak_setup[0], p0_score)
        peak_setup[1] = max(peak_setup[1], p1_score)

        for i in range(2):
            p_state = state.players[i]
            active = [p for p in (p_state.active or []) if p is not None]
            bench = [p for p in (p_state.bench or []) if p is not None]

            # --- own-KO tracking ---
            current_serials = _board_serials(active, bench)
            lost_serials = set()
            if prev_serials[i] is not None:
                lost_serials = prev_serials[i] - current_serials
                ko_counts[i] += len(lost_serials)
            prev_serials[i] = current_serials

            # --- Chikorita/Bayleef/Meganium lineage tracking ---
            # A lost serial that was last known to be part of this
            # lineage, and hadn't yet become Meganium, means that copy
            # was knocked out (or otherwise discarded) before finishing
            # its evolution chain.
            for s in lost_serials:
                if s in line_serial_id[i] and line_serial_id[i][s] != MEGANIUM:
                    line_serial_ko[i] = True
                    tracker = lineage_active_tracker[i]
                    if tracker is not None and tracker["serial"] == s:
                        if tracker["decisions"] == 0:
                            classification = "zero_decisions_before_ko"
                        elif tracker["retreat_legal_decisions"] == 0:
                            classification = "retreat_never_legal"
                        else:
                            classification = "retreat_available_not_taken"
                        lineage_ko_details[i].append({
                            "turn": state.turn,
                            "id": tracker["id"],
                            "decisions_while_active": tracker["decisions"],
                            "retreat_legal_decisions": tracker["retreat_legal_decisions"],
                            "classification": classification,
                            "illegal_reasons": dict(tracker.get("illegal_reasons", {})),
                        })
                        lineage_active_tracker[i] = None
                    else:
                        # Was never this player's own active Pokemon when
                        # lost (e.g. sniped off the bench) - a different
                        # failure mode from dying in the active slot.
                        lineage_ko_details[i].append({
                            "turn": state.turn,
                            "id": line_serial_id[i][s],
                            "decisions_while_active": 0,
                            "retreat_legal_decisions": 0,
                            "classification": "lost_from_bench",
                        })
            for p in active + bench:
                if p.id in (CHIKORITA, BAYLEEF, MEGANIUM):
                    s = getattr(p, "serial", None)
                    if s is not None:
                        line_serial_id[i][s] = p.id
            for card in (p_state.hand or []):
                if card.id in (CHIKORITA, BAYLEEF, MEGANIUM):
                    hand_seen_precursor[i] = True

            # --- Meganium-active transition tracking ---
            current_active_id = active[0].id if active else None
            if current_active_id == MEGANIUM and prev_active_id[i] != MEGANIUM:
                meganium_active_events[i].append({
                    "turn": state.turn,
                    "trigger": last_meganium_trigger[i] or "unknown",
                })
            prev_active_id[i] = current_active_id

            # --- milestone tracking, now including regression ---
            current_milestones = _milestones(active, bench)
            for name, reached in current_milestones.items():
                if reached and name not in milestone_turns[i]:
                    milestone_turns[i][name] = state.turn
                prev_reached = milestone_state[i].get(name, False)
                if prev_reached and not reached:
                    milestone_regressed[i][name] = True
                    if name not in milestone_regress_turn[i]:
                        milestone_regress_turn[i][name] = state.turn
                milestone_state[i][name] = reached

        # --- EX-wall response tracking (P0's perspective) ---
        p0_state = state.players[0]
        op_state_ex = state.players[1]
        p0_active_ex = [p for p in (p0_state.active or []) if p is not None]
        p0_bench_ex = [p for p in (p0_state.bench or []) if p is not None]
        op_active_list_ex = [p for p in (op_state_ex.active or []) if p is not None]
        op_active_ex = op_active_list_ex[0] if op_active_list_ex else None
        op_bench_ex = [p for p in (op_state_ex.bench or []) if p is not None]

        threat_in_play = _ex_wall_threat_in_play(op_active_ex, op_bench_ex)
        if threat_in_play and ex_wall_threat_first_turn is None:
            ex_wall_threat_first_turn = state.turn

        p0_active_pk = p0_active_ex[0] if p0_active_ex else None
        have_tapu_bulu = any(p.id == TAPU_BULU for p in p0_active_ex + p0_bench_ex)
        dipplin_active = p0_active_pk is not None and p0_active_pk.id == DIPPLIN
        if (have_tapu_bulu or dipplin_active) and ex_wall_counter_ready_turn is None \
                and ex_wall_threat_first_turn is not None:
            ex_wall_counter_ready_turn = state.turn

        wall_is_active_ex = op_active_ex is not None and op_active_ex.id in EX_WALL_IDS
        my_ex_attacker_active = p0_active_pk is not None and p0_active_pk.id in (HYDRAPPLE_EX, OGERPON)

        if wall_is_active_ex and my_ex_attacker_active:
            if not ex_wall_flee_pending:
                ex_wall_flee_moments += 1
                ex_wall_flee_pending = True
        else:
            if ex_wall_flee_pending:
                if wall_is_active_ex and not my_ex_attacker_active:
                    ex_wall_flee_successes += 1
                ex_wall_flee_pending = False

        index = obs_dict["current"]["yourIndex"]
        active_agent = wrapped_p0 if index == 0 else wrapped_p1

        # Bench size right before this decision - used to tell an
        # unavoidable evolve-into-active (empty bench) apart from one
        # made despite having somewhere else to put it.
        decider_state = state.players[index]
        decider_bench_count = len([p for p in (decider_state.bench or []) if p is not None])

        # Track this player's exposure history for whatever
        # Chikorita/Bayleef is currently their active Pokemon: how many
        # of their own decisions passed while it sat active, and how
        # many of those had a legal RETREAT option. Lets a later KO be
        # classified as "never got a chance to react", "retreat wasn't
        # legal", or "retreat was available but not taken".
        decider_active_list = [p for p in (decider_state.active or []) if p is not None]
        decider_active = decider_active_list[0] if decider_active_list else None
        if decider_active is not None and decider_active.id in (CHIKORITA, BAYLEEF):
            s = getattr(decider_active, "serial", None)
            tracker = lineage_active_tracker[index]
            if tracker is None or tracker.get("serial") != s:
                tracker = {"serial": s, "id": decider_active.id,
                           "decisions": 0, "retreat_legal_decisions": 0,
                           "illegal_reasons": defaultdict(int)}
                lineage_active_tracker[index] = tracker
            tracker["decisions"] += 1
            if any(o.type == OptionType.RETREAT for o in obs.select.option):
                tracker["retreat_legal_decisions"] += 1
            else:
                reason = _classify_retreat_unavailable(
                    decider_state, decider_active, last_retreat_turn[index], state.turn
                )
                tracker["illegal_reasons"][reason] += 1
        else:
            # Active is no longer a raw lineage piece - either it evolved
            # onward (success, not a KO) or something else is active now.
            # Either way, stop tracking; a fresh piece gets fresh tracking
            # if/when it becomes active.
            lineage_active_tracker[index] = None

        action = active_agent(obs_dict)
        last_meganium_trigger[index] = _classify_meganium_trigger(
            obs, obs.select, action, index, decider_bench_count
        )

        # Record if this decision's chosen action was itself a RETREAT -
        # needed by _classify_retreat_unavailable on future decisions this
        # same turn, to recognize "already used this turn's retreat".
        for idx in action:
            if 0 <= idx < len(obs.select.option) and obs.select.option[idx].type == OptionType.RETREAT:
                last_retreat_turn[index] = state.turn
                break

        if index == 0:
            for opt_idx, o in enumerate(obs.select.option):
                if o.type == OptionType.PLAY:
                    card = _get_card_br(obs, AreaType.HAND, o.index, index)
                    if card is not None and card.id in (CHIKORITA, BAYLEEF, MEGANIUM) and opt_idx not in action:
                        foregone_lineage_plays.append({
                            "turn": state.turn,
                            "card": card_table[card.id].name if card.id in card_table else card.id,
                            "chosen_instead": _describe_chosen(obs, obs.select, action, index),
                        })

        obs_dict.pop("search_begin_input", None)
        obs_dict = battle_select(action)

    if ex_wall_flee_pending:
        ex_wall_flee_never_resolved = True

    result = obs_dict["current"]["result"]
    battle_finish()

    # Only meaningful for a player who never got Meganium into play at all -
    # classifies why, so we can tell a search/draw problem apart from a
    # survival-while-transitional problem.
    meganium_failure_bucket = [None, None]
    for i in range(2):
        if "meganium_in_play" not in milestone_turns[i]:
            if not hand_seen_precursor[i] and not line_serial_id[i]:
                bucket = "never_drawn"
            elif hand_seen_precursor[i] and not line_serial_id[i]:
                bucket = "drawn_never_played"
            elif line_serial_ko[i]:
                bucket = "played_then_ko_before_meganium"
            else:
                bucket = "played_stuck_never_evolved"
            meganium_failure_bucket[i] = bucket

    ex_wall_metrics = {
        "threat_first_turn": ex_wall_threat_first_turn,
        "counter_ready_turn": ex_wall_counter_ready_turn,
        "flee_moments": ex_wall_flee_moments,
        "flee_successes": ex_wall_flee_successes,
        "flee_never_resolved": ex_wall_flee_never_resolved,
    }

    return (result, peak_setup[0], peak_setup[1],
            milestone_turns[0], milestone_turns[1],
            final_turn, ko_counts[0], ko_counts[1],
            milestone_regressed[0], milestone_regressed[1],
            milestone_regress_turn[0], milestone_regress_turn[1],
            meganium_active_events[0], meganium_active_events[1],
            meganium_failure_bucket[0], meganium_failure_bucket[1],
            foregone_lineage_plays,
            lineage_ko_details[0], lineage_ko_details[1],
            ex_wall_metrics)


def main():
    # Swap these to change who plays which side
    agent_p0 = main_agent
    agent_p1 = boss_agent

    fieldnames = (["game", "winner", "game_length", "peak_setup_p0", "peak_setup_p1",
                   "ko_count_p0", "ko_count_p1",
                   "p0_meganium_active_events", "p1_meganium_active_events",
                   "p0_meganium_failure_reason", "p1_meganium_failure_reason"]
                  + [f"p0_turn_{m}" for m in MILESTONE_NAMES]
                  + [f"p1_turn_{m}" for m in MILESTONE_NAMES]
                  + [f"p0_regressed_{m}" for m in MILESTONE_NAMES]
                  + [f"p1_regressed_{m}" for m in MILESTONE_NAMES])
    p0_wins = 0
    p1_wins = 0
    other = 0
    crashes = 0

    setup_p0_total = 0
    setup_p1_total = 0
    completed_games = 0

    milestone_totals_p0 = defaultdict(list)
    milestone_totals_p1 = defaultdict(list)
    milestone_regress_counts_p0 = defaultdict(int)
    milestone_regress_counts_p1 = defaultdict(int)

    # Terminal = regression happened at/right before the game's last
    # recorded snapshot - essentially simultaneous with the loss, not a
    # separate preventable event. Mid-game = regression happened with at
    # least one more turn cycle still to play afterward.
    TERMINAL_TURN_GAP = 2  # turn numbers step by ~2 per full turn cycle
    regress_terminal_p0 = defaultdict(int)
    regress_midgame_p0 = defaultdict(int)
    regress_terminal_p1 = defaultdict(int)
    regress_midgame_p1 = defaultdict(int)

    # Regressions split by whether that player actually won or lost the game
    regress_p0_in_wins = defaultdict(int)
    regress_p0_in_losses = defaultdict(int)
    regress_p1_in_wins = defaultdict(int)
    regress_p1_in_losses = defaultdict(int)

    # --- game-length tracking ---
    game_lengths_all = []
    game_lengths_p0_win = []
    game_lengths_p1_win = []
    game_lengths_p0_reached_full = []
    game_lengths_p0_never_full = []

    # --- KO tracking ---
    ko_p0_total = 0
    ko_p1_total = 0
    ko_p0_when_reached_full = []
    ko_p0_when_never_full = []

    # --- Meganium-active trigger tracking ---
    meganium_event_count_p0 = 0
    meganium_event_count_p1 = 0
    meganium_trigger_counts_p0 = defaultdict(int)
    meganium_trigger_counts_p1 = defaultdict(int)
    games_with_meganium_active_p0 = 0
    games_with_meganium_active_p1 = 0

    # --- meganium_in_play failure classification ---
    meganium_failure_counts_p0 = defaultdict(int)
    meganium_failure_counts_p1 = defaultdict(int)
    meganium_failure_total_p0 = 0
    meganium_failure_total_p1 = 0

    # --- foregone lineage-play diagnostics, for drawn_never_played games ---
    foregone_type_counts = defaultdict(int)      # what kind of option won instead
    foregone_play_detail_counts = defaultdict(int)  # specific card name, when a PLAY won
    foregone_event_total = 0
    foregone_example_games = []   # a handful of full per-game logs to eyeball
    MAX_FOREGONE_EXAMPLES = 5

    # --- lineage-KO classification, for played_then_ko_before_meganium games ---
    lineage_ko_class_counts_p0 = defaultdict(int)
    lineage_ko_class_counts_p1 = defaultdict(int)
    lineage_ko_event_total_p0 = 0
    lineage_ko_event_total_p1 = 0

    # --- why retreat wasn't legal, restricted to retreat_never_legal events ---
    retreat_illegal_reason_counts_p0 = defaultdict(int)
    retreat_illegal_reason_counts_p1 = defaultdict(int)
    retreat_illegal_reason_total_p0 = 0
    retreat_illegal_reason_total_p1 = 0

    # --- EX-wall (Crustle/Sylveon) response tracking, P0's perspective ---
    ex_wall_games_with_threat = 0
    ex_wall_games_with_counter = 0
    ex_wall_response_lags = []
    ex_wall_total_flee_moments = 0
    ex_wall_total_flee_successes = 0
    ex_wall_total_flee_never_resolved = 0

    start_time = time.time()

    with open(CSV_PATH, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for i in range(NUM_GAMES):
            try:
                (result, peak_p0, peak_p1, milestones_p0, milestones_p1,
                 game_length, ko_p0, ko_p1,
                 regressed_p0, regressed_p1,
                 regress_turn_p0, regress_turn_p1,
                 meganium_events_p0, meganium_events_p1,
                 meganium_failure_p0, meganium_failure_p1,
                 foregone_plays,
                 lineage_ko_p0, lineage_ko_p1,
                 ex_wall_metrics) = run_game(agent_p0, agent_p1, deck_p0, deck_p1)
            except Exception as e:
                print(f"Game {i} crashed:")
                traceback.print_exc()
                crashes += 1
                continue

            if result == 0:
                p0_wins += 1
                game_lengths_p0_win.append(game_length)
            elif result == 1:
                p1_wins += 1
                game_lengths_p1_win.append(game_length)
            else:
                other += 1

            setup_p0_total += peak_p0
            setup_p1_total += peak_p1
            completed_games += 1

            if ex_wall_metrics["threat_first_turn"] is not None:
                ex_wall_games_with_threat += 1
                if ex_wall_metrics["counter_ready_turn"] is not None:
                    ex_wall_games_with_counter += 1
                    ex_wall_response_lags.append(
                        ex_wall_metrics["counter_ready_turn"] - ex_wall_metrics["threat_first_turn"]
                    )
            ex_wall_total_flee_moments += ex_wall_metrics["flee_moments"]
            ex_wall_total_flee_successes += ex_wall_metrics["flee_successes"]
            if ex_wall_metrics["flee_never_resolved"]:
                ex_wall_total_flee_never_resolved += 1

            game_lengths_all.append(game_length)
            reached_full = "full_target_state" in milestones_p0
            if reached_full:
                game_lengths_p0_reached_full.append(game_length)
                ko_p0_when_reached_full.append(ko_p0)
            else:
                game_lengths_p0_never_full.append(game_length)
                ko_p0_when_never_full.append(ko_p0)

            ko_p0_total += ko_p0
            ko_p1_total += ko_p1

            if meganium_events_p0:
                games_with_meganium_active_p0 += 1
            if meganium_events_p1:
                games_with_meganium_active_p1 += 1
            for ev in meganium_events_p0:
                meganium_event_count_p0 += 1
                meganium_trigger_counts_p0[ev["trigger"]] += 1
            for ev in meganium_events_p1:
                meganium_event_count_p1 += 1
                meganium_trigger_counts_p1[ev["trigger"]] += 1

            if meganium_failure_p0 is not None:
                meganium_failure_total_p0 += 1
                meganium_failure_counts_p0[meganium_failure_p0] += 1
            if meganium_failure_p1 is not None:
                meganium_failure_total_p1 += 1
                meganium_failure_counts_p1[meganium_failure_p1] += 1

            if meganium_failure_p0 == "drawn_never_played" and foregone_plays:
                for ev in foregone_plays:
                    foregone_event_total += 1
                    for chosen in ev["chosen_instead"]:
                        foregone_type_counts[chosen["type"]] += 1
                        if chosen["type"] == "PLAY":
                            foregone_play_detail_counts[chosen["detail"]] += 1
                    if not ev["chosen_instead"]:
                        foregone_type_counts["(nothing selected)"] += 1
                if len(foregone_example_games) < MAX_FOREGONE_EXAMPLES:
                    foregone_example_games.append((i, foregone_plays))

            if meganium_failure_p0 == "played_then_ko_before_meganium":
                for ev in lineage_ko_p0:
                    lineage_ko_event_total_p0 += 1
                    lineage_ko_class_counts_p0[ev["classification"]] += 1
                    if ev["classification"] == "retreat_never_legal":
                        for reason, count in ev.get("illegal_reasons", {}).items():
                            retreat_illegal_reason_counts_p0[reason] += count
                            retreat_illegal_reason_total_p0 += count
            if meganium_failure_p1 == "played_then_ko_before_meganium":
                for ev in lineage_ko_p1:
                    lineage_ko_event_total_p1 += 1
                    lineage_ko_class_counts_p1[ev["classification"]] += 1
                    if ev["classification"] == "retreat_never_legal":
                        for reason, count in ev.get("illegal_reasons", {}).items():
                            retreat_illegal_reason_counts_p1[reason] += count
                            retreat_illegal_reason_total_p1 += count

            for name in MILESTONE_NAMES:
                if name in milestones_p0:
                    milestone_totals_p0[name].append(milestones_p0[name])
                if name in milestones_p1:
                    milestone_totals_p1[name].append(milestones_p1[name])
                if regressed_p0.get(name):
                    milestone_regress_counts_p0[name] += 1
                    turns_left = game_length - regress_turn_p0.get(name, game_length)
                    if turns_left < TERMINAL_TURN_GAP:
                        regress_terminal_p0[name] += 1
                    else:
                        regress_midgame_p0[name] += 1
                    if result == 0:
                        regress_p0_in_wins[name] += 1
                    else:
                        regress_p0_in_losses[name] += 1
                if regressed_p1.get(name):
                    milestone_regress_counts_p1[name] += 1
                    turns_left = game_length - regress_turn_p1.get(name, game_length)
                    if turns_left < TERMINAL_TURN_GAP:
                        regress_terminal_p1[name] += 1
                    else:
                        regress_midgame_p1[name] += 1
                    if result == 1:
                        regress_p1_in_wins[name] += 1
                    else:
                        regress_p1_in_losses[name] += 1

            row = {
                "game": i,
                "winner": result,
                "game_length": game_length,
                "peak_setup_p0": peak_p0,
                "peak_setup_p1": peak_p1,
                "ko_count_p0": ko_p0,
                "ko_count_p1": ko_p1,
                "p0_meganium_active_events": ";".join(
                    f"{ev['turn']}:{ev['trigger']}" for ev in meganium_events_p0),
                "p1_meganium_active_events": ";".join(
                    f"{ev['turn']}:{ev['trigger']}" for ev in meganium_events_p1),
                "p0_meganium_failure_reason": meganium_failure_p0 or "",
                "p1_meganium_failure_reason": meganium_failure_p1 or "",
            }
            for name in MILESTONE_NAMES:
                row[f"p0_turn_{name}"] = milestones_p0.get(name, "")
                row[f"p1_turn_{name}"] = milestones_p1.get(name, "")
                row[f"p0_regressed_{name}"] = regressed_p0.get(name, False)
                row[f"p1_regressed_{name}"] = regressed_p1.get(name, False)
            writer.writerow(row)
            csvfile.flush()

            if (i + 1) % 50 == 0:
                elapsed = time.time() - start_time
                avg_p0_so_far = setup_p0_total / completed_games if completed_games else 0
                avg_p1_so_far = setup_p1_total / completed_games if completed_games else 0
                avg_len_so_far = sum(game_lengths_all) / len(game_lengths_all) if game_lengths_all else 0
                avg_ko_p0_so_far = ko_p0_total / completed_games if completed_games else 0
                avg_ko_p1_so_far = ko_p1_total / completed_games if completed_games else 0
                #print(f"[{i + 1}/{NUM_GAMES}] P0 wins: {p0_wins} | P1 wins: {p1_wins} | "
                #      f"other: {other} | crashes: {crashes} | "
                #      f"avg setup P0: {avg_p0_so_far:.1f} | avg setup P1: {avg_p1_so_far:.1f} | "
                #      f"avg game length: {avg_len_so_far:.1f} | "
                #      f"avg KOs P0: {avg_ko_p0_so_far:.2f} | avg KOs P1: {avg_ko_p1_so_far:.2f} | "
                #      f"{elapsed:.1f}s elapsed")

    avg_setup_p0 = setup_p0_total / completed_games if completed_games else 0
    avg_setup_p1 = setup_p1_total / completed_games if completed_games else 0

    def _avg(vals):
        return sum(vals) / len(vals) if vals else float("nan")

    print("=== FINAL RESULTS ===")
    print(f"P0 ({agent_p0.__module__}.{agent_p0.__name__}) wins: {p0_wins}  [deck: {DECK_P0_PATH}]")
    print(f"P1 ({agent_p1.__module__}.{agent_p1.__name__}) wins: {p1_wins}  [deck: {DECK_P1_PATH}]")
    print(f"Other/draws: {other}")
    print(f"Crashed games: {crashes}")
    print(f"Completed games: {completed_games}")
    print(f"Average peak setup score - P0: {avg_setup_p0:.1f}")
    print(f"Average peak setup score - P1: {avg_setup_p1:.1f}")

    #print("\n=== GAME LENGTH ===")
    #print(f"Average game length (all games): {_avg(game_lengths_all):.1f} turns")
    #print(f"Average game length (P0 wins):    {_avg(game_lengths_p0_win):.1f} turns  ({len(game_lengths_p0_win)} games)")
    #print(f"Average game length (P1 wins):    {_avg(game_lengths_p1_win):.1f} turns  ({len(game_lengths_p1_win)} games)")
    #print(f"Average game length (P0 reached full_target_state): {_avg(game_lengths_p0_reached_full):.1f} turns  "
    #      f"({len(game_lengths_p0_reached_full)} games)")
    #print(f"Average game length (P0 never reached full_target_state): {_avg(game_lengths_p0_never_full):.1f} turns  "
    #      f"({len(game_lengths_p0_never_full)} games)")

    #print("\n=== OWN-POKEMON KO TRACKING ===")
    #print(f"Average own-Pokemon KOs per game - P0: {ko_p0_total / completed_games if completed_games else 0:.2f}")
    #print(f"Average own-Pokemon KOs per game - P1: {ko_p1_total / completed_games if completed_games else 0:.2f}")
    #print(f"Average own-Pokemon KOs - P0 games that REACHED full_target_state:  {_avg(ko_p0_when_reached_full):.2f}  "
    #      f"({len(ko_p0_when_reached_full)} games)")
    #print(f"Average own-Pokemon KOs - P0 games that NEVER reached full_target_state: {_avg(ko_p0_when_never_full):.2f}  "
    #      f"({len(ko_p0_when_never_full)} games)")

    #def print_meganium_triggers(label, games_with_active, event_count, trigger_counts):
    #    print(f"\n=== MEGANIUM-ACTIVE TRIGGERS - {label} ===")
    #    print(f"  Games where Meganium was active at least once: {games_with_active}/{completed_games}")
    #    print(f"  Total times Meganium became active: {event_count}")
    #    if event_count:
    #        for trigger, count in sorted(trigger_counts.items(), key=lambda x: -x[1]):
    #            pct = 100 * count / event_count
    #            print(f"    {trigger:<35} {count:>6}  ({pct:5.1f}% of events)")
    #    print("  (evolve_active_empty_bench: no bench slot existed yet - unavoidable that turn.")
    #    print("   evolve_active_with_bench_available: evolved into active despite bench room -")
    #    print("   the evolve-in-active penalty didn't win. forced_or_chosen_switch: promoted")
    #    print("   into active via a SWITCH/TO_ACTIVE decision, e.g. after a KO.)")

    #print_meganium_triggers("P0 (main.agent)", games_with_meganium_active_p0,
    #                         meganium_event_count_p0, meganium_trigger_counts_p0)
    #print_meganium_triggers("P1 (boss.agent)", games_with_meganium_active_p1,
    #                         meganium_event_count_p1, meganium_trigger_counts_p1)

    #def print_meganium_failures(label, total, counts):
    #    print(f"\n=== WHY meganium_in_play WAS NEVER REACHED - {label} ===")
    #    print(f"  Games where it was never reached: {total}/{completed_games}")
    #    if total:
    #        for bucket, count in sorted(counts.items(), key=lambda x: -x[1]):
    #            pct = 100 * count / total
    #            print(f"    {bucket:<35} {count:>6}  ({pct:5.1f}% of failures)")
    #    print("  (never_drawn: Chikorita/Bayleef/Meganium never appeared in hand all game -")
    #    print("   a draw/search reliability problem. drawn_never_played: appeared in hand but")
    #    print("   was never put into play. played_then_ko_before_meganium: made it to the")
    #    print("   field but that copy was lost before finishing its evolution chain - a")
    #    print("   survival-while-transitional problem. played_stuck_never_evolved: stayed on")
    #    print("   the field the whole game but never evolved further - likely missing the")
    #    print("   next piece (Bayleef/Rare Candy/Meganium) rather than dying.)")

    #print_meganium_failures("P0 (main.agent)", meganium_failure_total_p0, meganium_failure_counts_p0)
    #print_meganium_failures("P1 (boss.agent)", meganium_failure_total_p1, meganium_failure_counts_p1)

    #print(f"\n=== FOREGONE LINEAGE PLAYS - P0, games classified drawn_never_played ===")
    #print(f"  Total instances (a Chikorita/Bayleef/Meganium PLAY was legal but not taken): {foregone_event_total}")
    #if foregone_event_total:
    #    print("  What won instead, by option type:")
    #    for opt_type, count in sorted(foregone_type_counts.items(), key=lambda x: -x[1]):
    #        pct = 100 * count / foregone_event_total
    #        print(f"    {opt_type:<20} {count:>6}  ({pct:5.1f}%)")
    #    if foregone_play_detail_counts:
    #        print("  When a PLAY won instead, which card:")
    #        for card_name, count in sorted(foregone_play_detail_counts.items(), key=lambda x: -x[1])[:15]:
    #            print(f"    {card_name:<35} {count:>6}")

    #if foregone_example_games:
    #    print(f"\n  --- {len(foregone_example_games)} example game(s), full turn-by-turn log ---")
    #    for game_idx, events in foregone_example_games:
    #        print(f"  Game {game_idx}:")
    #        for ev in events:
    #            chosen_str = ", ".join(
    #                f"{c['type']} {c['detail']}".strip() for c in ev["chosen_instead"]
    #            ) or "(nothing selected)"
    #            print(f"    turn {ev['turn']:>3}: could have played {ev['card']:<20} -> chose {chosen_str}")

    #def print_lineage_ko(label, total, counts):
    #    print(f"\n=== LINEAGE KO CLASSIFICATION - {label}, games classified played_then_ko_before_meganium ===")
    #    print(f"  Total lineage KO events: {total}")
    #    if total:
    #        for cls, count in sorted(counts.items(), key=lambda x: -x[1]):
    #            pct = 100 * count / total
    #           print(f"    {cls:<30} {count:>6}  ({pct:5.1f}%)")
    #    print("  (zero_decisions_before_ko: the piece became active and was knocked out before")
    #    print("   our own next decision - no chance to react at all. retreat_never_legal: we")
     #   print("   had one or more decisions while it was active, but RETREAT was never a legal")
    #    print("   option in any of them. retreat_available_not_taken: RETREAT was legal on at")
    #    print("   least one decision while it was active and it still wasn't taken before the")
    #    print("   KO - a real, actionable gap. lost_from_bench: it wasn't even active when")
    #    print("   lost, e.g. sniped by a bench-hitting attack.)")

    #print_lineage_ko("P0 (main.agent)", lineage_ko_event_total_p0, lineage_ko_class_counts_p0)
    #print_lineage_ko("P1 (boss.agent)", lineage_ko_event_total_p1, lineage_ko_class_counts_p1)

    #def print_retreat_illegal_reasons(label, total, counts):
    #    print(f"\n=== WHY RETREAT WASN'T LEGAL - {label}, within retreat_never_legal events ===")
    #    print(f"  Total illegal-retreat decisions counted: {total}")
    #    if total:
    #        for reason, count in sorted(counts.items(), key=lambda x: -x[1]):
    #            pct = 100 * count / total
    #            print(f"    {reason:<28} {count:>6}  ({pct:5.1f}%)")
    #    print("  (status_blocked / already_retreated_this_turn: hard rule constraints, not")
    #    print("   fixable by scoring changes. insufficient_energy: the piece had 0 energy to")
    #    print("   pay its own retreat cost - this IS fixable, e.g. by keeping at least one")
    #    print("   energy on a fragile active piece instead of routing every attachment")
    #    print("   elsewhere.)")

    #print_retreat_illegal_reasons("P0 (main.agent)", retreat_illegal_reason_total_p0,
    #                               retreat_illegal_reason_counts_p0)
    #print_retreat_illegal_reasons("P1 (boss.agent)", retreat_illegal_reason_total_p1,
    #                               retreat_illegal_reason_counts_p1)

    #print(f"\nResults written to {CSV_PATH}")

    #def print_milestones(label, totals):
    #    print(f"\n=== MILESTONE TIMING (avg turn first reached, when reached) - {label} ===")
    #    for name in MILESTONE_NAMES:
    #        turns = totals[name]
    #        if turns:
    #            print(f"  {name:<30} avg turn {sum(turns)/len(turns):5.1f}  ({len(turns)}/{completed_games} games reached it)")
    #        else:
    #            print(f"  {name:<30} never reached in any game")

    #def print_regressions(label, total_counts, terminal_counts, midgame_counts,
    #                      in_wins_counts, in_losses_counts):
    #    print(f"\n=== MILESTONE REGRESSIONS (reached, then later lost) - {label} ===")
    #    print(f"  {'milestone':<30} {'total':>7} {'terminal':>9} {'mid-game':>9} {'in wins':>8} {'in losses':>10}")
    #    for name in MILESTONE_NAMES:
    #        total = total_counts[name]
    #        terminal = terminal_counts[name]
    #        midgame = midgame_counts[name]
    #        in_wins = in_wins_counts[name]
    #        in_losses = in_losses_counts[name]
    #        print(f"  {name:<30} {total:>7} {terminal:>9} {midgame:>9} {in_wins:>8} {in_losses:>10}")
    #    print("  (terminal = regressed at/near the game's final turn, i.e. simultaneous")
    #    print("   with the loss and not a separately preventable event. mid-game = at")
    #    print("   least one more turn cycle remained afterward - a real, actionable event.)")

    #print_milestones("P0 (main.agent)", milestone_totals_p0)
    #print_milestones("P1 (boss.agent)", milestone_totals_p1)
    #print_regressions("P0 (main.agent)", milestone_regress_counts_p0,
    #                   regress_terminal_p0, regress_midgame_p0,
    #                   regress_p0_in_wins, regress_p0_in_losses)
    #print_regressions("P1 (boss.agent)", milestone_regress_counts_p1,
     #                  regress_terminal_p1, regress_midgame_p1,
    #                   regress_p1_in_wins, regress_p1_in_losses)

    #print("\n=== EX-WALL (CRUSTLE/SYLVEON) RESPONSE - P0's perspective ===")
    #print("  (Measures the wall-counter strategy directly, independent of win/loss -")
    #print("   setup_agent deliberately avoids fighting most of the time, so win rate")
    #print("   alone is a noisy, indirect signal for whether this feature works.)")
    #print(f"  Games where the threat (or its pre-evolution) appeared on the opponent's "
    #      f"board: {ex_wall_games_with_threat}/{completed_games}")
    #if ex_wall_games_with_threat:
     #   print(f"    Of those, games where a counter (Tapu Bulu in play, or Dipplin "
     #         f"active) was built at all: {ex_wall_games_with_counter}/{ex_wall_games_with_threat}")
     #   if ex_wall_response_lags:
    #        avg_lag = sum(ex_wall_response_lags) / len(ex_wall_response_lags)
    #        print(f"    Average turns between threat sighting and counter ready: {avg_lag:.1f}")
    #    never_countered = ex_wall_games_with_threat - ex_wall_games_with_counter
     ##   if never_countered:
     #       print(f"    Games where the threat appeared but NO counter was ever built: {never_countered}")
    #print(f"  Total 'should flee' moments (EX attacker active opposite the evolved "
    #      f"wall): {ex_wall_total_flee_moments}")
    #if ex_wall_total_flee_moments:
    #    print(f"    Successfully retreated out of: "
    #          f"{ex_wall_total_flee_successes}/{ex_wall_total_flee_moments}")
    #    print(f"    Still stuck when the game ended: {ex_wall_total_flee_never_resolved}")


if __name__ == "__main__":
    main()