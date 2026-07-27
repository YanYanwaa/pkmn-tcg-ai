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
        for pokemon in my_state.bench:
            benched += 1
        damage = 2 * (20 * benched)
    elif active_id == Meganium:
        damage = 140
    elif active_id == Applin:
        damage = 20
    elif active_id == Chikorita:
        damage = 30 
    else:
        damage = 60
    
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

    def
    