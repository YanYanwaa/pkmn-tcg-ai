import os
from collections import defaultdict
from types import SimpleNamespace

from sdk.api import (
    AreaType, Observation, SelectContext, OptionType,
    Card, Pokemon, State, all_card_data, to_observation_class, EnergyType
)

# --- Debug logging ---
DEBUG_OPTIONS = False  # flip to True for a single-game debug run; keep False for batch runs

# --- Core pieces ---
Ogerpon = 96
Meganium = 710
Bayleef = 918
Chikorita = 917
Hydrapple_Ex = 150
Celebi = 655
Forest_of_Vitality = 1261
Applin = 149
Dipplin = 93
Tapu_Bulu = 920

# --- Opponent threats that block our EX attackers (Hydrapple_Ex/Ogerpon
# take 0 damage-dealing effect against these). Verified directly against
# the card database: Crustle (345, DRI) has "Mysterious Rock Inn -
# Prevent all damage done to this Pokemon by attacks from your
# opponent's Pokemon {ex}". Sylveon has TWO real prints - 134 (SFA) has
# no damage-prevention ability at all, but 330 (PRE) has "Safeguard",
# the same "Prevent all damage... from Pokemon {ex}" wording as Crustle.
# 330 is the one that actually matches the threat being countered here.
Crustle = 345
Sylveon = 330
EX_WALL_IDS = frozenset({Crustle, Sylveon})

# Pre-evolutions, for an early-warning trigger - seeing these gives lead
# time to start building the counter before the actual wall is online.
# Multiple real prints exist for each; "Eevee ex" is excluded since its
# evolvesFrom text doesn't make it a valid Sylveon pre-evolution.
_EEVEE_IDS = frozenset({43, 145, 317})
_DWEBBLE_IDS = frozenset({344, 532})
EX_WALL_EARLY_WARNING_IDS = _EEVEE_IDS | _DWEBBLE_IDS

# --- Draw / search engine pieces ---
Fezandipiti_Ex = 140
Meowth_Ex = 1071

# --- Search / trainer cards ---
Rare_Candy = 1079
Ultra_Ball = 1121
Poke_Pad = 1152
Dawn = 1231
Lana_Aid = 1184
Bug_Catching_Set = 1094
Ciphermaniac_Codebreaking = 1188
Basic_Grass_Energy = 1
Lillie_Determination = 1227

all_card = all_card_data()
card_table = {c.cardId: c for c in all_card}

file_path = "deck.csv"
if not os.path.exists(file_path):
    file_path = "/kaggle_simulations/agent/" + file_path
with open(file_path, "r") as file:
    csv = file.read().split("\n")
my_deck = []
for i in range(60):
    my_deck.append(int(csv[i]))


# ============================================================
# Deck-count tracking (ported from main.py, with None-guards)
# ============================================================

card_counts: defaultdict[int, int] = defaultdict(int)
serial_set: set[int] = set()


def _add_card_count(card, my_index: int):
    if card is None:
        return
    if isinstance(card, Pokemon) or card.playerIndex == my_index:
        if card.serial not in serial_set:
            card_counts[card.id] -= 1
            serial_set.add(card.serial)
    if isinstance(card, Pokemon):
        for c in (card.energyCards or []):
            _add_card_count(c, my_index)
        for c in (card.tools or []):
            _add_card_count(c, my_index)
        for c in (card.preEvolution or []):
            _add_card_count(c, my_index)


def _set_card_counts(obs, my_index: int):
    """Rebuilds card_counts as 'remaining copies believed to still be in
    deck' - starts from the known decklist and subtracts every copy we
    can see anywhere else (hand, discard, board, stadium, looking, and
    the current selection's effect card).

    Note: prize cards are NOT subtracted, since their identities aren't
    known until drawn - same limitation main.py's version has. A card
    stuck in prizes will still read as "findable" here.
    """
    card_counts.clear()
    serial_set.clear()
    for cid in my_deck:
        card_counts[cid] += 1

    state = obs.current
    my_state = state.players[my_index]
    for card in (my_state.hand or []):
        _add_card_count(card, my_index)
    for card in (my_state.discard or []):
        _add_card_count(card, my_index)
    for card in (my_state.bench or []):
        _add_card_count(card, my_index)
    for card in (my_state.active or []):
        _add_card_count(card, my_index)
    for card in (state.stadium or []):
        _add_card_count(card, my_index)
    if state.looking is not None:
        for card in state.looking:
            _add_card_count(card, my_index)
    if obs.select is not None:
        _add_card_count(obs.select.effect, my_index)


# ============================================================
# Core scoring
# ============================================================

def _pseudo(id, energy):
    """A stand-in Pokemon-like object for hypothetical board states."""
    return SimpleNamespace(id=id, energies=[None] * energy, hp=999)


def _score(active, bench, backup_meganium_count, stadium_id,
           ex_wall_relevant=False, ex_wall_is_active=False):
    """Core scoring logic. active/bench are lists of objects with .id and .energies."""
    score = 0

    field_counts = defaultdict(int)
    for p in active + bench:
        field_counts[p.id] += 1

    has_meganium = field_counts[Meganium] >= 1
    meganium_active = any(p.id == Meganium for p in active)
    meganium_bench = any(p.id == Meganium for p in bench)
    attacker_active = any(p.id in (Hydrapple_Ex, Ogerpon) for p in active)

    # Meganium: position matters. Bench = safe, active = exposed & wrong slot.
    if meganium_bench:
        score += 150
    elif meganium_active:
        score += 40
    else:
        if field_counts[Bayleef] >= 1:
            score += 60
        elif field_counts[Chikorita] >= 1:
            score += 25

    if backup_meganium_count >= 1:
        score += 30

    energy_multiplier = 2 if has_meganium else 1

    for p in active + bench:
        is_active = p in active
        if p.id == Hydrapple_Ex:
            score += 25
            energy = len(p.energies)
            if not ex_wall_is_active:
                score += energy * 8 * energy_multiplier
                if energy >= 2:
                    score += 25
            if is_active:
                score += 35
        elif p.id == Ogerpon:
            score += 15
            energy = len(p.energies)
            if not ex_wall_is_active:
                score += energy * 8 * energy_multiplier
                if energy >= 3:
                    score += 25
            if is_active:
                score += 35
        elif p.id in (Applin, Dipplin):
            # Precursors on the path to Hydrapple_Ex. Without this,
            # attaching energy here scores identically to a no-op pass,
            # so nothing pushes this line forward on its own.
            score += 10
            energy = len(p.energies)
            score += energy * 6 * energy_multiplier
            if p.id == Dipplin and energy >= 2:
                score += 15  # close to evolving into the real attacker
        elif p.id == Tapu_Bulu:
            score += 20
            energy = len(p.energies)
            capped_energy = min(energy, 4)  # Wood Hammer costs 4 energy total (2 Grass + 2 any)
            per_energy = 20 if ex_wall_relevant else 8
            score += capped_energy * per_energy
            if is_active:
                score += 25
        elif p.id == Fezandipiti_Ex:
            # Draw/search engine. Doesn't attack or advance a setup line
            # directly, but is what lets the agent actually hit its
            # search trainers and supporters on schedule.
            score += 25
        elif p.id == Meowth_Ex:
            score += 20

    # Target state: Meganium benched + attacker active, both at once
    if meganium_bench and attacker_active:
        score += 60

    board_is_weak = not has_meganium and field_counts[Hydrapple_Ex] == 0 and field_counts[Ogerpon] == 0
    for p in active + bench:
        if p.id == Celebi and board_is_weak:
            score += 20

    if stadium_id == Forest_of_Vitality:
        score += 20

    # Fragile setup pieces should not be sitting active - they're
    # transitional/situational, not attackers, and exposed for no benefit.
    if any(p.id in (Chikorita, Bayleef, Celebi) for p in active):
        score -= 40

    fragile_active_zero_energy = [
        p for p in active if p.id in (Chikorita, Bayleef, Celebi) and len(p.energies) == 0
    ]
    if fragile_active_zero_energy:
        score -= 15

    return score


def setup_progress(field_counts, my_state, stadium_id,
                    ex_wall_relevant=False, ex_wall_is_active=False):
    """Kept for backwards compatibility with main.py's existing call site."""
    active = [p for p in (my_state.active or []) if p is not None]
    bench = [p for p in (my_state.bench or []) if p is not None]
    backup_meganium = max(0, field_counts[Meganium] - 1)
    for card in (my_state.hand or []):
        if card.id == Meganium:
            backup_meganium += 1
    return _score(active, bench, backup_meganium, stadium_id,
                  ex_wall_relevant, ex_wall_is_active)


# ============================================================
# Helpers
# ============================================================

def _get_card(obs, area, index, player_index):
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


def _estimate_incoming_damage(op_active):
    """Crude estimate of what the opponent's active could hit us for next
    turn, based on the same Ogerpon/Hydrapple-style energy scaling as our
    own attacks. Not exact - doesn't know the opponent's actual card id
    or attack, just approximates worst-case threat from energy count."""
    if op_active is None:
        return 0
    energy = len(op_active.energies)
    return 30 + 30 * energy


def pokemon_sacrifice_cost(card_id):
    """Roughly, how bad is it to lose this Pokemon if we're forced to
    promote it into a KO? Higher = worse to sacrifice."""
    if card_id == Meganium:
        return 300  # never want to lose this
    if card_id in (Hydrapple_Ex, Ogerpon):
        return 200  # core attackers, costs 2 prizes
    if card_id in (Bayleef, Chikorita):
        return 80   # mid-value, still en route to Meganium
    if card_id == Celebi:
        return 40   # situational, cheap to lose
    return 60       # default for anything else (Meowth, Fezandipiti, etc.)


def _still_findable(card_id, active, bench, hand, my_state):
    """True if we believe at least one copy of card_id remains in deck,
    based on the tracked card_counts (deck minus everything we've seen
    in hand/discard/board/stadium/looking). Does NOT account for prizes -
    see _set_card_counts docstring."""
    return card_counts[card_id] > 0


def _search_target_value(card_id, active, bench, hand, has_meganium, field_counts, ex_wall_relevant=False):
    """How valuable would fetching this specific card be right now, given
    current board state? Used both to decide whether search trainers are
    worth playing, and to pick the best target once a search is offered."""
    # If we're already holding a copy, searching for another one doesn't
    # help us play it any sooner - it was chronically outscoring just
    # playing the copy already in hand, which left Chikorita/Bayleef/
    # Meganium sitting unplayed turn after turn. Doesn't apply to
    # Basic_Grass_Energy/Forest_of_Vitality, where holding one copy
    # doesn't mean a second isn't still useful.
    already_in_hand = any(c.id == card_id for c in (hand or []))

    if card_id == Meganium:
        value = 100 if not has_meganium else 5
    elif card_id == Bayleef:
        if not has_meganium and field_counts[Chikorita] >= 1 and field_counts[Bayleef] == 0:
            value = 90
        else:
            value = 5
    elif card_id == Chikorita:
        if not has_meganium and field_counts[Chikorita] == 0 and field_counts[Bayleef] == 0:
            value = 70
        else:
            value = 5
    elif card_id == Hydrapple_Ex:
        value = 60 if field_counts[Hydrapple_Ex] == 0 else 5
    elif card_id in (Applin, Dipplin):
        if field_counts[Hydrapple_Ex] >= 1:
            value = 5  # already evolved past this, no point fetching more
        else:
            value = 45 if card_id == Dipplin else 30  # Dipplin is one evolution closer
    elif card_id == Ogerpon:
        value = 55 if field_counts[Ogerpon] < 2 else 5
    elif card_id == Tapu_Bulu:
        # Only actively worth fetching once a Crustle/Sylveon has been
        # confirmed on the opponent's board - otherwise it's just a spare
        # attacker with no particular urgency.
        value = 65 if (ex_wall_relevant and field_counts[Tapu_Bulu] == 0) else 5
    elif card_id in (Fezandipiti_Ex, Meowth_Ex):
        already_have_engine = field_counts[Fezandipiti_Ex] >= 1 or field_counts[Meowth_Ex] >= 1
        value = 30 if not already_have_engine else 5
    elif card_id == Forest_of_Vitality:
        value = 40
    elif card_id == Basic_Grass_Energy:
        value = 35
    else:
        value = 0

    if already_in_hand and card_id not in (Basic_Grass_Energy,):
        value = min(value, 5)

    return value


def _best_search_value(active, bench, hand, has_meganium, field_counts, ex_wall_relevant=False):
    """Best value among the pieces we'd realistically want to search for
    right now, used to decide if playing a search trainer is worth it."""
    candidates = [Meganium, Bayleef, Chikorita, Hydrapple_Ex, Ogerpon,
                  Applin, Dipplin, Fezandipiti_Ex, Meowth_Ex, Tapu_Bulu,
                  Forest_of_Vitality, Basic_Grass_Energy]
    best = 0
    for cid in candidates:
        if _still_findable(cid, active, bench, hand, None):
            best = max(best, _search_target_value(cid, active, bench, hand, has_meganium, field_counts, ex_wall_relevant))
    return best


# NOTE: Bayleef has two known print IDs (918 and 709) - the rest of this
# file only checks for 918. These two helpers check both, since getting
# this wrong would make the Celebi-at-setup logic below undercount a hand
# that's actually complete. Worth reconciling with the rest of the file's
# single Bayleef constant at some point - flagging rather than doing a
# wider rename here.
_BAYLEEF_IDS = frozenset({918, 709})


def _can_build_meganium_from_hand(hand):
    """True if the opening hand alone - no searching, no further draws -
    is enough to get Meganium onto the field. Needs Chikorita plus either
    Bayleef (normal evolution) or Rare Candy (skips straight from
    Chikorita), and Meganium itself in hand either way."""
    ids = [c.id for c in (hand or [])]
    if Chikorita not in ids or Meganium not in ids:
        return False
    return any(i in _BAYLEEF_IDS for i in ids) or Rare_Candy in ids


def _can_build_hydrapple_from_hand(hand):
    """Same idea for Hydrapple_Ex: needs Applin plus either Dipplin or
    Rare Candy, and Hydrapple_Ex itself already in hand."""
    ids = [c.id for c in (hand or [])]
    if Applin not in ids or Hydrapple_Ex not in ids:
        return False
    return Dipplin in ids or Rare_Candy in ids


def _meganium_line_completable(field_counts, hand):
    """Broader version of _can_build_meganium_from_hand for mid-game use:
    counts pieces already on the field too, not just in hand. Used to
    decide whether Meganium is actually one step away, versus just
    having the top card sitting in hand with a missing link (e.g. no
    Bayleef and no Rare Candy) - which looks 'secured' if you only check
    for Meganium itself, but isn't."""
    if field_counts[Meganium] >= 1:
        return True
    ids = [c.id for c in (hand or [])]
    has_chikorita = field_counts[Chikorita] >= 1 or Chikorita in ids
    has_meganium = Meganium in ids
    if not has_chikorita or not has_meganium:
        return False
    has_bayleef = field_counts[Bayleef] >= 1 or any(i in _BAYLEEF_IDS for i in ids)
    return has_bayleef or Rare_Candy in ids


def _hydrapple_line_completable(field_counts, hand):
    """Broader version of _can_build_hydrapple_from_hand for mid-game
    use - same idea, counts field presence too."""
    if field_counts[Hydrapple_Ex] >= 1:
        return True
    ids = [c.id for c in (hand or [])]
    has_applin = field_counts[Applin] >= 1 or Applin in ids
    has_hydrapple = Hydrapple_Ex in ids
    if not has_applin or not has_hydrapple:
        return False
    has_dipplin = field_counts[Dipplin] >= 1 or Dipplin in ids
    return has_dipplin or Rare_Candy in ids


def _opponent_ex_wall_status(op_active, op_bench, op_discard):
    """Detect Crustle/Sylveon on the opponent's side. We can't see their
    hand or deck, so this is necessarily reactive - it only knows about
    a threat once something relevant has actually been played to their
    board (active or bench, both visible). Returns (in_play, is_active,
    defeated):
      in_play: the evolved wall OR its pre-evolution (Eevee/Dwebble) is
        currently on their board - worth prepping for even before the
        wall itself is online, since the pre-evolution is real lead time.
      is_active: the EVOLVED wall specifically is their CURRENT active -
        this is when we actually want to swap our EX attacker out. A
        baby Eevee/Dwebble being active doesn't block anything, so this
        deliberately does NOT count the pre-evolution sighting.
      defeated: the evolved wall has been sent to their discard pile and
        none remain in play - once true, stop the whole strategy.
    """
    board = list(op_bench or [])
    if op_active is not None:
        board = board + [op_active]
    wall_on_board = any(p is not None and p.id in EX_WALL_IDS for p in board)
    early_warning = any(p is not None and p.id in EX_WALL_EARLY_WARNING_IDS for p in board)
    in_play = wall_on_board or early_warning
    is_active = op_active is not None and op_active.id in EX_WALL_IDS
    defeated = (not wall_on_board) and any(
        c.id in EX_WALL_IDS for c in (op_discard or [])
    )
    return in_play, is_active, defeated


def _describe_option(obs, o, my_index):
    """Best-effort human-readable label for a scored option, for debug logging."""
    try:
        if o.type == OptionType.PLAY:
            card = _get_card(obs, AreaType.HAND, o.index, my_index)
            name = card_table[card.id].name if card and card.id in card_table else card
            return f"PLAY {name}"
        elif o.type == OptionType.EVOLVE:
            evo_card = _get_card(obs, AreaType.HAND, o.index, my_index)
            pokemon = _get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
            evo_name = card_table[evo_card.id].name if evo_card and evo_card.id in card_table else evo_card
            from_name = card_table[pokemon.id].name if pokemon and pokemon.id in card_table else pokemon
            return f"EVOLVE {from_name} -> {evo_name}"
        elif o.type == OptionType.ATTACH:
            card = _get_card(obs, o.area, o.index, my_index)
            pokemon = _get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
            target = card_table[pokemon.id].name if pokemon and pokemon.id in card_table else pokemon
            return f"ATTACH -> {target}"
        elif o.type == OptionType.CARD:
            card = _get_card(obs, o.area, o.index, o.playerIndex)
            name = card_table[card.id].name if card and card.id in card_table else card
            return f"CARD[{o.area}] {name}"
        elif o.type == OptionType.ATTACK:
            return f"ATTACK id={o.attackId}"
        elif o.type == OptionType.RETREAT:
            return "RETREAT"
        elif o.type == OptionType.ABILITY:
            card = _get_card(obs, o.area, o.index, my_index)
            name = card_table[card.id].name if card and card.id in card_table else card
            return f"ABILITY {name}"
        elif o.type == OptionType.NUMBER:
            return f"NUMBER {o.number}"
        return str(o.type)
    except Exception:
        return f"{o.type} (describe failed)"


# ============================================================
# Main agent
# ============================================================

def agent(obs_dict: dict) -> list[int]:
    obs = to_observation_class(obs_dict)
    if obs.select is None:
        return my_deck

    state = obs.current
    select = obs.select
    context = select.context
    my_index = state.yourIndex
    my_state = state.players[my_index]
    op_state = state.players[1 - my_index]

    _set_card_counts(obs, my_index)

    active = [p for p in (my_state.active or []) if p is not None]
    bench = [p for p in (my_state.bench or []) if p is not None]

    op_active_list = [p for p in (op_state.active or []) if p is not None]
    op_active = op_active_list[0] if op_active_list else None

    stadium_id = 0
    for card in (state.stadium or []):
        stadium_id = card.id

    field_counts = defaultdict(int)
    for p in active + bench:
        field_counts[p.id] += 1

    has_meganium = field_counts[Meganium] >= 1

    ex_wall_in_play, ex_wall_is_active, ex_wall_defeated = _opponent_ex_wall_status(
        op_active, op_state.bench, op_state.discard
    )
    # Once a threat has been confirmed defeated, don't chase this
    # strategy anymore even if it briefly looked relevant earlier.
    ex_wall_relevant = ex_wall_in_play and not ex_wall_defeated

    # Nothing in _score() currently distinguishes "this bench slot is
    # needed for something" from "this bench slot has a Pokemon in it" -
    # every recognized Pokemon contributes flat positive score just by
    # existing, so the agent has no reason to hold a slot open. Once the
    # wall is confirmed and Tapu Bulu isn't secured yet, that's a real
    # problem: spare/redundant bench pieces can fill the bench before
    # Tapu Bulu is even drawn, leaving no room for it when it matters.
    tapu_bulu_secured = field_counts[Tapu_Bulu] >= 1 or any(
        c.id == Tapu_Bulu for c in (my_state.hand or [])
    )
    reserve_bench_for_tapu_bulu = ex_wall_relevant and not tapu_bulu_secured

    setup_score = setup_progress(field_counts, my_state, stadium_id,
                              ex_wall_relevant, ex_wall_is_active)

    backup_meganium = max(0, field_counts[Meganium] - 1)
    for card in (my_state.hand or []):
        if card.id == Meganium:
            backup_meganium += 1

    base_score = _score(active, bench, backup_meganium, stadium_id,
                        ex_wall_relevant, ex_wall_is_active)

    def hypothetical(new_active=None, new_bench=None, new_stadium=None,
                    energy_delta=0, energy_target_id=None):
        hyp_active = list(new_active) if new_active is not None else list(active)
        hyp_bench = list(new_bench) if new_bench is not None else list(bench)

        if energy_target_id is not None and energy_delta:
            for lst in (hyp_active, hyp_bench):
                for i, p in enumerate(lst):
                    if p.id == energy_target_id:
                        lst[i] = _pseudo(p.id, len(p.energies) + energy_delta)
                        break

        hyp_stadium = new_stadium if new_stadium is not None else stadium_id
        return _score(hyp_active, hyp_bench, backup_meganium, hyp_stadium,
                    ex_wall_relevant, ex_wall_is_active)

    scores = []
    attack_cache = {}  # option index -> attack_value, resolved after the main pass
    debug_entries = []  # (label, score) pairs, only populated if DEBUG_OPTIONS

    # Celebi's attack IDs turned out not to be reliably identifiable by
    # fixed number (945/946 matched in some games, not in others - never
    # fully confirmed why). Following the same approach a working sibling
    # agent uses: identify Celebi's attacks by POSITION among whatever
    # ATTACK options are actually legal this decision, not by ID. Assumes
    # the engine lists them in card order (Traverse Time first, Solar
    # Cutter second) - if only one attack is ever legal at once this
    # could misattribute it, but that's a rarer edge case than the ID
    # mismatch we were hitting before.
    my_active = active[0] if active else None
    celebi_active_now = my_active is not None and my_active.id == Celebi

    if DEBUG_OPTIONS and celebi_active_now:
        # Raw, unfiltered dump of every legal option this decision - no
        # scoring, no ranking, no top-8 truncation. The normal debug
        # printout below only shows the top 8 BY SCORE, so if Traverse
        # Time were legal but happened to score low that decision, it
        # could be sitting off-screen and we'd never see it there. This
        # bypasses our own scoring entirely to show ground truth.
        celebi_energy_count = len(my_active.energies)
        raw_types = []
        for o in select.option:
            if o.type == OptionType.ATTACK:
                raw_types.append(f"ATTACK(id={o.attackId})")
            else:
                raw_types.append(str(o.type))
        #print(f"[CELEBI RAW] turn={state.turn} energy={celebi_energy_count} "
              #f"options=[{', '.join(raw_types)}]")

    celebi_attack_indices = []
    celebi_wants_search = False
    if celebi_active_now:
        celebi_attack_indices = [idx for idx, opt in enumerate(select.option) if opt.type == OptionType.ATTACK]
        # Checking whether Meganium/Hydrapple_Ex itself is in hand isn't
        # enough - a game showed Celebi treating the line as "secured"
        # the moment Meganium landed in hand, even though Bayleef (the
        # connecting piece) was never fetched, and retreated away with
        # the line still one card short. Use the full-chain check instead.
        meganium_completable = _meganium_line_completable(field_counts, my_state.hand)
        hydrapple_completable = _hydrapple_line_completable(field_counts, my_state.hand)
        op_hp = getattr(op_active, "hp", None)
        if not meganium_completable:
            celebi_wants_search = True
        elif op_hp is not None and op_hp <= 30:
            celebi_wants_search = False  # take the free knockout instead
        elif not hydrapple_completable:
            celebi_wants_search = True
        else:
            celebi_wants_search = False

    for opt_idx, o in enumerate(select.option):
        score = base_score

        if o.type == OptionType.CARD:
            card = _get_card(obs, o.area, o.index, o.playerIndex)
            if card is not None and o.playerIndex == my_index:
                if context == SelectContext.SETUP_ACTIVE_POKEMON:
                    score = hypothetical(new_active=[_pseudo(card.id, 0)])
                    if card.id == Celebi:
                        hand_already_complete = (
                            _can_build_meganium_from_hand(my_state.hand)
                            or _can_build_hydrapple_from_hand(my_state.hand)
                        )
                        if not hand_already_complete:
                            # The opening hand can't finish either line on
                            # its own - Celebi active turn 1 can use
                            # Traverse Time to fetch exactly what's
                            # missing, then build Meganium (or Hydrapple)
                            # the very next turn. Make it the clear top
                            # pick for starting active in that case.
                            score += 200

                elif context == SelectContext.SETUP_BENCH_POKEMON:
                    score = hypothetical(new_bench=bench + [_pseudo(card.id, 0)])

                elif context in (SelectContext.SWITCH, SelectContext.TO_ACTIVE):
                    combined = active + bench
                    new_bench = [p for p in combined if p is not card]
                    score = hypothetical(new_active=[card], new_bench=new_bench)

                    incoming_threat = _estimate_incoming_damage(op_active)
                    survives = incoming_threat < getattr(card, "hp", 0)

                    if survives:
                        score += 150
                    else:
                        sac_value = pokemon_sacrifice_cost(card.id)
                        score -= sac_value
                        score += getattr(card, "hp", 0) // 10

                    if ex_wall_relevant and card.id in (Tapu_Bulu, Dipplin, Meganium):
                        # Widened from ex_wall_is_active to ex_wall_relevant.
                        # Choosing who comes up is forward-looking - even if
                        # the wall isn't the opponent's active RIGHT NOW
                        # (still on their bench, or this switch was forced
                        # by something unrelated), bringing up an EX
                        # attacker here still risks facing it walled next
                        # turn with nothing gained by sending it up early.
                        # Tapu Bulu/Dipplin/Meganium can all still fight
                        # normally against a non-wall active, so there's no
                        # downside to preferring them once the threat is
                        # confirmed anywhere in play. Tiered rather than
                        # flat: Tapu Bulu is the real answer (Wood Hammer
                        # hits hard once charged), Dipplin is a strong
                        # second (Do the Wave deals real damage off bench
                        # count and isn't EX at all), Meganium is the
                        # fallback body if neither of the others is around.
                        if card.id == Tapu_Bulu:
                            score += 170
                        elif card.id == Dipplin:
                            score += 160
                        else:  # Meganium
                            score += 150
                        # All three land above the +150 survival bonus, so
                        # each wins even against a candidate that also
                        # happens to survive the incoming-damage estimate.

                elif context == SelectContext.TO_BENCH:
                    score = hypothetical(new_bench=bench + [card])

                elif context == SelectContext.TO_HAND:
                    score = base_score + _search_target_value(
                        card.id, active, bench, my_state.hand, has_meganium, field_counts, ex_wall_relevant
                    )

                elif context in (SelectContext.ATTACH_FROM, SelectContext.ATTACH_TO):
                    if select.contextCard is not None:
                        score = hypothetical(energy_delta=1, energy_target_id=card.id)
                    else:
                        score = base_score

                else:
                    # Catch-all for any CARD-selection context not explicitly
                    # handled above - most commonly forced discards (paying
                    # Ultra Ball's or Ciphermaniac's cost, hand-size limits,
                    # etc). Default conservatively: penalize losing anything
                    # setup-critical, so the agent discards spares/less-
                    # important cards instead when it has a choice.
                    penalty = 0
                    if card.id in (Meganium, Hydrapple_Ex, Ogerpon, Bayleef, Chikorita, Celebi):
                        penalty = 200
                    elif card.id == Rare_Candy:
                        penalty = 150
                    elif card.id in (Ultra_Ball, Bug_Catching_Set, Ciphermaniac_Codebreaking,
                                      Poke_Pad, Lana_Aid, Dawn, Lillie_Determination,
                                      Forest_of_Vitality):
                        penalty = 60
                    elif card.id == Basic_Grass_Energy:
                        penalty = 40
                    score = base_score - penalty

        elif o.type == OptionType.PLAY:
            card = _get_card(obs, AreaType.HAND, o.index, my_index)
            if card is not None:
                if card.id == Forest_of_Vitality:
                    score = hypothetical(new_stadium=card.id)

                elif card.id in (Meganium, Hydrapple_Ex, Ogerpon, Bayleef, Chikorita, Celebi, Applin, Tapu_Bulu):
                    score = hypothetical(new_bench=bench + [_pseudo(card.id, 0)])
                    # Search (70-100) and abilities (40-50) were routinely
                    # outscoring the marginal field-presence gain from
                    # actually playing the first Chikorita/Applin of the
                    # game, so the card sat in hand turn after turn even
                    # though nothing better was being accomplished. Give
                    # starting a cold line a real priority bump.
                    if card.id in (Chikorita, Bayleef) and not has_meganium \
                            and field_counts[Chikorita] == 0 and field_counts[Bayleef] == 0:
                        score += 50
                    elif card.id == Applin and field_counts[Applin] == 0 \
                            and field_counts[Dipplin] == 0 and field_counts[Hydrapple_Ex] == 0:
                        score += 40
                    elif card.id == Celebi and state.turn <= 1:
                        # Celebi's ability (search up to 3 Grass Pokemon/
                        # Stadiums) is only worth chasing if it can be
                        # benched and used in the same opening round it
                        # was drawn - get it down now if that's still on
                        # the table.
                        score += 30
                    elif card.id == Tapu_Bulu and ex_wall_relevant and field_counts[Tapu_Bulu] == 0:
                        # Crustle/Sylveon block our EX attackers - Tapu
                        # Bulu doesn't rely on the EX-based mechanic they
                        # counter, so it's the preferred answer whenever
                        # one of them is confirmed on the opponent's board.
                        score += 65
                    elif card.id == Ogerpon and field_counts[Ogerpon] >= 1 and reserve_bench_for_tapu_bulu:
                        # Same idea as the redundant second Fezandipiti_Ex/
                        # Meowth_Ex below - Ogerpon is itself an EX
                        # Pokemon walled by Crustle/Sylveon just like
                        # Hydrapple_Ex, so an extra copy is doubly not the
                        # answer here. The first copy still has real value
                        # as an attacker/energy sink for whenever the wall
                        # isn't active, so this only discourages
                        # additional copies while a slot's being held open.
                        score -= 40

                elif card.id in (Fezandipiti_Ex, Meowth_Ex):
                    # Get the draw engine online early - most valuable
                    # before the main setup lines have consumed the
                    # turn's resources.
                    score = hypothetical(new_bench=bench + [_pseudo(card.id, 0)])
                    if not has_meganium and field_counts[Bayleef] == 0 and field_counts[Chikorita] == 0:
                        score += 15  # nothing more urgent yet, may as well set this up first
                    already_have_engine = field_counts[Fezandipiti_Ex] >= 1 or field_counts[Meowth_Ex] >= 1
                    if reserve_bench_for_tapu_bulu and already_have_engine:
                        # A second engine piece is a genuinely spare play -
                        # the first copy still earns its keep helping find
                        # Tapu Bulu itself, but a redundant second one
                        # shouldn't be allowed to use up the bench slot
                        # we're trying to hold open for it.
                        score -= 40

                elif card.id == Rare_Candy:
                    can_skip_to_meganium = (
                        field_counts[Chikorita] >= 1
                        and any(c.id == Meganium for c in (my_state.hand or []))
                    )
                    score = base_score + (95 if can_skip_to_meganium else -20)

                elif card.id in (Ultra_Ball, Bug_Catching_Set, Ciphermaniac_Codebreaking,
                                  Poke_Pad, Lana_Aid):
                    best_value = _best_search_value(active, bench, my_state.hand,
                                                     has_meganium, field_counts, ex_wall_relevant)
                    score = base_score + best_value

                elif card.id == Dawn:
                    score = base_score + (80 if not has_meganium else 10)

                elif card.id == Lillie_Determination:
                    meganium_bench_now = any(p.id == Meganium for p in bench)
                    attacker_active_now = any(p.id in (Hydrapple_Ex, Ogerpon) for p in active)
                    if meganium_bench_now and attacker_active_now:
                        score = base_score + 20
                    else:
                        score = base_score + 70

        elif o.type == OptionType.EVOLVE:
            evo_card = _get_card(obs, AreaType.HAND, o.index, my_index)
            pokemon = _get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
            if evo_card is not None and pokemon is not None:
                energy = len(pokemon.energies)
                new_p = _pseudo(evo_card.id, energy)
                evolving_dipplin_to_hydrapple = (
                    evo_card.id == Hydrapple_Ex and pokemon.id == Dipplin
                )
                if o.inPlayArea == AreaType.ACTIVE:
                    score = hypothetical(new_active=[new_p])
                    # Evolving Bayleef->Meganium (or Chikorita->Bayleef) in
                    # the active slot locks it into the exposed position.
                    # If we have a bench to retreat into instead, defer
                    # this evolution - let RETREAT win this decision, then
                    # evolve from the bench next action instead, for the
                    # full benched-Meganium value.
                    if evo_card.id in (Meganium, Bayleef) and bench:
                        score -= 90
                    if evolving_dipplin_to_hydrapple and ex_wall_is_active:
                        # Narrowed from ex_wall_relevant to ex_wall_is_active.
                        # ex_wall_relevant stays True for the rest of the
                        # game the moment any Eevee/Dwebble/Crustle/Sylveon
                        # touches the opponent's board and stays there -
                        # even sitting harmlessly on their bench, never
                        # actually brought active. Gating this block on
                        # that broad flag effectively forfeited the whole
                        # Hydrapple line for the entire game in most
                        # matchups, which was never the intent - the
                        # retreat-swap logic already handles "evolved into
                        # Hydrapple, wall showed up later" by pulling it
                        # back out, so this only needs to stop the
                        # evolution when the wall is the opponent's
                        # active RIGHT NOW.
                        score -= 180
                else:
                    new_bench = [p for p in bench if p is not pokemon] + [new_p]
                    score = hypothetical(new_bench=new_bench)
                    if evo_card.id == Dipplin and ex_wall_relevant and field_counts[Tapu_Bulu] == 0 \
                            and not any(c.id == Tapu_Bulu for c in (my_state.hand or [])):
                        # Tapu Bulu isn't in play or in hand to fall back
                        # on - Dipplin's own attack scales with bench
                        # count and doesn't rely on the EX mechanic the
                        # wall blocks, making it the next-best answer.
                        score += 45
                    if evolving_dipplin_to_hydrapple and ex_wall_is_active:
                        # Same narrowed condition and reasoning as the
                        # active-slot case above.
                        score -= 180

        elif o.type == OptionType.ATTACH:
            card = _get_card(obs, o.area, o.index, my_index)
            pokemon = _get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
            if card is not None and pokemon is not None:
                score = hypothetical(energy_delta=1, energy_target_id=pokemon.id)
                if pokemon.id == Celebi and o.inPlayArea == AreaType.ACTIVE \
                        and len(pokemon.energies) == 0:
                    # Traverse Time costs 1 energy and Celebi starts with
                    # none - without this, energy routinely goes to
                    # Ogerpon/searches instead and the attack never
                    # becomes legal at all. Not turn-gated: if Celebi
                    # ends up active later too (e.g. forced in after a
                    # KO), it should still get powered up rather than
                    # sit there useless - it's only ever meant to be
                    # active for as long as it takes to fire this once,
                    # not to stay there (the existing fragile-active
                    # penalty and retreat priority already push it back
                    # off active as soon as there's somewhere to go).
                    score += 70
                if pokemon.id == Tapu_Bulu and o.inPlayArea == AreaType.ACTIVE \
                        and ex_wall_is_active and len(pokemon.energies) < 4:
                    # Same problem as Celebi above, same fix - but +70
                    # still wasn't reliably winning against everything
                    # else competing that turn (searches, abilities,
                    # other priority bumps routinely land in the 65-95
                    # range too). Raised well above those so getting Tapu
                    # Bulu to 4 energy is unambiguously the top priority
                    # while it's the one actually standing in front of
                    # the wall, not just a nudge among several.
                    score += 150

                # Chikorita/Bayleef/Meganium/Meowth_Ex/Fezandipiti_Ex all
                # share the same problem: _score() gives none of them
                # credit for their own energy count (Meganium's Wild
                # Growth multiplier is presence-based, not tied to energy
                # on Meganium itself; the other four just don't scale at
                # all). Without a fix, a genuinely useless attach to any
                # of them ties EXACTLY with base_score - the same score
                # passing gets - and can win that tie purely by
                # enumeration-order accident. But an ACTIVE one that's
                # still under its own retreat cost is a real exception:
                # getting it retreat-ready is worth prioritizing, using
                # each card's actual cost from card_table rather than
                # assuming they match (Chikorita: 1, Bayleef: 2,
                # Meganium: 2, Meowth_Ex/Fezandipiti_Ex: 1).
                if pokemon.id in (Chikorita, Bayleef, Meganium, Meowth_Ex, Fezandipiti_Ex):
                    retreat_cost = card_table[pokemon.id].retreatCost or 0
                    needs_retreat_energy = (
                        o.inPlayArea == AreaType.ACTIVE and len(pokemon.energies) < retreat_cost
                    )
                    if needs_retreat_energy:
                        score += 30
                    else:
                        score -= 5

        elif o.type == OptionType.ABILITY:
            card = _get_card(obs, o.area, o.index, my_index)
            if card is not None and card.id in (Ogerpon, Hydrapple_Ex):
                score = base_score + 50
            elif card is not None and card.id in (Fezandipiti_Ex, Meowth_Ex):
                # Draw/discard-to-hand abilities - real value since they
                # convert directly into more search/evolution options
                # next turn.
                score = base_score + 40
            # Note: Celebi's "Traverse Time" is an ATTACK, not an ABILITY,
            # despite reading like a search ability - it can only be used
            # while Celebi is active. Identified by position among legal
            # ATTACK options, not by ID - see the ATTACK branch below.

        elif o.type == OptionType.ATTACK:
            if celebi_active_now and opt_idx in celebi_attack_indices:
                position = celebi_attack_indices.index(opt_idx)
                is_search_move = (position == 0)
                if is_search_move and celebi_wants_search:
                    # Traverse Time, and a core line still needs it -
                    # scored directly (not deferred like real attacks
                    # below) since it doesn't trade damage or need to
                    # wait for "nothing better to do". Same Meganium-
                    # first, then-Hydrapple priority used elsewhere.
                    if not has_meganium:
                        score = base_score + 60
                    elif field_counts[Hydrapple_Ex] == 0:
                        score = base_score + 50
                    else:
                        score = base_score
                    scores.append(score)
                    continue
                elif not is_search_move and not celebi_wants_search:
                    # Solar Cutter, and we're past the point of needing
                    # the search (both lines secured, or there's a free
                    # KO on the table) - fall through to the normal
                    # deferred-attack logic below like any other attack,
                    # including the flat-30 damage value added there.
                    pass
                else:
                    # The non-preferred attack this turn (e.g. Solar
                    # Cutter while we still need Traverse Time, or vice
                    # versa) - don't chase it, but don't block it either;
                    # let it fall through to the same deferred logic so
                    # it's only ever a last resort.
                    pass

            my_active_energy = len(my_active.energies) if my_active else 0
            op_active_energy = len(op_active.energies) if op_active else 0
            my_total_energy = sum(len(p.energies) for p in active + bench)

            damage = 0
            if o.attackId == 120:  # Ogerpon
                damage = 30 + 30 * (my_active_energy + op_active_energy)
            elif o.attackId == 195:  # Hydrapple
                damage = 30 + 30 * my_total_energy
            elif o.attackId == 115:  # Dipplin - Do the Wave. Verified
                # against the card database: base damage 0, "This attack
                # does 20 damage for each of your Benched Pokemon", costs
                # 1 energy. Not an EX Pokemon, so unaffected by the wall
                # suppression below - this is exactly why it's the
                # preferred active fighter while Crustle/Sylveon matter.
                damage = 20 * len(bench)
            elif my_active is not None and my_active.id == Tapu_Bulu:
            # Wood Hammer (attackId 1326): flat 220, costs 2 energy, not
            # energy-scaled like Ogerpon/Hydrapple's attacks. Card text also
            # deals 30 recoil to itself - not modeled in damage/attack_value
            # here since that's about damage dealt, not taken; see the energy
            # cap note below for why recoil still matters elsewhere.
                damage = 220
            elif celebi_active_now and opt_idx in celebi_attack_indices \
                    and celebi_attack_indices.index(opt_idx) == 1:
                damage = 30  # Solar Cutter, flat per card text
            elif o.attackId == 1323:  # Chikorita - Seed Bomb
                damage = 30
            elif o.attackId == 1324:  # Bayleef (918 print) - Leaf Step
                damage = 60
            elif o.attackId == 1027:  # Bayleef (709 print) - Push Down
                damage = 50
            elif o.attackId == 1028:  # Meganium - Solar Beam
                damage = 140
            elif o.attackId == 194:  # Applin - Spray Fluid
                damage = 20
            elif o.attackId == 1546:  # Meowth ex - Tuck Tail
                damage = 60
            elif o.attackId == 183:  # Fezandipiti ex - Cruel Arrow
                damage = 100
            # o.attackId == 1322 (Chikorita - Growl) is intentionally not
            # given damage here - it's a pure status move (0 base
            # damage). It still gets to beat END via the floor in the
            # deferred-resolution step below, just not prioritized above
            # any real attack the way actual damage would be.

            if o.attackId in (120, 195) and ex_wall_is_active:
                # Crustle/Sylveon block damage from our EX attackers -
                # this attack would do nothing, so let the normal
                # deferred-attack logic treat it as a last resort just
                # like any other zero-value option, rather than actively
                # wasting a turn on it.
                damage = 0

            attack_value = 0
            if op_active is not None and damage > 0:
                if damage >= op_active.hp:
                    data = card_table.get(op_active.id)
                    prize = 3 if (data and data.megaEx) else 2 if (data and data.ex) else 1
                    attack_value = 200 + prize * 50
                else:
                    attack_value = damage // 4

            # Score is resolved after this pass, once we know whether any
            # other option this decision actually beats doing nothing -
            # attacking ends the turn, so it should only win once there's
            # genuinely nothing better left to do (see below the loop).
            attack_cache[opt_idx] = attack_value
            score = None

        elif o.type == OptionType.RETREAT:
            my_active_p = active[0] if active else None
            active_id = my_active_p.id if my_active_p else None
            retreat_cost = 0
            if my_active_p is not None and my_active_p.id in card_table:
                retreat_cost = card_table[my_active_p.id].retreatCost or 0
            energy_lost = min(retreat_cost, len(my_active_p.energies)) if my_active_p else 0

            meganium_active_now = active_id == Meganium
            celebi_has_energy = celebi_active_now and my_active is not None and len(my_active.energies) >= 1
            celebi_can_still_traverse = celebi_active_now and celebi_wants_search and \
                (len(celebi_attack_indices) > 0 or celebi_has_energy)
            fragile_active = active_id in (Chikorita, Bayleef, Celebi) and not celebi_can_still_traverse
            active_is_attacker = active_id in (Hydrapple_Ex, Ogerpon)
            has_next_evo_in_hand = any(
                c.id in (Bayleef, Meganium) for c in (my_state.hand or [])
            )

            def _ready(p):
                if p.id == Hydrapple_Ex:
                    return len(p.energies) >= 2
                if p.id == Ogerpon:
                    return len(p.energies) >= 3
                return False

            bench_attacker_ready = any(_ready(p) for p in bench if p.id in (Hydrapple_Ex, Ogerpon))

            wall_counter_ready = any(
                p.id in (Tapu_Bulu, Dipplin, Meganium) for p in bench
            )
            should_flee_wall = ex_wall_is_active and active_is_attacker and wall_counter_ready

            if should_flee_wall:
                score = base_score + 120 - (energy_lost * 15)
            elif meganium_active_now:
                score = base_score + 80 - (energy_lost * 15)
            elif celebi_can_still_traverse:
                score = base_score - 30
            elif fragile_active and bench:
                bonus = 70 if has_next_evo_in_hand else 35
                score = base_score + bonus - (energy_lost * 15)
            elif has_meganium and bench_attacker_ready and not active_is_attacker and not ex_wall_is_active:
                score = base_score + 90 - (energy_lost * 15)
            else:
                score = base_score - 10

        elif o.type == OptionType.NUMBER:
            score = o.number

        scores.append(score)

    # Resolve deferred ATTACK scores now that every other option this
    # decision has a real score. Attack ends the turn, so it should only
    # win when nothing else here actually improves on doing nothing -
    # otherwise it's held back just below whatever the best real option
    # is, so setup work always gets first priority within a turn.
    if attack_cache:
        non_attack_scores = [s for idx, s in enumerate(scores) if idx not in attack_cache and s is not None]
        best_other = max(non_attack_scores) if non_attack_scores else base_score
        for idx, attack_value in attack_cache.items():
            if best_other > base_score:
                scores[idx] = best_other - 1
            else:
                # Nothing else this decision beats just passing (END has
                # no explicit handler anywhere, so it always scores
                # exactly base_score) - this is the genuine "attack or
                # end turn" choice. Per an explicit ask, attacking should
                # win here even when the attack's own value is small or
                # zero (e.g. a pure status move with no damage, or an
                # attack whose damage isn't modeled above) - the floor of
                # 2 only matters in that narrow case; any attack with
                # real damage already clears base_score - 1 + attack_value
                # on its own and is unaffected by this.
                scores[idx] = base_score - 1 + max(attack_value, 2)

    if DEBUG_OPTIONS:
        for opt_idx, o in enumerate(select.option):
            debug_entries.append((_describe_option(obs, o, my_index), scores[opt_idx]))

    output = []
    if scores:
        sorted_scores = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        for i in range(select.maxCount):
            if sorted_scores[i][1] >= 0 or select.minCount > i:
                output.append(sorted_scores[i][0])

    if DEBUG_OPTIONS and debug_entries:
        ranked = sorted(enumerate(debug_entries), key=lambda x: x[1][1], reverse=True)
        #print(f"\n===== TURN {state.turn} | context={context} | setup_score={setup_score} =====")
        #for i, (orig_idx, (label, s)) in enumerate(ranked[:8], 1):
            #marker = " <-- chosen" if orig_idx in output else ""
            #print(f"  {i}. {label:<40} score={s:>6}{marker}")

    return output