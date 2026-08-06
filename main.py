import os
import sys
from collections import defaultdict

from sdk.api import AreaType, CardType, Log, LogType, Observation, SelectContext, OptionType, Card, Pokemon, State, all_card_data, to_observation_class, EnergyType

file_path = "decks/hydrapple.csv"
if not os.path.exists(file_path):
    file_path = "/kaggle_simulations/agent/" + file_path
with open(file_path, "r") as file:
    csv = file.read().split("\n")
my_deck = []
for i in range(60):
    my_deck.append(int(csv[i]))

all_card = all_card_data()

card_table = {c.cardId:c for c in all_card}

Ogerpon = 96 # 4
Chikorita = 917 # 2
Bayleef = 918 or 709 # 2
Meganium = 710 # 2
Applin = 149 # 2
Hydrapple_Ex = 150 # 2
Dipplin = 93 # 2
Meowth_Ex = 1071 # 2
Tapu_Bulu = 920 # 1
Fezandipiti_Ex = 140 # 1
Celebi = 655 # 1
Lillie_Determination = 1227 # 4
Boss_Orders = 1182 # 2
Ciphermaniac_Codebreaking = 1188 # 1
Briar = 1201 # 1
Lana_Aid = 1184 # 1
Dawn = 1231 # 1
Bug_Catching_Set = 1094 # 4
Ultra_Ball = 1121 # 3
Poke_Pad = 1152 # 1
Night_Stretcher = 1097 # 1
Prime_Catcher = 1088 # 1
Forest_of_Vitality = 1261 # 4
Basic_Grass_Energy = 1 # 14
Rare_Candy = 1079 # 1

UNNECESSARY = -10000000

class AttackPlan:
    attack: int = 0
    counter: list[int] = []

can_attack = False
can_switch = False
can_main_attack = False
can_attach_energy = False
use_support = 0
can_bench_attack = False
pre_turn_log = []
current_turn_log = []
prize: list[int] = []
card_counts: defaultdict[int,int] = defaultdict(int)
serial_set: set[int] = set()
plan_a = AttackPlan()
plan_b = AttackPlan()

def no_damage_dex(id: int) -> bool:
    # Drednaw, Milotic ex, Sylveon, Crustle
    return id == 158 or id == 207 or id == 330 or id == 345

def no_damage_counter(pokemon: Pokemon) -> bool:
    # Poltchageist, Empoleon ex, Skeledirge, Milotic ex, Misty's Magikarp, Antique Cover Fossil
    if pokemon.id == 28 or pokemon.id == 199 or pokemon.id == 203 or pokemon.id == 207 or pokemon.id == 362 or pokemon.id == 1136:
        return True
    for card in pokemon.energyCards:
        # Mist Energy, Rock Fighting Energy
        if card.id == 11 or card.id == 20:
            return True
    return False

def prize_count(pokemon: Pokemon, is_attack_damage: bool) -> int:
    data = card_table[pokemon.id]
    count = 3 if data.megaEx else 2 if data.ex else 1
    if is_attack_damage:
        for card in pokemon.energyCards:
            if card.id == 12:  # Legacy Energy
                count -= 1
        for card in pokemon.tools:
            if card.id == 1172 and "Lillie" in data.name:  # Lillie’s Pearl
                count -= 1
    return max(0, count)

def pokemon_score(pokemon: Pokemon, is_attack_damage: bool) -> int:
    data = card_table[pokemon.id]
    score = prize_count(pokemon, is_attack_damage) * 1000
    score += len(pokemon.energies) * 150
    score += len(pokemon.tools) * 100
    if data.stage2:
        score += 250
    elif data.stage1:
        score += 130
    
    id = pokemon.id
    # Noctowl, Fan Rotom, Archaludon ex, Meowth ex
    if id == 173 or id == 174 or id == 190 or id == 1071:
        score -= 200
    if id == 112 and len(pokemon.energies) >= 1:  # Munkidori
        score += 300
    score += pokemon.hp
    return score


def add_card_count(card: Card | Pokemon | None, my_index: int):
    if card == None:
        return
    if isinstance(card, Pokemon) or card.playerIndex == my_index:
        if card.serial not in serial_set:
            card_counts[card.id] -= 1
            serial_set.add(card.serial)
    if isinstance(card, Pokemon):
        for c in card.energyCards:
            add_card_count(c, my_index)
        for c in card.tools:
            add_card_count(c, my_index)
        for c in card.preEvolution:
            add_card_count(c, my_index)

def set_card_counts(obs: Observation, my_index: int):
    card_counts.clear()
    serial_set.clear()
    for id in my_deck:
        card_counts[id] += 1
    
    state = obs.current
    my_state = state.players[my_index]
    for card in my_state.hand:
        add_card_count(card, my_index)
    for card in my_state.discard:
        add_card_count(card, my_index)
    for card in my_state.bench:
        add_card_count(card, my_index)
    for card in my_state.active:
        add_card_count(card, my_index)
    for card in state.stadium:
        add_card_count(card, my_index)
    if state.looking != None:
        for card in state.looking:
            add_card_count(card, my_index)
    add_card_count(obs.select.effect, my_index)

    
def get_card(obs: Observation, area: AreaType, index: int, player_index: int) -> Pokemon | Card | None:
    ps = obs.current.players[player_index]
    match area:
        case AreaType.DECK:
            return obs.select.deck[index]
        case AreaType.HAND:
            return ps.hand[index]
        case AreaType.DISCARD:
            return ps.discard[index]
        case AreaType.ACTIVE:
            return ps.active[index]
        case AreaType.BENCH:
            return ps.bench[index]
        case AreaType.PRIZE:
            return ps.prize[index]
        case AreaType.STADIUM:
            return obs.current.stadium[index]
        case AreaType.LOOKING:
            return obs.current.looking[index]
        case _:
            return None
        
def main_option_proc(obs: Observation, damage: int, bench_attacker: bool):
    state = obs.current
    select = obs.select
    my_index = state.yourIndex
    my_state = state.players[my_index]
    op_state = state.players[1 - my_index]

    global can_switch
    global can_attack
    global can_main_attack
    global can_energy_attach

    can_switch = False
    can_attack = False
    can_main_attack = False
    can_energy_attach = False
    for o in select.option:
        if o.type == OptionType.RETREAT:
            can_switch = True
        elif o.type == OptionType.ATTACK:
            can_attack = True
            if o.attackId == 120 or o.attackId == 195 or o.attackId == 115 or o.attackId == 1326:  # Ogerpon, Hydrapple, Dipplin, Tapu Bulu attacks
                can_main_attack = True
    
    plan_a.attack = -1
    plan_b.attack = -1
    if not can_main_attack and not (bench_attacker and can_switch):
        return
    
    cards = [op_state.active[0]]
    for pokemon in op_state.bench:
        cards.append(pokemon)
    counter_indices = []
    ci = []
    ci.append(0)
    remain_damage = 60
    while ci:
        index = ci[-1]
        hp = cards[index].hp
        if remain_damage >= hp:
            counter_indices.append(ci.copy())
            if index < len(cards) - 1:
                remain_damage -= hp
                ci.append(index + 1)
                continue
        if index == len(cards) - 1:
            ci.pop()
            if ci:
                remain_damage += cards[ci[-1]].hp
        if ci:
            ci[-1] += 1
    counter_indices.append([])

    remain_prize = len(my_state.prize)
    plan_score = 0
    for i, pokemon in enumerate(cards):
        base_prize_count = 0
        base_score = pokemon_score(pokemon, True)
        active_damage = 0 if no_damage_dex(pokemon.id) else damage
        if pokemon.hp <= active_damage:
            base_prize_count += prize_count(pokemon, True)
        else:
            base_score *= active_damage / pokemon.hp
        ci = []
        max_score = base_score
        if remain_prize <= base_prize_count:
            max_score = 50000
        else:
            for indices in counter_indices:
                if i in indices:
                    continue
                prize = base_prize_count
                score = base_score
                for index in indices:
                    prize += prize_count(cards[index], False)
                    score += pokemon_score(cards[index], False)
                if remain_prize <= prize:
                    score = 50000
                else:
                    if prize >= 2:
                        if remain_prize <= 4:
                            score -= 1200
                    elif prize == 1:
                        score -= 300
                    else:
                        score += 1200
                if max_score < score:
                    max_score = score
                    ci = indices
        if plan_score < max_score:
            plan_score = max_score
            plan_a.attack = i
            plan_a.counter = ci
        if i == 0:
            plan_b.attack = plan_a.attack
            plan_b.counter = plan_a.counter
            
def greedy_select_cards(select, obs, hand_counts, hand_score):
    """Pick up to select.maxCount cards for TO_HAND/TO_BENCH-style contexts,
    re-scoring after each pick so duplicates and diminishing returns are
    handled correctly regardless of option order."""
    remaining = list(enumerate(select.option))
    picked = []
    while remaining and len(picked) < select.maxCount:
        best_i = None
        best_score = None
        best_pos = None
        for pos, (i, o) in enumerate(remaining):
            card = get_card(obs, o.area, o.index, o.playerIndex)
            if card is None:
                continue
            s = hand_score(card.id, False)
            if best_score is None or s > best_score:
                best_score, best_i, best_pos = s, i, pos
        if best_i is None:
            break
        if best_score < 0 and len(picked) >= select.minCount:
            break
        picked.append(best_i)
        o = select.option[best_i]
        card = get_card(obs, o.area, o.index, o.playerIndex)
        hand_counts[card.id] += 1
        remaining.pop(best_pos)
    return picked

def agent(obs_dict: dict) -> list[int]:
    """Main Agent Function.

    Each element in the returned list must be >= 0 and < len(obs.select.option).
    The list length must be between obs.select.minCount and obs.select.maxCount (inclusive), with no duplicate elements.
    
    Returns:
        list[int]: A list of option index.
    """
    obs = to_observation_class(obs_dict)
    if obs.select == None:
        # In the initial selection, the obs.select is None, and it is necessary to return the deck.
        # The deck is a list of 60 card IDs.
        # The deck must comply with the Pokémon Trading Card Game rules.
        return my_deck

    global pre_turn_log
    global current_turn_log

    state = obs.current
    select = obs.select
    context = select.context
    my_index = state.yourIndex
    my_state = state.players[my_index]
    op_state = state.players[1 - my_index]
            
    if state.turn == 0:
        prize.clear()
        pre_turn_log.clear()
        current_turn_log.clear()
    else:
        for log in obs.logs:
            current_turn_log.append(log)
            if log.type == LogType.TURN_END:
                pre_turn_log = current_turn_log
                current_turn_log = []

    pre_ko = False
    no_item = False
    for log in pre_turn_log:
        if log.type == LogType.ATTACK:
            if log.attackId == 323:  # Itchy Pollen
                no_item = True
        elif log.type == LogType.MOVE_CARD:
            if (log.playerIndex == my_index
                and (log.fromArea == AreaType.BENCH or log.fromArea == AreaType.ACTIVE)
                and log.toArea == AreaType.DISCARD):
                pre_ko = True

    if select.deck != None:
        set_card_counts(obs, my_index)
        for card in select.deck:
            card_counts[card.id] -= 1
        prize.clear()
        for id in card_counts:
            for _ in range(card_counts[id]):
                prize.append(id)
                
    set_card_counts(obs, my_index)
    for id in prize:
        card_counts[id] -= 1
    deck_counts = card_counts

    prize_diff = len(my_state.prize) - len(op_state.prize)
    
    global bench_attacker

    # Number of cards per card ID on the Bench and in the Active Spot
    field_counts = defaultdict(int)
    # Number of cards per card ID in hand
    hand_counts = defaultdict(int)
    # Number of cards per card ID in discard pile
    discard_counts = defaultdict(int)
    
    active_id = 0
    bench_attacker = False
    can_evolve_applin = False
    evolve_applin_count = 0
    can_evolve_chikorita = False
    evolve_chikorita_count = 0
    can_evolve_dipplin = False
    can_evolve_bayleef = False
    hydrapple_ability = False
    ogerpon_ability = False
    damage = 60 # TODO change damage based on number of energies (eg if ogerpon in play add my active and op active energies, if hydrapple add all my active, if meganium in play double all my counts)

    my_active = my_state.active[0] if len(my_state.active) > 0 else None
    op_active = op_state.active[0] if len(op_state.active) > 0 else None

    active_id = my_active.id if my_active is not None else 0

    stadium_id = 0
    for card in state.stadium:
        stadium_id = card.id
    
    can_main_attack = any(o.type == OptionType.ATTACK and o.attackId in (120,195,115,1326) for o in select.option)

    if active_id == Hydrapple_Ex and can_main_attack:
        energy_count = 0
        if my_active is not None:
            energy_count += sum(1 for e in my_active.energies if e == EnergyType.GRASS)
        for pokemon in my_state.bench:
            energy_count += sum(1 for e in pokemon.energies if e == EnergyType.GRASS)
        damage = 30 + (30 * energy_count)
    elif active_id == Ogerpon and can_main_attack:
        energy_count = 0
        if my_active is not None:
            energy_count += len(my_active.energies)
        if op_active is not None:
            energy_count += len(op_active.energies)
        damage = 30 + (30 * energy_count)
    elif active_id == Tapu_Bulu:
        damage = 220
    elif active_id == Dipplin:
        benched = 0
        for pokemon in my_state.bench:
            benched += 1
        damage = 2 * (20 * benched)
    elif active_id == Meganium:
        damage = 140
    elif active_id == Applin:
        damage = 20
    elif active_id == Chikorita:
        damage = 30 
    elif active_id == Fezandipiti_Ex:
        damage = 100
    else:
        damage = 60
    
    for card in my_state.active:
        if card == None:
            continue
        active_id = card.id
        field_counts[card.id] += 1
        if not card.appearThisTurn or stadium_id == Forest_of_Vitality:
            if card.id == Applin:
                can_evolve_applin = True
                evolve_applin_count += 1
            elif card.id == Dipplin:
                can_evolve_dipplin = True
            elif card.id == Chikorita:
                can_evolve_chikorita = True
                evolve_chikorita_count += 1
            elif card.id == Bayleef:
                can_evolve_bayleef = True
    for card in my_state.bench:
        field_counts[card.id] += 1
        if not card.appearThisTurn or stadium_id == Forest_of_Vitality:
            if card.id == Applin:
                can_evolve_applin = True
                evolve_applin_count += 1
            elif card.id == Dipplin:
                can_evolve_dipplin = True
            elif card.id == Chikorita:
                can_evolve_chikorita = True
                evolve_chikorita_count += 1
            elif card.id == Bayleef:
                can_evolve_bayleef = True
        if card.id == Hydrapple_Ex and len(card.energies) >= 2:
            bench_attacker = True
        elif card.id == Ogerpon and len(card.energies) >= 3:
            bench_attacker = True
        elif card.id == Tapu_Bulu and len(card.energies) >= 4:
            bench_attacker = True
    main_pokemon_count = field_counts[Applin] + field_counts[Dipplin] + field_counts[Hydrapple_Ex] + field_counts[Ogerpon] + field_counts[Chikorita] + field_counts[Bayleef] + field_counts[Meganium]
    

    support_count = 0
    for card in my_state.discard:
        discard_counts[card.id] += 1

    def do_switch():

        if can_attack and op_active is not None and damage >= op_active.hp:
            return False
        elif (not can_main_attack) and bench_attacker:
            return True
        elif active_id == Meganium and field_counts[Meganium] <= 1:
            return True
        elif my_active is not None:
            if active_id == Hydrapple_Ex or active_id == Ogerpon or active_id == Meowth_Ex or active_id == Fezandipiti_Ex:
                if my_active.hp <= 100:
                    if not bench_attacker and op_active and damage >= (op_active.hp / 2):  
                        return False
                    else:
                        return True
            else:
                if active_id != Applin and my_active.hp <= 40:
                    return True
        else:
            return False
    
    def attach_score(attach_id: int, pokemon: Pokemon, active: bool) -> int:
        if attach_id == 0:
            return -1
        energy_count = len(pokemon.energies)
        if field_counts[Meganium] >= 1:
            energy_count *= 2
        if card_table[attach_id].cardType == CardType.TOOL:
            score = 60000
            if active:
                score += 1000
            return score
        
        if pokemon.id == Meowth_Ex or pokemon.id == Fezandipiti_Ex or pokemon.id == Tapu_Bulu or pokemon.id == Celebi:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                if bench_attacker:
                    return 22000
                else:
                    return 18000
            else:
                return -1
        if active and can_main_attack and pokemon.id != Ogerpon:
            return -1
        score = 20000

        if active and pokemon.id == Hydrapple_Ex and energy_count < 2:
            return 50000
        
        if active and pokemon.id == Meganium and energy_count < 2:
            return 45000
        
        if energy_count >= 2:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                score += 200
            elif pokemon.id == Ogerpon:
                score += 500
            else:
                return -1
        elif energy_count == 1:
            if pokemon.id == Hydrapple_Ex:
                score += 400
            elif pokemon.id == Ogerpon:
                if active:
                    score += 350
                else:
                    score += 300
            elif pokemon.id == Applin:
                score += 80
            elif pokemon.id == Dipplin:
                score += 90
            else:
                score -= 200
            if active:
                score += 200
        else:
            if active:
                if bench_attacker:
                    score += 400
                else:
                    if pokemon.id == Ogerpon:
                        score += 300
                    elif pokemon.id == Hydrapple_Ex:
                        score += 250
                    elif pokemon.id == Tapu_Bulu:
                        score += 150
                    elif pokemon.id == Applin:
                        score += 100
                    elif pokemon.id == Dipplin:
                        if hand_counts[Hydrapple_Ex] >= 1:
                            score += 220
                        else:
                            score += 200
                    else:
                        score += 55
            else:
                if pokemon.id == Hydrapple_Ex:
                    score += 200
                elif pokemon.id == Applin:
                    if hand_counts[Hydrapple_Ex] >= 1 and hand_counts[Rare_Candy] >= 1:
                        score += 130
                    elif hand_counts[Hydrapple_Ex] >= 1 and hand_counts[Dipplin] >= 1:
                        score += 105
                    else:
                        score += 100
                elif pokemon.id == Dipplin:
                    if hand_counts[Hydrapple_Ex] >= 1:
                        score += 120
                    else:
                        score += 110
                elif pokemon.id == Ogerpon:
                    if active:
                        score += 300
                    else:
                        score += 250
                else:
                    score += 50
                if bench_attacker and pokemon.id != Ogerpon:
                    score -= 200
            
        
        if (pokemon.id == Tapu_Bulu or pokemon.id == Meganium) and can_attack:
            score -= 1000
        
        if do_switch() and my_active is not None and my_active.energies == 0:
            score += 500
        return score
    

    def hydrapple_attach_score(pokemon: Pokemon) -> int:
        if pokemon is None:
            return -1
        
        if pokemon.id == Ogerpon:
            energy_score = len(pokemon.energies) * 10000
            hp_score = 1000 - pokemon.hp

            return 50000 + energy_score + hp_score
        elif pokemon.id == Hydrapple_Ex:
            if pokemon.energies < 2:
                return 50000
            else:
                return 30000 + len(pokemon.energies) * 5000
        else:
            return 0
    
    def hand_score(id: int, ignore_count: bool, _in_progress: frozenset = frozenset()):
        if id in _in_progress:
            return 0  
        _in_progress = _in_progress | {id}

        score = 0
        if id == Applin:
            if main_pokemon_count >= 3:
                score = 1000
            else:
                score = 15000
        elif id == Dipplin:
            if can_evolve_applin:
                score = 17000
            else:
                score = 2000
        elif id == Hydrapple_Ex:
            if can_evolve_applin and hand_counts[Rare_Candy] >= 1 and not no_item:
                score = 41500
            elif can_evolve_dipplin:
                if field_counts[id] == 0:
                    score = 30500
                elif field_counts[id] == 1:
                    score = 9000
                else:
                    score = 50
            else:
                if field_counts[id] >= 2:
                    score = 50
                else:
                    score = 2000
        elif id == Chikorita:
            if field_counts[Meganium] < 1:
                score = 20500
            else: 
                score = 5000
        elif id == Bayleef:
            if (field_counts[Meganium] < 1) and can_evolve_chikorita:
                score = 22000
            else:
                score = 6000
        elif id == Meganium:
            if field_counts[id] < 1:
                if can_evolve_chikorita and hand_counts[Rare_Candy] >= 1 and not no_item:
                    score = 41500
                elif can_evolve_bayleef:
                    score = 38000
                else:
                    score = 30000
            else:
                score = 40
        elif id == Fezandipiti_Ex:
            if pre_ko:
                score = 40000
            elif prize_diff <= -2:
                score = 10
            elif len(op_state.prize) == 1:
                score = UNNECESSARY
        elif id == Meowth_Ex:
            if support_count > hand_counts[Boss_Orders] or stadium_id == 1256:
                score = 10
            elif state.supporterPlayed:
                score = 30
            else:
                score = 30000
        elif id == Celebi:
            if field_counts[Meganium] < 1 or stadium_id != Forest_of_Vitality:
                if hand_counts[Forest_of_Vitality] == 0:
                    score = 46000
                elif hand_counts[Meganium] == 0 and hand_counts[Bayleef] == 0:
                    score = 45000
                elif hand_counts[Meganium] == 0 or hand_counts[Bayleef] == 0:
                    score = 40500
            elif field_counts[Hydrapple_Ex] < 1 or field_counts[Ogerpon] < 1:
                if hand_counts[Hydrapple_Ex] == 0 or hand_counts[Dipplin] == 0 or hand_counts[Ogerpon] == 0:
                    score = 44000
            else:
                score = 1000
        elif id == Ogerpon:
            if field_counts[id] == 0:
                score = 39000
            elif field_counts[id] >= 1:
                score = 20000
        elif id == Tapu_Bulu:
            if field_counts[Meganium] >= 1:
                score = 21000
            else:
                score = 400
        elif id == Rare_Candy:
            if can_evolve_applin and hand_counts[Hydrapple_Ex] >= 1:
                score = 41000
            elif can_evolve_chikorita and hand_counts[Meganium] >= 1:
                score = 42000
        elif id == Prime_Catcher:
            score = 0
            op_active = op_state.active[0] if len(op_state.active) > 0 else None

            # Rule 1: if my current active can already KO the opponent's active this turn, don't use it.
            active_ko = (
                op_active is not None
                and can_main_attack
                and not no_damage_dex(op_active.id)
                and op_active.hp <= damage
            )

            # Rule 2: if nothing on my bench can attack, don't use it (nothing to retreat into afterward).
            if not active_ko and bench_attacker:
                # Rule 3a: I need a benched Pokemon with enough energy already on it to retreat for free
                # (so I can swap my attacker back in after Prime Catcher forces a switch).
                can_retreat_free = any(
                    len(p.energies) >= card_table[p.id].retreatCost
                    for p in my_state.bench
                )
                # Rule 3b: my current attack needs to be able to KO something on the opponent's bench
                # (since it can't KO their active, per Rule 1).
                can_snipe_bench = any(
                    not no_damage_dex(p.id) and p.hp <= damage
                    for p in op_state.bench
                )
                if can_retreat_free and can_snipe_bench:
                    score = 44000
        elif id == Briar:
            if (
                len(op_state.prize) == 2
                and active_id == Ogerpon
                and len(op_state.active) > 0
                and op_state.active[0] is not None
                and prize_count(op_state.active[0], True) < 2
            ):
                score = 50000
            else:
                score = 60
        elif id == Poke_Pad:
            score = max(
                hand_score(Meganium, ignore_count, _in_progress),
                hand_score(Celebi, ignore_count, _in_progress),
                hand_score(Bayleef, ignore_count, _in_progress),
                hand_score(Chikorita, ignore_count, _in_progress),
                hand_score(Applin, ignore_count, _in_progress),
                hand_score(Dipplin, ignore_count, _in_progress),
                hand_score(Tapu_Bulu, ignore_count, _in_progress),
            )
        elif id == Boss_Orders:
            if plan_a.attack > 0:
                score = 60000
        elif id == Lillie_Determination:
            if not ignore_count or support_count == 0:
                score = 45000
        elif id == Night_Stretcher:
            for i in discard_counts:
                if discard_counts[i] >= 1:
                    card_type = card_table[i].cardType
                    if card_type == CardType.POKEMON or card_type == CardType.BASIC_ENERGY:
                        score = max(score, hand_score(i, ignore_count, _in_progress))
        elif id == Forest_of_Vitality:
            if stadium_id != 0 and stadium_id != Forest_of_Vitality:
                score = 7000
        elif id == Ciphermaniac_Codebreaking:
            searchable_ids = [i for i in deck_counts if deck_counts[i] > 0]
            first_id = max(searchable_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
            first_score = hand_score(first_id, ignore_count, _in_progress) if first_id is not None else 0
            remaining_ids = [i for i in searchable_ids if i != first_id]
            second_id = max(remaining_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
            second_score = hand_score(second_id, ignore_count, _in_progress) if second_id is not None else 0
            score = first_score + second_score
        elif id == Ultra_Ball:
            if main_pokemon_count <= 3 or field_counts[Applin] >= 1 or field_counts[Chikorita] >= 1:
                score = 70
            else:
                score = 5
        elif id == Bug_Catching_Set:
            searchable_ids = [i for i in deck_counts if deck_counts[i] > 0]
            first_id = max(searchable_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
            first_score = hand_score(first_id, ignore_count, _in_progress) if first_id is not None else 0
            remaining_ids = [i for i in searchable_ids if i != first_id]
            second_id = max(remaining_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
            second_score = hand_score(second_id, ignore_count, _in_progress) if second_id is not None else 0
            score = first_score + second_score
        elif id == Lana_Aid:
            searchable_ids_disc = [i for i in discard_counts if discard_counts[i] > 0 and (i == Meganium or i == Bayleef or i == Chikorita or i == Tapu_Bulu or i == Celebi or i == Applin or i == Dipplin or i == Basic_Grass_Energy)]
            first_id = max(searchable_ids_disc, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
            first_score = hand_score(first_id, ignore_count, _in_progress) if first_id is not None else 0
            remaining_ids = [i for i in searchable_ids_disc if i != first_id]
            second_id = max(remaining_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
            second_score = hand_score(second_id, ignore_count, _in_progress) if second_id is not None else 0
            remaining_ids = [i for i in searchable_ids_disc if (i != first_id and i != second_id)]
            third_id = max(remaining_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
            third_score = hand_score(third_id, ignore_count, _in_progress) if third_id is not None else 0
            score = first_score + second_score + third_score
        elif id == Dawn:
            if field_counts[Applin] == 0 and hand_counts[Applin] == 0:
                if field_counts[Dipplin] == 0 and hand_counts[Dipplin] == 0:
                    if field_counts[Hydrapple_Ex] == 0 and hand_counts[Hydrapple_Ex] == 0:
                        score = 72000
                    elif hand_counts[Hydrapple_Ex] >= 1 and field_counts[Hydrapple_Ex] == 0:
                        score = 52000
                elif hand_counts[Dipplin] >= 1 and field_counts[Dipplin] == 0:
                    score = 18000
            elif hand_counts[Applin] >= 1 and field_counts[Applin] == 0:
                score = 20000
            elif field_counts[Chikorita] == 0 and hand_counts[Chikorita] == 0: # If no Chikorita
                if field_counts[Bayleef] == 0 and hand_counts[Bayleef] == 0: # If no Bayleef AND no Chikorita
                    if field_counts[Meganium] == 0 and hand_counts[Meganium] == 0: # If no Meganium AND no Bayleef AND no Chikorita
                        score = 70000
                    elif hand_counts[Meganium] >= 1 and field_counts[Meganium] == 0:
                        score = 50000
                elif hand_counts[Bayleef] >= 1 and field_counts[Bayleef] == 0:
                    score = 12000
            elif hand_counts[Chikorita] >= 1 and field_counts[Chikorita] == 0:
                score = 15000
            elif field_counts[Ogerpon] == 0 and hand_counts[Ogerpon] == 0:
                score = 30000
            else:
                score = 100
        elif id == Basic_Grass_Energy:
            if can_main_attack and (len(op_state.prize) <= 2 or (bench_attacker and len(op_state.prize) <= 4)):
                score = UNNECESSARY
            else:
                max_score = -10000
                for pokemon in my_state.active:
                    if pokemon == None:
                        continue
                    max_score = max(max_score, attach_score(id, pokemon, True))
                for pokemon in my_state.bench:
                    max_score = max(max_score, attach_score(id, pokemon, False))
                score = max_score - 5000
                if can_main_attack or bench_attacker:
                    score /= 10
        if not ignore_count and hand_counts[id] > 0:
            if id == Dipplin and hand_counts[id] < evolve_applin_count:
                score -= 10
            elif id == Applin:
                score -= 100
            elif id == Bayleef and hand_counts[id] < evolve_chikorita_count:
                score -= 10
            elif id == Chikorita:
                score -= 100
            else:
                score -= 10000
        return score

    global use_support
    if context == SelectContext.MAIN:
        main_option_proc(obs, damage)

        use_support = 0
        if not state.supporterPlayed:
            support_score = 0
            for o in select.option:
                if o.type == OptionType.PLAY:
                    card = get_card(obs,AreaType.HAND, o.index, state.yourIndex)
                    if card_table[card.id].cardType == CardType.SUPPORTER:
                        score = hand_score(card.id, True)
                        if support_score < score:
                            support_score = score
                            use_support = card.id
    hand_scores = []
    negative_hand_count = 0
    for card in my_state.hand:
        score = hand_score(card.id, False)
        hand_scores.append(score)
        if score < 0:
            negative_hand_count += 1
        hand_counts[card.id] += 1
        if card_table[card.id].cardType == CardType.SUPPORTER and card.id != Boss_Orders:
            support_count += 1 
    no_draw = (my_state.deckCount <= 8)
    # do_switch = (not can_main_attack and bench_attacker) or (active_id == Meganium and field_counts[Meganium] <= 1)
    
        
    effect_card_id = 0 if select.effect == None else select.effect.id
    hydrapple_ability = (effect_card_id == Hydrapple_Ex)
    ogerpon_ability = (effect_card_id == Ogerpon)
    context_card_id = 0 if select.contextCard == None else select.contextCard.id

    scores = []
    for o in select.option:
        score = 0
        if o.type == OptionType.NUMBER:
            score = o.number
        elif o.type == OptionType.YES:
            if context == SelectContext.IS_FIRST:
                score =- 1
            else:
                score = 1
        elif o.type == OptionType.CARD:
            card = get_card(obs, o.area, o.index, o.playerIndex)
            if card != None:
                energy_count = 0
                hp = 0
                if isinstance(card, Pokemon):
                    energy_count = len(card.energies)
                    hp = card.hp
                if (context == SelectContext.SWITCH or context == SelectContext.TO_ACTIVE or context == SelectContext.SETUP_ACTIVE_POKEMON):
                    if o.playerIndex == my_index:
                        if card.id == Celebi:
                            if context == SelectContext.SETUP_ACTIVE_POKEMON:
                                score += 100000
                            elif stadium_id != Forest_of_Vitality and ((field_counts[Meganium] == 0 and hand_counts[Meganium] == 0) or (field_counts[Hydrapple_Ex] and hand_counts[Hydrapple_Ex])) and not bench_attacker:
                                score += 30000
                        elif card.id == Applin:
                            score += 5000
                        elif card.id == Dipplin:
                            if energy_count >= 1:
                                score += 20000
                            else:
                                score -= 5000
                        elif card.id == Hydrapple_Ex:
                            hydrapple_energy_count = 0
                            ogerpon_energy_count = 0
                            if my_active is not None:
                                ogerpon_energy_count += len(my_active.energies)
                            if op_active is not None:
                                ogerpon_energy_count += len(op_active.energies)
                            
                            if my_active is not None:
                                hydrapple_energy_count += sum(1 for e in my_active.energies if e == EnergyType.GRASS)
                            for pokemon in my_state.bench:
                                hydrapple_energy_count += sum(1 for e in pokemon.energies if e == EnergyType.GRASS)
                                
                            if energy_count == 1 and hand_counts[Basic_Grass_Energy] >= 1:
                                score += 51000
                            elif energy_count == 0 and hand_counts[Basic_Grass_Energy] >= 2 and hydrapple_ability:
                                score += 52000
                            elif energy_count >= 2:
                                score += 53000
                            if hydrapple_energy_count > ogerpon_energy_count:
                                score += 10000
                            
                            if 60 < hp < 200:
                                score -= 10000
                            elif hp < 60:
                                score -= 30000

                        elif card.id == Ogerpon: # TODO update all scores based on if meganium in play or able to be played and num of energy gained
                            hydrapple_energy_count = 0
                            ogerpon_energy_count = 0
                            if my_active is not None:
                                ogerpon_energy_count += len(my_active.energies)
                            if op_active is not None:
                                ogerpon_energy_count += len(op_active.energies)
                            
                            if my_active is not None:
                                hydrapple_energy_count += sum(1 for e in my_active.energies if e == EnergyType.GRASS)
                            for pokemon in my_state.bench:
                                hydrapple_energy_count += sum(1 for e in pokemon.energies if e == EnergyType.GRASS)

                            if energy_count >= 3:
                                score += 53000
                            elif energy_count >= 2:
                                score += 40000
                            elif energy_count == 1:
                                score += 29500
                            else:
                                if hp >= 200:
                                    score += 6000
                                elif 100 < hp < 200:
                                    score += 3500
                                else:
                                    score -= 100
                            
                            if ogerpon_energy_count > hydrapple_energy_count:
                                score += 10000
                            
                            if 60 < hp < 200:
                                score -= 10000
                            elif hp < 60:
                                score -= 30000
                            
                        elif card.id == Meowth_Ex:
                            score -= 2000
                        elif card.id == Fezandipiti_Ex:
                            score -= 1000
                        elif card.id == Chikorita:
                            score -= 6000
                        elif card.id == Bayleef: # TODO change diff bayleef scores based on op bench and active hp
                            score -= 4000
                        elif card.id == Meganium: # TODO change later based on if backup meganium
                            if field_counts[Meganium] >= 2:
                                if energy_count >= 2:
                                    score += 5500
                            score -= 5000
                        elif card.id == Tapu_Bulu:
                            if field_counts[Meganium] >= 1:
                                if energy_count >= 2: # TODO check if meganium passive means actual energy count is doubled 
                                    score += 40000
                                elif energy_count == 1:
                                    score += 31000
                            else:
                                if hp <= 70:
                                    score -= 7000
                                else:
                                    score -= 10000
                    else:
                        if plan_a.attack == o.index + 1:
                            score += 100000
                    score += energy_count * 1000
                    score += hp
                elif context == SelectContext.SETUP_BENCH_POKEMON:
                    if my_index == state.firstPlayer or (card.id == Chikorita and field_counts[Chikorita] >= 1):
                        score = -1
                elif context == SelectContext.TO_BENCH or context == SelectContext.TO_HAND:
                    score = hand_score(card.id, False)
                    hand_counts[card.id] += 1
                elif context == SelectContext.DISCARD:
                    hand_counts[card.id] -= 1
                    if card_table[card.id].cardType == CardType.SUPPORTER:
                        support_count -= 1
                    score = -hand_score(card.id, False)
                elif context == SelectContext.DAMAGE_COUNTER or context == SelectContext.DAMAGE_COUNTER_ANY:
                    if hp > 0:
                        score = 100000 - 10 * hp + pokemon_score(card, False)
                        if context == SelectContext.DAMAGE_COUNTER: # Only Fezandipiti can do this which deals 100 damage
                            if hp >= 200:
                                score += 20000 + hp * 20
                                if o.area == AreaType.ACTIVE:
                                    score += 10000
                            elif 100 <= hp < 200:
                                score += 10000 + hp * 20
                            elif hp < 100:
                                score += -10000 + hp * 20
                            if card.id == 133 or card.id == 351 or card.id == 132: # TODO identify any threatening abilities and add them here
                                score += 30000
                        else:
                            index = o.index + 1
                            if index in plan_b.counter:
                                score += 100000
                            else: # Might not be necessary (not sure if fezandipiti counts as damage counters)
                                remain_damage = select.remainDamageCounter * 10
                                if 210 <= hp <= 200 + remain_damage:
                                    score += 30000
                                elif 20 <= hp <= 60 + remain_damage:
                                    score += 10000
                                elif hp == 10:
                                    score -= 100000
                            if no_damage_counter(card):
                                score = -1
                elif context == SelectContext.ATTACH_FROM:
                    if select.contextCard is None:
                        score = -1
                    else:
                        score = attach_score(select.contextCard.id, card, o.area == AreaType.ACTIVE)

                    if card.id == Hydrapple_Ex and len(card.energies) < 2:
                        score += 300
                    elif card.id == Ogerpon:
                        score += 350
                elif context == SelectContext.ATTACH_TO:
                    if select.contextCard is None:
                        score = -1
                    else:
                        pokemon = get_card(obs, o.area, o.index, my_index)
                        if hydrapple_ability:
                            score = hydrapple_attach_score(pokemon)
                        else:
                            if card.id == Basic_Grass_Energy:
                                score += 500


                    
                    
                    
        elif o.type == OptionType.ENERGY_CARD or o.type == OptionType.ENERGY:
            if o.playerIndex != state.yourIndex:
                if o.area == AreaType.BENCH:
                    score = 20
                else:
                    score = 10
                card = get_card(obs, o.area, o.index, o.playerIndex)
                if card_table[card.id].cardType == CardType.SPECIAL_ENERGY:
                    score += 1
        elif o.type == OptionType.PLAY:
            card = get_card(obs,AreaType.HAND, o.index, my_index)
            card_score = hand_scores[o.index]
            if card.id == Applin:
                score = 51000
            elif card.id == Chikorita:
                if field_counts[Meganium]:
                    if active_id == Meganium:
                        score = 30000
                    else:
                        score -1
                else:
                    score = 52000
            elif card.id == Celebi:
                if stadium_id != Forest_of_Vitality and ((field_counts[Meganium] == 0 and hand_counts[Meganium] == 0) or (field_counts[Hydrapple_Ex] and hand_counts[Hydrapple_Ex])):
                    score = 53000
                elif my_state.bench == 0:
                    score = 5000
                else:
                    score = -1
            elif card.id == Tapu_Bulu:
                if field_counts[Chikorita] >= 1 or field_counts[Bayleef] >= 1 or field_counts[Meganium] >= 1:
                    score = 50000
                else:
                    score = -1
            elif card.id == Ogerpon:
                if active_id == Hydrapple_Ex:
                    score = 50500
                if field_counts[Ogerpon] >= 2:
                    score = -1
                elif field_counts[Ogerpon] == 1:
                    score = 45000
                else:
                    score = 50000
            elif card.id == Fezandipiti_Ex:
                if card_score > 0:
                    score = 53000
                else:
                    score = -1
            elif card.id == Meowth_Ex:
                if state.supporterPlayed or stadium_id == 1256: # Team Rockets Watchtower (basic pokemon have no abilities)
                    score = -1
                elif support_count == 0:
                    score = 50000
                elif support_count == hand_counts[Boss_Orders] and not plan_a.attack <= 0:
                    score = 50000
                else:
                    score = -1
            elif card.id == Rare_Candy:
                    score = 75000
            elif card.id == Night_Stretcher:
                if card_score >= 18000:
                    score = 42000
                else:
                    score = -1
            elif card.id == Boss_Orders:
                if card.id == use_support:
                    score = 35000
                elif op_state.bench is not None and op_active is not None:
                    for pokemon in op_state.bench:
                        if len(pokemon.energies) < len(op_active.energies):
                            score = 25000
                else:
                    score = -1
            elif card.id == Lillie_Determination:
                if card.id == use_support:
                    score = 14000
                elif my_state.active == 1 and hand_counts[Applin] == 0 and hand_counts[Chikorita] == 0 and hand_counts[Celebi] == 0 and hand_counts[Tapu_Bulu] == 0 and hand_counts[Ogerpon] == 0 and hand_counts[Meowth_Ex] == 0 and hand_counts[Fezandipiti_Ex] == 0:
                    score = 10000
                else:
                    score = -1
            elif card.id == Ultra_Ball:
                if negative_hand_count >= 2:
                    score = 44000
                elif my_state.active == 1 and hand_counts[Applin] == 0 and hand_counts[Chikorita] == 0 and hand_counts[Celebi] == 0 and hand_counts[Tapu_Bulu] == 0 and hand_counts[Ogerpon] == 0 and hand_counts[Meowth_Ex] == 0 and hand_counts[Fezandipiti_Ex] == 0:
                    score = 42000
                else:
                    score = -1
            elif card.id == Poke_Pad:
                if deck_counts[Applin] + deck_counts[Dipplin] + deck_counts[Chikorita] + deck_counts[Bayleef] + deck_counts[Meganium] > 0:
                    score = 45000
                elif my_state.active == 1 and hand_counts[Applin] == 0 and hand_counts[Chikorita] == 0 and hand_counts[Celebi] == 0 and hand_counts[Tapu_Bulu] == 0 and hand_counts[Ogerpon] == 0 and hand_counts[Meowth_Ex] == 0 and hand_counts[Fezandipiti_Ex] == 0:
                    score = 44500
                else:
                    score = -1
            elif card.id == Forest_of_Vitality:
                if stadium_id != Forest_of_Vitality or state.turn == 1:
                    score = 80000
                else: score = -1
            elif card.id == Briar:
                if active_id == Ogerpon and prize_count(op_state.active[0], True) < 2 and op_state.active[0].hp < damage:
                    score = 65000
                else:
                    score = -1
            elif card.id == Bug_Catching_Set:
                if card_score >= 16000:
                    score = 45000
                else:
                    score = -1
            elif card.id == Ciphermaniac_Codebreaking:
                if card.id == use_support:
                    score = 20000
                else:
                    score = -1
            elif card.id == Lana_Aid:
                if card.id == use_support:
                    score = 35000
                else:
                    score = -1
            elif card.id == Dawn:
                if card.id == use_support:
                    score = score = 46000
            elif card.id == Prime_Catcher:
                if card_score >= 44000:
                    score = 36000
                else:
                    score = -1
            if card == CardType.POKEMON:
                if my_state.bench is None:
                    score = 60000
        elif o.type == OptionType.ATTACH:
            card = get_card(obs, o.area, o.index, my_index)
            pokemon = get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
            score = attach_score(card.id, pokemon, o.inPlayArea == AreaType.ACTIVE)
            score += 100000
        elif o.type == OptionType.EVOLVE:
            pokemon = get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
            score += len(pokemon.energies)
            if pokemon.id == Applin:
                score += 30000
            elif field_counts[Ogerpon] + field_counts[Hydrapple_Ex] >= 3 and pokemon.id == Dipplin:
                score = -1
            elif pokemon.id == Bayleef:
                score += 110000
            else:
                score += 70000
        elif o.type == OptionType.ABILITY:
            is_active = (o.area == AreaType.ACTIVE)
            card = get_card(obs, o.area, o.index, my_index)
            if no_draw:
                score = -1
            elif card.id == 1267:
                score = 1
            elif card.id == Ogerpon:
                if is_active:
                    # Always use Teal Dance while the active Ogerpon can still benefit.
                    if hand_counts[Basic_Grass_Energy] >= 1:
                        score = 80000
                    else:
                        score = -1
                else:
                    # Benched Ogerpon follows the normal conservation rules.
                    if hand_counts[Basic_Grass_Energy] <= 1 and not can_main_attack:
                        score = -1
                    else:
                        score = 40000
        
            elif card.id == Hydrapple_Ex:
                    hydrapple_ability = True
                    score = 90000
            else:
                score = 40000
        elif o.type == OptionType.RETREAT:
            if do_switch():
                score = 10000
            else: score = -1
        elif o.type == OptionType.ATTACK:
            score = o.attackId

        scores.append(score)
    output = []
    if context in (SelectContext.TO_HAND, SelectContext.TO_BENCH) and select.deck is not None:
        # Deck-search style selection (Ultra Ball, Poke Pad, Bug Catching Set,
        # Ciphermaniac's Codebreaking, Lana's Aid, Night Stretcher, Dawn, Celebi's attack...)
        output = greedy_select_cards(select, obs, hand_counts, hand_score)
    elif len(scores) >= 1:
        sorted_scores = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        for i in range(select.maxCount):
            if(sorted_scores[i][1] >= 0 or select.minCount > i or (context != SelectContext.TO_BENCH and context != SelectContext.SETUP_BENCH_POKEMON)):
                output.append(sorted_scores[i][0])
    
    return output

# TODO function to calculate highest damage (add op + own energy for ogerpon, add all my energy for hydrapple, default 220 for tapu bulu), update retreat in comparison with damage + prize cards + energy

# TODO noticed issues -> applin +2G active, ogerpon+2G bench, Meganium+0G bench -> attach energy to meganium + attack with applin, should switch applin with ogerpon + attach energy and attack with ogerpon

# TODO account for crustle/sylv & account for fire decks(all weak to fire)
                
# FIXED dont appear to be using ogerpon ability to attach energy

# FIXED when hydrapple is active with 2 energy and ogerpons are on bench with no energy, attaches energy to hydrapple rather than ogerpon                        

# TODO celebi attack handling to search for necessary useful pokemon

# fixed? prime catcher used incorrectly (switches out strong my pokemon)

# fixed? energy attached to chikorita instead of applin even if applin active (only 2 pokemon on field)

# fixed? attached energy to benched ogerpon over active ogerpon even though both have no energy and same hp

# TODO meganium not retreating if only meganium on field to preserve ability

# TODO trainer cards not choosing specific cards that can improve game state

# TODO ogerpon w/3G(6G) switched with meganium w/0G by boss order, energy in hand could be used to switch meganium, attack and ko but is given to ogerpon instead

# TODO ogerpon W/1G switched to active over ogerpon W/2G 

# TODO apply switching choices

# TODO i think ciphermaniac + pokepad + bug catcher always super high score (add multiple cards scores) - need to simulate selecting card to give accurate usage score

# TODO if dipplin in active W/0G and ogerpon on bench W/2G and hydrapple in hand, attach energy to dipplin then use hydrapple ability to attach energy the attack

#TODO if applin and dipplin both W/1G, energy attaches to applin rather than dipplin

#TODO lots of people using lucario/lunatone/solrock/hariyama deck - research + counter

#TODO if only 1 pokemon on board but celebi in hand after turn 2, doesnt play celebi, should play celebi and attack if no useful pokemon (applin, chikorita, ogerpon)

#TODO applin active and applin on bench both W/0G ogerpon on bench W/2G, applin active dies, chooses applin to switch next over ogerpon