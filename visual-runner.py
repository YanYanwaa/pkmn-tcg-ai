import json
import random
from main import score_agent, random_agent, read_deck_csv
from sdk.game import battle_start, battle_finish, battle_select, visualize_data

deck = read_deck_csv()
obs_dict, _ = battle_start(deck, deck)
obs_log = [""]
action_log = [None]
while True:
    if obs_dict["current"]["result"] >= 0:
        break

    index = obs_dict["current"]["yourIndex"]
    agent_index = score_agent if index == 0 else score_agent

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