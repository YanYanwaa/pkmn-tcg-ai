import json
import random
from main import agent
from offense import offence_agent
from defence import defence_agent
from sdk.game import battle_start, battle_finish, battle_select, visualize_data

with open("decks/hydrapple.csv") as f:
    deck = [int(line) for line in f.readlines() if line.strip()]
obs_dict, _ = battle_start(deck, deck)
obs_log = [""]
action_log = [None]
while True:
    if obs_dict["current"]["result"] >= 0:
        break

    index = obs_dict["current"]["yourIndex"]
    agent_index = defence_agent if index == 0 else offence_agent

    action = agent_index(obs_dict)
    obs_dict.pop("search_begin_input")
    obs_log.append(obs_dict)
    action_log.append(action)
    obs_dict = battle_select(action)

vis = json.loads(visualize_data())
for i in range(len(vis)):
    vis[i]["obs"] = obs_log[i]
    vis[i]["action"] = [action_log[i], action_log[i]]
with open("resources/visualisation/vis.json", "w") as file:
    json.dump(vis, file)

battle_finish()