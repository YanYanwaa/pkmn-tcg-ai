import os
import sys
from collections import defaultdict
from sdk.api import AreaType, CardType, Log, LogType, Observation, SelectContext, OptionType, Card, Pokemon, State, all_card_data, to_observation_class, EnergyType, all_attack
from offence import offence_agent
from defence import defence_agent
from setup_agent import agent as setup_agent
import threat_detection as td

file_path = "deck.csv"
if not os.path.exists(file_path):
    file_path = "/kaggle_simulations/agent/" + file_path
with open(file_path, "r") as file:
    csv = file.read().split("\n")
my_deck = []
for i in range(60):
    my_deck.append(int(csv[i]))

CARD_DATA = {c.cardId: c for c in all_card_data()}
ATTACK_DATA = {a.attackId: a for a in all_attack()}
_threat_detector = td.ThreatDetector(CARD_DATA, ATTACK_DATA)

HIGH_THREAT_TRIGGER = 11.0

def agent(obs_dict: dict) -> list[int]:
    obs = to_observation_class(obs_dict)
    if obs.select == None:
        return my_deck

    state = obs.current
    my_index = state.yourIndex
    op_state = state.players[1 - my_index]

    board_threat = _threat_detector.score_board(op_state)
    high_threat = board_threat["active_threat"] >= HIGH_THREAT_TRIGGER

    if state.turn <= 6:
        #print("Using Setup")
        return setup_agent(obs_dict)
    elif high_threat:
        #print("Using Defence")
        return defence_agent(obs_dict)
    else:
        #print("Using Offence")
        return offence_agent(obs_dict)


#SETUP TODO change prime catcher score
#SETUP TODO used ultra ball before attaching energy to ogerpon (missed ko breakpoint by discarding energy before attaching)
