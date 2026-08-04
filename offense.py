import os
import sys
from collections import defaultdict
from main import no_damage_dex, no_damage_counter, prize_count, pokemon_score, add_card_count, set_card_counts, get_card, main_option_proc
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

def offence_agent(obs_dict: dict) -> list[int]:
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
    damage = 60 # TODO change damage based on number of energies (eg if ogerpon in play add my active and op active energies, if hydrapple add all my active, if meganium in play double all my counts)

    my_active = my_state.active[0] if len(my_state.active) > 0 else None
    op_active = op_state.active[0] if len(op_state.active) > 0 else None

    def damage_calc(active_id: int, my_active: Pokemon, op_active: Pokemon) -> int:
        damage = 60
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
            damage = 200
        elif active_id == Dipplin:
            benched = len(my_state.bench)
            damage = 2 * (20 * benched)
        elif active_id == Meganium:
            damage = 140
        elif active_id == Applin:
            damage = 20
        elif active_id == Chikorita:
            damage = 30 
        else:
            damage = 60
        return damage

    def can_attack_now(pokemon: Pokemon) -> bool:

        energy_count = len(pokemon.energies)

        if field_counts[Meganium] >= 1:
            energy_count *= 2

        if pokemon.id == Ogerpon:
            return energy_count >= 3
        elif pokemon.id == Tapu_Bulu:
            return energy_count >= 4
        elif pokemon.id == Chikorita:
            return energy_count >= 1
        elif pokemon.id == Bayleef:
            return energy_count >= 2
        elif pokemon.id == Meganium:
            return energy_count >= 4
        elif pokemon.id == Applin:
            return energy_count >= 1
        elif pokemon.id == Dipplin:
            return energy_count >= 2
        elif pokemon.id == Hydrapple_Ex:
            return energy_count >= 2
        elif pokemon.id == Meowth_Ex:
            return energy_count >= 3
        elif pokemon.id == Fezandipiti_Ex:
            return energy_count >= 3
        elif pokemon.id == Celebi:
            return energy_count >= 1    

        return False

    def better_bench_attacker(active_id: int, my_active: Pokemon, op_active: Pokemon) -> int:
        if my_active is not None:
            return False
        if can_attack_now(my_active):
            active_damage = damage_calc(active_id, my_active, op_active)
            if my_active.id == Ogerpon or my_active.id == Hydrapple_Ex:
                active_damage += 30
        else:
            active_damage = 0

        for pokemon in my_state.bench:
            if can_attack_now(pokemon):
                bench_damage = damage_calc(pokemon.id, pokemon, op_active)
                if pokemon.id == Ogerpon or pokemon.id == Hydrapple_Ex:
                    bench_damage += 30
                if bench_damage > active_damage:
                    return True


    for card in my_state.active:
        if card == None:
            continue
        active_id = card.id
        field_counts[card.id] += 1
        if not card.appearThisTurn:
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
        if not card.appearThisTurn:
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
    
    stadium_id = 0
    for card in state.stadium:
        stadium_id = card.id

    support_count = 0
    for card in my_state.discard:
        discard_counts[card.id] += 1

    def attach_score(attach_id: int, pokemon: Pokemon, active: bool) -> int:
        score = 4000
        if attach_id == 0:
            return -1
        energy_count = len(pokemon.energies)
        if field_counts[Meganium] >= 1:
            energy_count *= 2

        if pokemon.id == Meowth_Ex:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                if better_bench_attacker(active_id, my_active, op_active):
                    score += 100
            elif active and can_switch and energy_count == 2 and not my_state.asleep and not my_state.paralyzed:
                score += 400
            elif not active:
                score += 60 
        elif pokemon.id == Fezandipiti_Ex:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                if better_bench_attacker(active_id, my_active, op_active):
                    score += 100
            elif not active:
                score += 40
        elif pokemon.id == Celebi:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                if better_bench_attacker(active_id, my_active, op_active):
                    score += 100
        elif pokemon.id == Hydrapple_Ex:
            if active and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 500
                elif not better_bench_attacker(active_id, my_active, op_active):
                    score += 350
            elif not active:
                if not can_attack_now(pokemon):
                    score += 200
            else:
                score += 90
        elif pokemon.id == Ogerpon:
            if active and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 550
                elif not can_switch and better_bench_attacker(active_id, my_active, op_active):
                    score += 400
            elif not active:
                if not can_attack_now(pokemon):
                    score += 250
                else:
                    for n in energy_count:
                        score += 10
            else:
                score += 100
        elif pokemon.id == Tapu_Bulu:
            if active and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 100 
                elif not better_bench_attacker(active_id, my_active, op_active):
                    score += 300
            elif not active:
                if not can_attack_now(pokemon):
                    score += 100
            else:
                score += 70
        elif pokemon.id == Dipplin:
            if active and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    for n in my_state.bench:
                        score += 10 
                    if not better_bench_attacker(active_id, my_active, op_active):
                        score += 150
                elif energy_count == 1:
                    score += 150
            elif not active:
                if energy_count == 1:
                    field_hydra = field_counts[Hydrapple_Ex]
                    discard_hydra = discard_counts[Hydrapple_Ex]
                    if field_hydra + discard_hydra <= 1:
                        score += 200
                elif energy_count == 0:
                    score += 150
            else:
                score += 80
        elif pokemon.id == Applin:
            if active and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    for n in my_state.bench:
                        score += 5
                    if not better_bench_attacker(active_id, my_active, op_active):
                        score += 120
                elif energy_count == 1:
                        score += 120
            elif not active:
                if energy_count == 1:
                    field_hydra = field_counts[Dipplin]
                    discard_hydra = discard_counts[Dipplin]
                    if field_hydra + discard_hydra <= 1:
                        score += 170
                    elif energy_count == 0:
                        score += 120
            else:
                score += 20
        elif pokemon.id == Chikorita:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 100
                elif not better_bench_attacker(active_id, my_active, op_active):
                    score += 20
            else: 
                score += 10
        elif pokemon.id == Bayleef:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 200
                elif not better_bench_attacker(active_id, my_active, op_active):
                    score += 50
            else:
                score += 20
        elif pokemon.id == Meganium:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 400
                elif field_counts[Meganium] == 1:
                    score += 600
            else:score += 50
        
        return score

    def hydrapple_attach_score(pokemon:Pokemon) -> int:
        score = 4900

        if pokemon.id == Ogerpon:
            energy_score = (len(pokemon.energies) * 10) + 11
        
        elif pokemon.id == Hydrapple_Ex:
            if pokemon.energies == 0:
                energy_score = 40
            elif pokemon.energies == 1:
                energy_score = 50
            else:
                energy_score = 0
        else:
            if not can_attack_now(pokemon):
                energy_score = 10

        hp_score = 500 - pokemon.hp
        
        return score + hp_score + energy_score

    def hand_score(id: int, ignore_count: bool, _in_progress: frozenset = frozenset()):
            if id in _in_progress:
                return 0  
            _in_progress = _in_progress | {id}
    

            # TODO move this to the play basic section rather than hand score
            if my_state.bench.count == 4 and field_counts[Meganium] == 0 and discard_counts[Meganium] < 2:
                return -1

            if id == Applin:
                score = 7000
                if discard_counts[Hydrapple_Ex] == 2 and discard_counts[Dipplin] == 2:
                    if hand_counts[Night_Stretcher] >= 1:
                        score += 50
                    else:
                        score = -1
                elif discard_counts[Hydrapple_Ex] == 2 and discard_counts[Dipplin] < 2:
                    score += 200
                elif discard_counts[Hydrapple_Ex] < 2 and discard_counts[Dipplin] == 2:
                    if discard_counts[Rare_Candy] == 0:
                        score += 120
                    else:
                        score = -1
                elif discard_counts[Hydrapple_Ex] < 2 and discard_counts[Dipplin] < 2:
                    score += 350
                if stadium_id == Forest_of_Vitality and hand_counts[Dipplin] >= 1 and hand_counts[Hydrapple_Ex] >= 1:
                    score += 100
            elif id == Dipplin:
                score = 7000
                if can_evolve_applin:
                    score += 300
                if discard_counts[Hydrapple_Ex] == 2:
                    score -= 80
                    if hand_counts[Night_Stretcher] >= 1:
                        score += 180
                elif hand_counts[Hydrapple_Ex] >= 1:
                    score += 200
                    if stadium_id == Forest_of_Vitality:
                        score += 100
            elif id == Hydrapple_Ex:
                score = 7000
                if can_evolve_dipplin:
                    score += 400
                elif can_evolve_applin and hand_counts[Rare_Candy] >= 1 and not no_item:
                    score += 350
                if field_counts[id] == 0:
                    score += 200
                else:
                    score += 100 
                if len(op_state.prize) == 2:
                    score -= 250
            elif id == Chikorita:
                score = 7000
                if field_counts[Meganium] < 1:
                    score += 300
                if discard_counts[Meganium] == 2 and discard_counts[Bayleef] == 2:
                    if hand_counts[Night_Stretcher] >= 1:
                        score += 100
                    else:
                        score = -1
                elif discard_counts[Meganium] < 2 and discard_counts[Bayleef] == 2:
                    if discard_counts[Rare_Candy] == 0:
                        score += 250
                elif discard_counts[Meganium] < 2 and discard_counts[Bayleef] < 2:
                    score += 200
                if stadium_id == Forest_of_Vitality and hand_counts[Bayleef] >= 1 and hand_counts[Meganium] >= 1:
                    score += 150
            elif id == Bayleef:
                score = 7000
                if can_evolve_chikorita:
                    score += 350
                if discard_counts[Meganium] == 2:
                    score -= 120
                    if hand_counts[Night_Stretcher] >= 1:
                        score += 250
                elif hand_counts[Meganium] >= 1:
                    score += 250
                    if stadium_id == Forest_of_Vitality:
                        score += 100
            elif id == Meganium:
                score = 7000
                if can_evolve_bayleef:
                    score += 450
                elif can_evolve_chikorita and hand_counts[Rare_Candy] >= 1 and not no_item:
                    score += 400
                if field_counts[id] == 0:
                    score += 250
                else:
                    score += 150
            elif id == Ogerpon:
                score = 7000
                if field_counts[id] == 0:
                    score += 400
                elif field_counts[id] >= 1:
                    if field_counts[Hydrapple_Ex] < 1:
                        return -1
                    else:
                        if field_counts[id] == 1:
                            score += 400
                        if field_counts[Meganium] >= 1:
                            score += 100
                        else:
                            score -= 110
                    if len(op_state.prize) == 2:
                        score -= 250
            elif id == Tapu_Bulu:
                score = 7000
                if field_counts[Meganium] >= 1:
                    score += 250
                if field_counts[Hydrapple_Ex] >= 1:
                    score += 100
                if len(op_state.prize) == 2:
                    score += 100
            elif id == Celebi:
                score = -1
            elif id == Meowth_Ex:
                score = 7000
                if field_counts[Hydrapple_Ex] < 1 and field_counts[Ogerpon] < 1 and field_counts[Dipplin] < 1:
                    if field_counts[Tapu_Bulu] < 1 and field_counts[Meganium] < 1:
                        score += 400
                    else:
                        score += 250
                else:
                    score = -1
            elif id == Fezandipiti_Ex:
                score = 7000
                if field_counts[Hydrapple_Ex] < 1 and field_counts[Ogerpon] < 1 and field_counts[Dipplin] < 1:
                    if field_counts[Tapu_Bulu] < 1 and field_counts[Meganium] < 1:
                        score += 300
                    else:
                        score += 150
                else:
                    score = -1
            elif id == Rare_Candy:
                score = 6000
                if can_evolve_chikorita and hand_counts[Meganium] >= 1:
                    score += 200
                    if field_counts[Meganium] < 1:
                        score += 300
                if can_evolve_applin and hand_counts[Hydrapple_Ex] >= 1:
                    score += 300
            elif id == Night_Stretcher:
                score = 6000
                if field_counts[Meganium] < 1:
                    if discard_counts[Meganium] >= 1:
                        score += 200
                    if field_counts[Bayleef] >= 1:
                        score += 200
                    if hand_counts[Rare_Candy] and can_evolve_chikorita and not no_item:
                        score += 50
                elif field_counts[Hydrapple_Ex] < 1:
                    if discard_counts[Hydrapple_Ex] >= 1:
                        score += 150
                    if field_counts[Dipplin] >= 1:
                        score += 150
                    if hand_counts[Rare_Candy] and can_evolve_applin and not no_item:
                        score += 20
                elif hand_counts[Basic_Grass_Energy] == 0:
                    if field_counts is not None:
                        for p in field_counts:
                            if field_counts[p] >= 1:
                                card_id = card_table[p].cardId
                                if card_id == Hydrapple_Ex or card_id == Ogerpon:
                                    score += 50
            elif id == Boss_Orders:
                score = 2000
                if my_active is not None and op_active is not None and op_state.bench is not None:
                    if can_attack_now:
                        if damage_calc(my_active.id,my_active,op_active) >= op_active.hp:
                            active_prize = prize_count(op_active, True)
                        for p in op_state.bench:
                            if op_state.bench[p] >= 1:
                                card

#TODO add can kill bench function comparing scores + energy (include ogerpon if given an extra energy) for bosses orders switch and before energy attachment

                         
                            
                

                

            return score
    

    ## ADD "DEF CAN_ATTACK()" TO CHECK IF BETTER BENCHED ATTACKER CAN ATTACK - MAYBE UPDATE CAN ATTACK OR CAN MAIN ATTACK HERE