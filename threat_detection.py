
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from sdk.api import CardData, Attack, Pokemon, PlayerState, State, EnergyType


# Setup. helpers from main are reused here
def find_card_ids_by_name(card_data: dict[int, CardData], name_substring: str) -> set[int]:

    needle = name_substring.lower()
    return {cid for cid, c in card_data.items() if needle in c.name.lower()}
    # im working in card names as opposed to ids

def attack_cost(attack: Attack | None) -> int:
    return len(attack.energies) if attack else 0


def cheapest_attack_cost(card: CardData, attack_data: dict[int, Attack]) -> int:
    costs = [attack_cost(attack_data.get(aid)) for aid in card.attacks]
    return min(costs) if costs else 1


def strongest_attack_damage(card: CardData, attack_data: dict[int, Attack]) -> int:
    damages = [attack_data[aid].damage for aid in card.attacks if aid in attack_data]
    return max(damages) if damages else 0


def prize_value(card: CardData) -> int:
    if card.megaEx:
        return 3
    if card.ex:
        return 2
    return 1


def stage_key(card: CardData) -> str:
    if card.stage2:
        return "stage2"
    if card.stage1:
        return "stage1"
    return "basic"



# 1. Based on game experience, threat values of meta cards
BASE_THREAT_BY_NAME: dict[str, float] = {
   "Mega Lucario ex": 10.0,
   "Dragapult": 7.0,
   "Mega Starmie ex": 6.0,
   "Crustle": 7.0,
   "Sylveon": 7.0,
   "Mega Abomasnow ex": 6.5,
   "Makuhita": 5.5,
   "Hariyama": 6.5,
   "Alakazam": 6.0,
}

STAGE_BASELINE_THREAT: dict[str, float] = {
    "basic": 2.0,
    "stage1": 4.0,
    "stage2": 6.0,
}

EX_BONUS = 2.0
MEGA_EX_BONUS = 3.5
TERA_BONUS = 1.0  #tera pokemon take no damage on bench


ABILITY_THREAT_BONUS_BY_NAME: dict[str, float] = {
    "ability_lock": 3.0,
    "gust_switch": 2.0,
    "bench_snipe": 2.5,
    "energy_denial": 2.5,
    "single_target_removal": 3.5,
    "stadium_replace": 1.5,
    # ngl this is just here in case there are any other
    # non-pokemon specific abilities that might be scary
}

FIRST_ENERGY_WEIGHT = 1.0
ENERGY_PROGRESS_WEIGHT = 1.5

# benched pokemon with enough energy are one effect
# away from attacking instantly since energy carries over on becoming
# active -- scarier than a charging card.
READY_BENCH_ATTACKER_BONUS = 2.0

TREND_TRANSITIONS: dict[str, list[tuple[str, float, float]]] = {
    "under_energized": [
        ("attach_energy", 0.55, 1.5),
        ("retreat", 0.15, 0.5),
    ],
    "fully_energized_basic": [
        ("evolve", 0.40, 2.0),
        ("attack", 0.45, 0.0),  # already done in current threat
    ],
    "just_evolved": [
        ("attach_tool", 0.20, 0.5),
        ("attack", 0.50, 0.0),
    ],
}
PREDICTIVE_WEIGHT = 0.5


# 2. Combat threat detector
class ThreatDetector:

    def __init__(self, card_data: dict[int, CardData], attack_data: dict[int, Attack]):
        self.card_data = card_data
        self.attack_data = attack_data
        self._static_cache: dict[int, float] = {}
        self._contextual_cache: dict[tuple, float] = {}
        self._predictive_cache: dict[tuple, float] = {}
        

    # 2.1: static
    def score_static(self, card: CardData) -> float:
        if card.cardId in self._static_cache:
            return self._static_cache[card.cardId]

        score = BASE_THREAT_BY_NAME.get(card.name, STAGE_BASELINE_THREAT[stage_key(card)])
        score += ABILITY_THREAT_BONUS_BY_NAME.get(card.name, 0.0)
        # looking at attack of cards to see if scary later

        if card.megaEx:
            score += MEGA_EX_BONUS
        elif card.ex:
            score += EX_BONUS
        if card.tera:
            score += TERA_BONUS

        self._static_cache[card.cardId] = score
        return score

    # 2.2: contextual, depends on current board state 
    def _status_tuple(self, player: PlayerState) -> tuple:
        # check status of our cards
        return (player.poisoned, player.burned, player.asleep, player.paralyzed, player.confused)

    def _contextual_key(self, pokemon: Pokemon, card: CardData, is_active: bool, player: PlayerState) -> tuple:

        return (
            card.cardId,
            pokemon.hp,
            pokemon.maxHp,
            tuple(sorted(pokemon.energies)),
            len(pokemon.tools),
            is_active,
            self._status_tuple(player) if is_active else None,
            # checking for our current card hp/maxhp and our energy level
        )

    def score_contextual(self, pokemon: Pokemon, card: CardData, is_active: bool, player: PlayerState) -> float:
        key = self._contextual_key(pokemon, card, is_active, player)
        if key in self._contextual_cache:
            return self._contextual_cache[key]

        n_energy = len(pokemon.energies)
        needed = cheapest_attack_cost(card, self.attack_data)

        if n_energy == 0:
            energy_score = 0.0
        else:
            energy_score = FIRST_ENERGY_WEIGHT + ENERGY_PROGRESS_WEIGHT * (
                min(n_energy, needed) - 1
            )
            # setting up a basic energy score for cards to see if under/over energised
        hp_score = pokemon.hp / max(pokemon.maxHp, 1)

        status_penalty = 0.0
        if is_active:
            if player.asleep or player.paralyzed:
                status_penalty = -1.5
            elif player.confused:
                status_penalty = -0.75
            elif player.poisoned or player.burned:
                status_penalty = -0.25

        tool_score = 0.5 * len(pokemon.tools)

        ready_bench_bonus = 0.0
        if not is_active and n_energy >= needed:
            ready_bench_bonus = READY_BENCH_ATTACKER_BONUS

        score = energy_score + hp_score + status_penalty + tool_score + ready_bench_bonus
        self._contextual_cache[key] = score
        return score

    # 2.3: predictive, not crazy accurate
    def _classify_signal(self, pokemon: Pokemon, card: CardData) -> str:
        needed = cheapest_attack_cost(card, self.attack_data)
        if len(pokemon.energies) < needed:
            return "under_energized"
        if card.basic and not pokemon.appearThisTurn:
            return "fully_energized_basic"
        # appearThisTurn also flags a pokemon that evolved this turn
        if pokemon.appearThisTurn and not card.basic:
            return "just_evolved"
        return "steady_state"

    def score_predictive(self, pokemon: Pokemon, card: CardData) -> float:
        key = (card.cardId, len(pokemon.energies), pokemon.appearThisTurn, card.basic)
        if key in self._predictive_cache:
            return self._predictive_cache[key]

        signal = self._classify_signal(pokemon, card)
        transitions = TREND_TRANSITIONS.get(signal, [])
        score = sum(prob * delta for _, prob, delta in transitions)
        # literally just check card energy level and what cards are on bench and active
        
        self._predictive_cache[key] = score
        return score

    def score_card(self, pokemon: Pokemon, card: CardData, is_active: bool, player: PlayerState, my_weakness=None) -> float:
        return (
            self.score_static(card)
            + self.score_contextual(pokemon, card, is_active, player)
            + PREDICTIVE_WEIGHT * self.score_predictive(pokemon, card)
        )
    # threat level is static + contextual + (0.6)*predictive
   
    def score_board(self, player: PlayerState, my_weakness=None) -> dict:

        per_pokemon: dict[int, float] = {}
        bench_threats: dict[int, float] = {}
        active_threat = 0.0
        ready_bench_attacker_count = 0

        active = player.active[0] if player.active else None
        if active is not None:
            active_card = self.card_data[active.id]
            s = self.score_card(active, active_card, True, player)
            per_pokemon[active.serial] = s
            active_threat = s

        for bench_mon in player.bench:
            bench_card = self.card_data[bench_mon.id]
            s = self.score_card(bench_mon, bench_card, False, player, my_weakness)
            per_pokemon[bench_mon.serial] = s
            bench_threats[bench_mon.serial] = s
            if len(bench_mon.energies) >= cheapest_attack_cost(bench_card, self.attack_data):
                ready_bench_attacker_count += 1

        bench_threat_total = sum(bench_threats.values())
        most_threatening_bench_serial = (
            max(bench_threats, key=bench_threats.get) if bench_threats else None
        )

        return {
            "per_pokemon": per_pokemon,
            "active_threat": active_threat,
            "bench_threats": bench_threats,
            "bench_threat_total": bench_threat_total,
            "max_bench_threat": max(bench_threats.values(), default=0.0),
            "most_threatening_bench_serial": most_threatening_bench_serial,
            "ready_bench_attacker_count": ready_bench_attacker_count,
            "max_threat": max(per_pokemon.values(), default=0.0),
            "total_threat": active_threat + bench_threat_total,
        }



class EnginePhase(Enum):
    PRE_ENGINE = auto()      # key engine piece not in play yet
    PARTIAL_ENGINE = auto()  # in play, but under-energized
    FULL_ENGINE = auto()     # in play and able to attack


@dataclass
class MyBoardState:
    key_engine_piece_in_play: bool
    key_engine_piece_backup_available: bool  # 2nd copy in hand/bench
    key_engine_piece_energy: int
    ability_lock_active: bool  # "does opponent have an ability-denial stadium/effect up?"

    @property
    def phase(self) -> EnginePhase:
        if not self.key_engine_piece_in_play or self.ability_lock_active:
            return EnginePhase.PRE_ENGINE
        if self.key_engine_piece_energy >= 2:
            return EnginePhase.FULL_ENGINE
        return EnginePhase.PARTIAL_ENGINE


def build_my_board_state(
    player: PlayerState,
    card_data: dict[int, CardData],
    key_engine_piece_ids: set[int],
    ability_lock_stadium_ids: set[int],
    stadium_in_play_id: int | None,
) -> MyBoardState:

    all_mine = ([player.active[0]] if player.active and player.active[0] else []) + list(player.bench)
    in_play = [p for p in all_mine if p.id in key_engine_piece_ids]

    in_play_flag = len(in_play) > 0
    energy = max((len(p.energies) for p in in_play), default=0)

    # Backup: a second copy visible in hand 
    hand_copies = 0
    if player.hand:
        hand_copies = sum(1 for c in player.hand if c.id in key_engine_piece_ids)
    backup_available = len(in_play) > 1 or hand_copies > 0

    ability_lock = stadium_in_play_id is not None and stadium_in_play_id in ability_lock_stadium_ids

    return MyBoardState(
        key_engine_piece_in_play=in_play_flag,
        key_engine_piece_backup_available=backup_available,
        key_engine_piece_energy=energy,
        ability_lock_active=ability_lock,
    )


PLAN_THREAT_TAGS_BY_NAME: dict[str, set[str]] = {
     "Path to the Peak": {"ability_lock"},
     "Boss's Orders": {"gust_switch"},
     "Iron Hands ex": {"bench_snipe"},
}

PLAN_THREAT_TAG_BASE_VALUE: dict[str, float] = {
    "ability_lock": 6.0,
    "gust_switch": 4.0,
    "bench_snipe": 4.5,
    "energy_denial": 5.0,
    "single_target_removal": 5.5,
    "stadium_replace": 3.0,
}

PLAN_THREAT_PHASE_MULTIPLIER: dict[EnginePhase, float] = {
    EnginePhase.PRE_ENGINE: 1.5,
    EnginePhase.PARTIAL_ENGINE: 1.2,
    EnginePhase.FULL_ENGINE: 0.8,
}


class PlanThreatDetector:
    def score_tag(self, tag: str, my_board: MyBoardState) -> float:
        base = PLAN_THREAT_TAG_BASE_VALUE.get(tag, 0.0)
        multiplier = PLAN_THREAT_PHASE_MULTIPLIER[my_board.phase]

        if tag == "ability_lock":
            multiplier *= 1.3  
        elif tag == "gust_switch" and not my_board.key_engine_piece_backup_available:
            multiplier *= 1.5
        elif tag == "bench_snipe" and my_board.phase != EnginePhase.FULL_ENGINE:
            multiplier *= 1.3

        return base * multiplier

    def score_by_name(self, card_name: str, my_board: MyBoardState) -> float:
        tags = PLAN_THREAT_TAGS_BY_NAME.get(card_name, set())
        return sum(self.score_tag(tag, my_board) for tag in tags)

    def score_card(self, card_name: str, my_board: MyBoardState) -> float:
        return self.score_by_name(card_name, my_board)

    def score_board(
        self, 
        player: PlayerState, 
        card_data: dict[int, CardData], 
        my_board: MyBoardState,
        stadium_card_id: int | None = None,
    ) -> dict:
        cards = ([player.active[0]] if player.active and player.active[0] else []) + list(player.bench)
        per_card = {
            p.serial: self.score_by_name(card_data[p.id].name, my_board) for p in cards
        }
        
        stadium_threat = 0.0
        if stadium_card_id is not None and stadium_card_id in card_data:
            stadium_threat = self.score_by_name(card_data[stadium_card_id].name, my_board) 
        
        
        return {
            "per_card": per_card,
            "stadium_plan_threat": stadium_threat,
            "max_plan_threat": max(per_card.values(), default=0.0),
            "total_plan_threat": sum(per_card.values()),
            "phase": my_board.phase.name,
        }

def derive_ability_lock_stadium_ids(card_data: dict[int, CardData]) -> set[int]:
    locked_names = {
        name for name, tags in PLAN_THREAT_TAGS_BY_NAME.items() if "ability_lock" in tags
        }

def assess_threats(
    state: State,
    card_data: dict[int, CardData],
    attack_data: dict[int, Attack],
    combat_detector: ThreatDetector,
    key_engine_piece_ids: set[int],
    ability_lock_stadium_ids: set[int] = frozenset(),
) -> dict:
    """returns BOTH threat numbers for the opponent's board,
    kept separate for the NN"""
    opp_index = 1 - state.yourIndex
    opponent = state.players[opp_index]
    mine = state.players[state.yourIndex]

    stadium_id = state.stadium[0].id if state.stadium else None
    my_board = build_my_board_state(
        mine, card_data, key_engine_piece_ids, set(ability_lock_stadium_ids), stadium_id
    )

    my_active = mine.active[0] if mine.active else None
    my_weakness = card_data[my_active.id].weakness if my_active else None

    plan_detector = PlanThreatDetector()
    return {
        "combat": combat_detector.score_board(opponent),
        "plan": plan_detector.score_board(opponent, card_data, my_board),
    }
