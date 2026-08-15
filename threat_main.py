import os
import random
from sdk.api import (Observation, to_observation_class, OptionType, SelectContext,
    AreaType, EnergyType, all_card_data, all_attack)
import threat_detection as td

BASE_SCORES = {
    OptionType.ATTACK: 70,
    OptionType.ABILITY: 80,
    OptionType.EVOLVE:90,
    OptionType.ATTACH:100,
    OptionType.PLAY: 110,
    OptionType.END: -50,
    OptionType.RETREAT: -60
}

CARD_DATA = {c.cardId: c for c in all_card_data()}
ATTACK_DATA = {a.attackId: a for a in all_attack()}
EVOLVES_INTO = {}
for card in CARD_DATA.values():
    if card.evolvesFrom:
        EVOLVES_INTO[card.evolvesFrom] = card

_detector = td.ThreatDetector(CARD_DATA, ATTACK_DATA)

THREAT_LOG_PATH = "threat_log.txt"
with open(THREAT_LOG_PATH, "w") as _f:
    _f.write("")
# i say this again after but this is literally just so i can 
# read the threat levels in a txt file 

KEY_ENGINE_PIECE_IDS = td.find_card_ids_by_name(CARD_DATA, "meganium")

ABILITY_LOCK_STADIUM_IDS: set[int] = set()

HIGH_THREAT_RETREAT_TRIGGER = 14.0

def read_deck_csv() -> list[int]:
    """Read deck.csv.
    
    Returns:
        list[int]: A list of card IDs in the deck.
    """
    file_path = "decks/hydrapple.csv"
    if not os.path.exists(file_path):
        file_path = "/kaggle_simulations/agent/" + file_path
    with open(file_path, "r") as file:
        csv = file.read().split("\n")
    deck = []
    for i in range(60):
        deck.append(int(csv[i]))
    return deck

def score_option(option, obs: Observation, threats: dict):

    # initialises my card and opp card and game state
    state = obs.current
    me = state.players[state.yourIndex]
    opp = state.players[1 - state.yourIndex]
    context = obs.select.context
    # theres only 2 players (player turn 1 player turn 0)
    # if ur player 0, 1 - 0 = 1 so opponent is player 1
    # works vice versa
    me_active = me.active[0] if me.active else None
    opp_active = opp.active[0] if opp.active else None
    # get the pokemon instances (not the card data) aka hp energy
    me_card = CARD_DATA.get(me_active.id) if me_active else None 

    opp_card = CARD_DATA.get(opp_active.id) if opp_active else None
  
    
    if option.type == OptionType.END:
        return -100
    
    if option.type == OptionType.RETREAT:
        opp_active_threat = threats["combat"]["active_threat"]
        if opp_active_threat >= HIGH_THREAT_RETREAT_TRIGGER:
            return -20
        return -110
    # attack scores
    if option.type == OptionType.ATTACK:

        attack = ATTACK_DATA.get(option.attackId) # initialises attack options by pulling the ids for the attacks

        if not attack:
            return 50
        
        if opp_active:
            dmg = attack.damage 

            # check if opp active pokemon is weak to attack
            if (opp_card and opp_card.weakness) and (me_card and (me_card.energyType == opp_card.weakness)):
                    dmg *= 2 

            # if attack kos opp active pokemon, increase score based on ko and ex/mega ex prize value
            if opp_active.hp <= dmg:
                prize_bonus = 200 * td.prize_value(CARD_DATA[opp_active.id])
                return 300 + prize_bonus 
            return 50 + dmg 
        return 60 
    
    if option.type == OptionType.EVOLVE:
        if option.inPlayArea == AreaType.ACTIVE:

            me_evolved_card = EVOLVES_INTO.get(me_card.name)
            
            # if evolving means cant attack, dont evolve
            energy_count = len(me_active.energies)
            evo_attacks = [ATTACK_DATA[atk_id] for atk_id in me_evolved_card.attacks if atk_id in ATTACK_DATA]
            if not evo_attacks:
                return 80
            
            cheapest_atk_cost = min(len(a.energies) for a in evo_attacks)
            energy_needed = max(0, cheapest_atk_cost - energy_count)

            if energy_needed >= 2:
                return 20
            
            # if can ko opp after evolving, evolve
            opp_active_hp = opp_active.hp
            highest_atk_dmg = max(a.damage for a in evo_attacks)
            if opp_active_hp <= highest_atk_dmg:
                return 100
            
            # if cant KO opp and will be kod after evolving, dont evolve
            opp_active_attacks = [ATTACK_DATA[atk_id] for atk_id in opp_card.attacks if atk_id in ATTACK_DATA]
            opp_highest_atk_dmg = max(a.damage for a in opp_active_attacks)

            me_dmg_taken = me_active.maxHp - me_active.hp
            me_evolved_hp = me_evolved_card.hp
            hp_after_evo = me_evolved_hp - me_dmg_taken
            if hp_after_evo <= opp_highest_atk_dmg:
                return 20
            
            # otherwise just evolve
            else:
                return 70


        else: # evolve benched pokemon anyway
            return 70

    return BASE_SCORES.get(option.type, 0)


# agent using default scored moves
def score_agent(obs_dict):

    obs: Observation = to_observation_class(obs_dict)

    if obs.select is None:
        return read_deck_csv()

    threats = td.assess_threats(
        obs.current, CARD_DATA, ATTACK_DATA, _detector,
        KEY_ENGINE_PIECE_IDS, ABILITY_LOCK_STADIUM_IDS,
    )

    log_line = f"[turn {obs.current.turn}] enemy deck threat: {threats['combat']['total_threat']:.2f}"
    print(log_line)
    with open(THREAT_LOG_PATH, "a") as f:
        f.write(log_line + "\n")
    #literally just so i can see the current threat levels at all times 
    #comment out once ur done

    options = obs.select.option
    max_count = obs.select.maxCount

    scored_options = [
        (score_option(opt, obs, threats), i)
        for i, opt in enumerate(options)
    ]

    scored_options.sort(reverse=True, key=lambda x: x[0])

    chosen = [i for _, i in scored_options[:max_count]]

    return chosen

# Full random agent
def random_agent(obs_dict):

    obs: Observation = to_observation_class(obs_dict)

    if obs.select is None:
        return read_deck_csv()
    
    return random.sample(
    list(range(len(obs_dict["select"]["option"]))),
    obs_dict["select"]["maxCount"]
)